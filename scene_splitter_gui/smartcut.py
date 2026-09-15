# -*- coding: utf-8 -*-
"""智能择优切分：优先在画面变化（置信度）最高的位置下刀。

背景：检测器给出的切点经过「最短/最长」限制整理时，老逻辑是——
  · 过短合并：从左到右盲合并，不管中间的切点多明显一律删掉；
  · 过长切分：按固定间隔均匀切开，切点落在画面哪里纯看运气。
于是就会出现「在没切镜的地方分割、段里却包含更明显的切镜」。

本模块的做法：
  · 检测阶段顺手记录逐帧画面变化信号（见 core.detect，零额外 IO）；
  · 合并改动态规划：在满足最短限制的前提下，保留置信度总和最高的一组切点；
  · 切分改峰值吸附：刀数不变，每刀吸附到附近变化最大的峰值，同时硬保证仍满足
    最长/最短限制；纯静态内容无峰可吸时回退均匀切分。

本模块不依赖 GUI、不读视频文件（信号由调用方在检测时灌入），所有核心函数都是
纯函数，可独立单测。
"""

from __future__ import annotations

import bisect


class FrameSignal:
    """逐帧画面变化信号：frame_no（绝对帧号）-> 变化强度（越大表示越可能是切镜）。

    由 core.detect 在检测回调里逐帧灌入；分析阶段只做查询。
    """

    def __init__(self) -> None:
        self._frames: list[int] = []
        self._scores: list[float] = []
        self._sorted = True

    def __len__(self) -> int:
        return len(self._frames)

    def __bool__(self) -> bool:
        return bool(self._frames)

    def add(self, frame_no: int, score: float) -> None:
        try:
            fno = int(frame_no)
        except (TypeError, ValueError):
            return
        if fno < 0:
            return
        # 检测是顺序灌入的，正常情况下一直有序；乱序到达时才标记重排
        if self._frames and fno < self._frames[-1]:
            self._sorted = False
        self._frames.append(fno)
        self._scores.append(max(0.0, float(score)))

    def _ensure_index(self) -> None:
        if not self._sorted:
            pairs = sorted(zip(self._frames, self._scores))
            self._frames = [p[0] for p in pairs]
            self._scores = [p[1] for p in pairs]
            self._sorted = True

    def score_at(self, frame_no: int, tolerance: int = 2) -> float:
        """取某帧的变化强度；在容差内找最近采样，找不到返回 0。"""
        if not self._frames:
            return 0.0
        self._ensure_index()
        pos = bisect.bisect_left(self._frames, int(frame_no))
        best = 0.0
        for neighbor in (pos - 1, pos):
            if 0 <= neighbor < len(self._frames):
                if abs(self._frames[neighbor] - frame_no) <= tolerance:
                    best = max(best, self._scores[neighbor])
        return best

    def window_scores(self, start: int, end: int) -> list[tuple[int, float]]:
        """取 [start, end) 区间内的全部采样（按帧号升序）。"""
        if not self._frames or end <= start:
            return []
        self._ensure_index()
        left = bisect.bisect_left(self._frames, int(start))
        right = bisect.bisect_left(self._frames, int(end))
        return list(zip(self._frames[left:right], self._scores[left:right]))

    def peaks_in(self, start: int, end: int, floor: float = 0.0,
                 min_separation: int = 1) -> list[tuple[int, float]]:
        """找区间内的局部极大值峰（平台取中心），只返回高于 floor 的。"""
        samples = self.window_scores(start, end)
        if len(samples) < 3:
            return [(fno, score) for fno, score in samples if score > floor]
        peaks: list[tuple[int, float]] = []
        i = 1
        while i < len(samples) - 1:
            fno, score = samples[i]
            if score <= floor:
                i += 1
                continue
            prev_score = samples[i - 1][1]
            next_score = samples[i + 1][1]
            # 平台处理：连续相等的一串只取中心点
            if score == next_score:
                j = i
                while j + 1 < len(samples) - 1 and samples[j + 1][1] == score:
                    j += 1
                # 平台右端仍需是下降沿才算峰
                if j + 1 < len(samples) and score > samples[j + 1][1] and score >= prev_score:
                    center = (i + j) // 2
                    peaks.append((samples[center][0], score))
                i = j + 1
                continue
            if score >= prev_score and score > next_score:
                peaks.append((fno, score))
            i += 1
        if min_separation > 1 and len(peaks) > 1:
            peaks = _thin_peaks(peaks, min_separation)
        return peaks


def _thin_peaks(peaks: list[tuple[int, float]], min_separation: int) -> list[tuple[int, float]]:
    """峰太多时按间隔稀释：每次保留剩余中最高的，删掉其邻域内的。"""
    remaining = sorted(peaks, key=lambda p: -p[1])
    kept: list[tuple[int, float]] = []
    while remaining:
        best = remaining.pop(0)
        kept.append(best)
        remaining = [p for p in remaining if abs(p[0] - best[0]) >= min_separation]
    return sorted(kept)


