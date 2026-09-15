# -*- coding: utf-8 -*-
"""智能择优纯逻辑单测：零第三方依赖，可直接运行。

    python tools/test_smartcut.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scene_splitter_gui import core  # noqa: E402
from scene_splitter_gui.smartcut import (  # noqa: E402
    FrameSignal,
    adaptive_floor,
    dp_merge_bounds,
    place_smart_cuts,
)

FAILED: list[str] = []


def check(condition: bool, label: str) -> None:
    print(("  [通过] " if condition else "  [失败] ") + label)
    if not condition:
        FAILED.append(label)


class Silent(core.Reporter):
    def log(self, message, level="info"):
        pass


def make_signal(peaks: dict[int, float], total: int = 400) -> FrameSignal:
    sig = FrameSignal()
    for fno in range(total):
        sig.add(fno, peaks.get(fno, 0.0))
    return sig


def scenes_of(*bounds: int, fps: float = 25.0) -> list:
    return [core.SceneSpan(i + 1, bounds[i], bounds[i + 1], fps)
            for i in range(len(bounds) - 1)]


print("1) dp_merge_bounds（合并 DP）")
# T3 形状：贪心会保留弱切点 50，DP 应保留强切点 60
kept = dp_merge_bounds([0, 50, 60, 120], [0.0, 1.0, 100.0, 0.0], 50)
check(kept == [0, 60, 120], f"保留强切点：{kept}")
kept = dp_merge_bounds([0, 30, 60, 100], [0.0, 10.0, 1.0, 0.0], 50)
check(kept == [0, 100], f"无连接可保留时并成一段：{kept}")
kept = dp_merge_bounds([0, 20, 30], [0.0, 5.0, 0.0], 50)
check(kept == [0, 30], f"总长不足下限时并成一段：{kept}")
kept = dp_merge_bounds([0, 60, 130], [0.0, 7.0, 0.0], 50)
check(kept == [0, 60, 130], f"本来就够长的全部保留：{kept}")
kept = dp_merge_bounds([0, 100], [0.0, 0.0], 50)
check(kept == [0, 100], "两边界原样返回")
kept = dp_merge_bounds([0, 10, 20], [0.0, 9.0, 0.0], 0)
check(kept == [0, 10, 20], "min<=0 原样返回")

print("2) place_smart_cuts（切分 DP）")
cuts = place_smart_cuts(0, 100, 2, [(50, 0.0), (70, 9.0)], 10, 90)
check(cuts == [0, 70, 100], f"吸附到强峰：{cuts}")
cuts = place_smart_cuts(0, 100, 2, [(5, 99.0)], 40, 60)
check(cuts is None, "峰会违反限制时返回 None（回退均匀）")
cuts = place_smart_cuts(0, 100, 1, [], 10, 90)
check(cuts == [0, 100], "pieces<=1 不切")
cuts = place_smart_cuts(0, 100, 2, [(50, 0.0), (95, 99.0)], 10, 60)
check(cuts == [0, 50, 100], f"强峰超上限时宁可均匀：{cuts}")
cuts = place_smart_cuts(0, 120, 3, [(40, 0.0), (80, 0.0), (38, 5.0), (82, 6.0)], 20, 60)
check(cuts == [0, 38, 82, 120], f"多刀各自吸附：{cuts}")

print("3) FrameSignal / peaks / floor")
sig = make_signal({30: 50.0, 70: 40.0}, 100)
peaks = sig.peaks_in(0, 100, 3.0)
check(peaks == [(30, 50.0), (70, 40.0)], f"双峰：{peaks}")
sig2 = make_signal({20: 10.0, 21: 10.0, 22: 10.0}, 100)
peaks2 = sig2.peaks_in(0, 100, 3.0)
check(peaks2 == [(21, 10.0)], f"平台取中心：{peaks2}")
check(sig.score_at(30) == 50.0, "命中采样")
check(sig.score_at(31) == 50.0, "容差内找最近")
check(sig.score_at(50) == 0.0, "容差外为 0")
floor = adaptive_floor([0.0] * 90 + [50.0])
check(3.0 <= floor < 50.0, f"自适应门限分离峰与底噪：{floor:.2f}")
check(adaptive_floor([0.0] * 50) == 3.0, "纯静内容门限=绝对下限")

print("4) _even_cuts 与老逻辑一致")
check(core._even_cuts(0, 100, 4, 0) == [0, 25, 50, 75, 100], "均匀四等分")
check(core._even_cuts(0, 100, 3, 40) == [0, 33, 100], "碎片规则：尾段并入")
check(core._even_cuts(0, 100, 3, 0) == [0, 33, 67, 100], "无限制三等分")

print("5) apply_limits 老逻辑 parity（smart 关闭/无信号）")
rep = Silent()
scenes = scenes_of(0, 100, 200, 300, 400, 500)
out, used = core.apply_limits(scenes, 25.0, core.TaskParams(max_scene_sec=2.0), rep, 500)
check([s.frame_count for s in out] == [50] * 10 and used is False,
      f"最长2秒→10×50帧且 smart 未启用（{used}）")
out, used = core.apply_limits(scenes_of(0, 10, 20, 100), 25.0,
                              core.TaskParams(min_scene_frames=50), rep, 100)
check([(s.start_frame, s.end_frame) for s in out] == [(0, 100)] and used is False,
      "贪心合并短段（老行为）")
out, used = core.apply_limits(scenes, 25.0, core.TaskParams(max_scene_sec=2.0, smart_cut=True),
                              rep, 500, 0, 0, None)
check(used is False, "无信号时即使开着开关也回退老逻辑")

print("6) apply_limits 智能路径（合成信号）")
sig3 = make_signal({50: 1.0, 60: 100.0}, 200)
out, used = core.apply_limits(scenes_of(0, 50, 60, 120), 25.0,
                              core.TaskParams(min_scene_frames=50, smart_cut=True),
                              rep, 120, 0, 0, sig3)
got = [(s.start_frame, s.end_frame) for s in out]
check(got == [(0, 60), (60, 120)] and used is True, f"合并保留强切点 60：{got}")
sig4 = make_signal({70: 50.0}, 300)
# 注：max=130（切 2 段，可行切点 ∈ [70,130]），70 恰在可行域边缘
out, used = core.apply_limits(scenes_of(0, 200), 25.0,
                              core.TaskParams(max_scene_frames=130, smart_cut=True),
                              rep, 200, 0, 0, sig4)
got = [(s.start_frame, s.end_frame) for s in out]
check(got == [(0, 70), (70, 200)] and used is True, f"切分吸附到峰值 70：{got}")
sig5 = make_signal({}, 300)
out, used = core.apply_limits(scenes_of(0, 200), 25.0,
                              core.TaskParams(max_scene_frames=100, smart_cut=True),
                              rep, 200, 0, 0, sig5)
got = [(s.start_frame, s.end_frame) for s in out]
check(got == [(0, 100), (100, 200)] and used is False, f"纯静态回退均匀：{got}")

print("\n===== 结果 =====")
print("全部通过 ✅" if not FAILED else f"失败 {len(FAILED)} 项 ❌：{FAILED}")
sys.exit(1 if FAILED else 0)