def adaptive_floor(scores: list[float], absolute: float = 3.0, k: float = 2.5) -> float:
    """自适应噪声门限：max(绝对下限, 中位数 + k*标准差）。

    纯静态内容标准差~0，门限≈绝对下限，压缩噪点过不去→回退均匀切分；
    有明显切镜时峰远高于门限→被选中。
    """
    if not scores:
        return absolute
    ordered = sorted(scores)
    median = ordered[len(ordered) // 2]
    mean = sum(scores) / len(scores)
    var = sum((s - mean) ** 2 for s in scores) / len(scores)
    return max(absolute, median + k * var ** 0.5)


def dp_merge_bounds(bounds: list[int], scores: list[float], min_len: int) -> list[int]:
    """合并优化：在每段都不短于 min_len 的前提下，保留置信度和最高的一组切点。

    bounds 为升序边界帧（含首尾），scores 与之一一对应（首尾分强制保留，
    调用方传 inf 或很大的数即可）。返回保留下来的边界子集。O(n)。
    """
    n = len(bounds)
    if n <= 2 or min_len <= 0:
        return list(bounds)
    if bounds[-1] - bounds[0] < min_len:
        return [bounds[0], bounds[-1]]

    # dp[i] = 以 bounds[i] 为右端时的最优保留分；best_j[i] 用于回溯。
    # bounds 有序，“与 i 相距 ≥ min_len 的 j”只会随 i 增大而增多，
    # 用单调指针维护前缀最优，O(n)。
    neg = float("-inf")
    dp = [neg] * n
    prev: list[int] = [-1] * n
    dp[0] = 0.0
    k = 0     # 已纳入比较的最大下标（可行前缀的右端，单调前移）
    best = 0  # dp[0..k] 的最优下标（与推进指针分开维护）
    for i in range(1, n):
        limit = bounds[i] - min_len
        while k + 1 < i and bounds[k + 1] <= limit:
            k += 1
            if dp[k] > dp[best]:
                best = k
        # 可行集恰为 [0..k]；为空（首段就不够长）时 best=0 不可行，跳过
        if bounds[best] <= limit and dp[best] > neg / 2:
            dp[i] = dp[best] + (scores[i] if i < len(scores) else 0.0)
            prev[i] = best
        # 否则 dp[i] 保持 -inf（到 i 为止无法满足最短限制，i 不可保留）

    # 尾边界必须保留；若不可达（理论上不会，因为总长已检查），回退只留首尾
    if dp[n - 1] <= neg / 2:
        return [bounds[0], bounds[-1]]
    kept: list[int] = []
    i = n - 1
    while i >= 0:
        kept.append(bounds[i])
        i = prev[i]
        if i == 0:
            kept.append(bounds[0])
            break
    return sorted(set(kept))


def place_smart_cuts(start: int, end: int, pieces: int,
                     candidates: list[tuple[int, float]],
                     min_len: int, max_len: int) -> list[int] | None:
    """切分优化：把 (start, end) 切成 pieces 段，每刀落在候选峰上。

    candidates 为 (帧号, 置信分)，只需包含开区间内的点；返回包括首尾在内的
    pieces+1 个边界。硬约束：每段长度严格落在 [min_len, max_len] 内；
    无可行解返回 None，调用方回退均匀切分。
    """
    if pieces <= 1 or end <= start:
        return [start, end]
    cuts_needed = pieces - 1
    lo = max(1, min_len)
    hi = max(lo, max_len)

    points = sorted({p for p, _ in candidates if start < p < end})
    if len(points) < cuts_needed:
        return None
    score_of = {p: s for p, s in candidates}
    # 位置序列：start, 候选..., end；切点只能落在内部候选上
    seq = [start] + points + [end]
    m = len(seq)
    # dp[c][i] = 第 c 刀落在 seq[i]（内部点）时的最高分；-inf 不可达
    neg = float("-inf")
    dp = [[neg] * m for _ in range(cuts_needed + 1)]
    prev: list[list[int]] = [[-1] * m for _ in range(cuts_needed + 1)]
    for i in range(1, m - 1):
        if lo <= seq[i] - start <= hi:
            dp[1][i] = score_of.get(seq[i], 0.0)
            prev[1][i] = 0
    for c in range(2, cuts_needed + 1):
        for i in range(1, m - 1):
            best, best_j = neg, -1
            # j 越大段越短；seq 有序，从 i-1 往前扫，超 max 即停
            for j in range(i - 1, 0, -1):
                length = seq[i] - seq[j]
                if length > hi:
                    break
                if length < lo:
                    continue
                if dp[c - 1][j] > best:
                    best, best_j = dp[c - 1][j], j
            if best_j >= 0:
                dp[c][i] = best + score_of.get(seq[i], 0.0)
                prev[c][i] = best_j
    # 收尾：最后一刀 j 必须能一步走到 end
    best, best_j = neg, -1
    for j in range(m - 2, 0, -1):
        if lo <= end - seq[j] <= hi and dp[cuts_needed][j] > best:
            best, best_j = dp[cuts_needed][j], j
    if best_j < 0:
        return None
    cuts_idx = [best_j]
    c = cuts_needed
    while c > 1:
        cuts_idx.append(prev[c][cuts_idx[-1]])
        c -= 1
    return sorted({start, end} | {seq[i] for i in cuts_idx})
