# -*- coding: utf-8 -*-
"""智能择优视频 E2E：合成色块视频，全链路验证 detect → apply_limits。

需要 opencv + scenedetect（项目正式依赖）：

    python tools/test_smartcut_video.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402
import numpy  # noqa: E402

from scene_splitter_gui import core  # noqa: E402

FAILED: list[str] = []
FPS = 25.0


def check(condition: bool, label: str) -> None:
    print(("  [通过] " if condition else "  [失败] ") + label)
    if not condition:
        FAILED.append(label)


class Silent(core.Reporter):
    def log(self, message, level="info"):
        pass


def make_video(path: str, segments: list[tuple[int, tuple[int, int, int]]]) -> None:
    """segments: [(帧数, BGR颜色), ...]，纯色块拼接。"""
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (64, 48))
    for count, color in segments:
        frame = numpy.full((48, 64, 3), color, dtype=numpy.uint8)
        for _ in range(count):
            writer.write(frame)
    writer.release()


def detect(path: str, **kw):
    dp = kw.pop("detector_params", {}) or {}
    params = core.TaskParams(video_path=path, detector="content",
                             detector_params={"threshold": 27.0, "min_scene_len": 0},
                             **kw)
    params.detector_params.update(dp)
    info, scenes, _video, signal = core.detect(params, Silent())
    cuts = [s.start_frame for s in scenes[1:]]
    bounds = [(s.start_frame, s.end_frame) for s in scenes]
    return params, info, bounds, cuts, signal


def apply(bounds, fps, params, signal, total, smart: bool):
    import dataclasses
    params = dataclasses.replace(params, smart_cut=smart)
    scenes = [core.SceneSpan(i + 1, a, b, fps) for i, (a, b) in enumerate(bounds)]
    out, used = core.apply_limits(scenes, fps, params, Silent(), total, 0, 0, signal)
    return [(s.start_frame, s.end_frame) for s in out], used


tmp = tempfile.mkdtemp(prefix="smartcut_e2e_")

print("V1) 合并保留强切点（弱 50 / 强 60，下限 50 帧）")
v1 = os.path.join(tmp, "v1.mp4")
make_video(v1, [(50, (40, 40, 120)), (10, (60, 60, 140)), (60, (200, 30, 30))])
p1, info1, raw1, cuts1, sig1 = detect(v1, min_scene_frames=50,
                                      detector_params={"threshold": 6.0})
print(f"    检测到切点：{cuts1}，信号峰 50={sig1.score_at(50):.1f} / 60={sig1.score_at(60):.1f}")
check(cuts1 == [50, 60], f"检测前提：两个切点都被找到（{cuts1}）")
got_on, used_on = apply(raw1, info1.fps, p1, sig1, 120, True)
check(got_on == [(0, 60), (60, 120)] and used_on, f"智能开：保留强切点 60（{got_on}）")
got_off, used_off = apply(raw1, info1.fps, p1, sig1, 120, False)
check(got_off == [(0, 50), (50, 120)] and not used_off, f"智能关：贪心保留弱切点 50（{got_off}）")

print("V2) 切分吸附漏检切点（125 处漏检，上限 150 帧）")
v2 = os.path.join(tmp, "v2.mp4")
make_video(v2, [(125, (40, 40, 120)), (75, (80, 80, 160)), (100, (30, 220, 30))])
p2, info2, raw2, cuts2, sig2 = detect(v2, max_scene_frames=150,
                                      detector_params={"threshold": 60.0})
print(f"    检测到切点：{cuts2}，125 处信号={sig2.score_at(125):.1f} / 50 处={sig2.score_at(50):.1f}")
check(cuts2 == [200], f"检测前提：125 漏检、只找到 200（{cuts2}）")
got_on, used_on = apply(raw2, info2.fps, p2, sig2, 300, True)
mid = [b for a, b in got_on if 50 < b < 150]
check(len(mid) == 1 and abs(mid[0] - 125) <= 3 and used_on,
      f"智能开：切点吸附到 125 附近（{got_on}）")
got_off, _ = apply(raw2, info2.fps, p2, sig2, 300, False)
check(got_off == [(0, 100), (100, 200), (200, 300)], f"智能关：均匀切分（{got_off}）")
check(sig2.score_at(125) > 5 * max(1.0, sig2.score_at(50)), "漏检处信号显著高于平坦区")

print("V3) 纯静态回退均匀（200 帧单色，上限 60 帧）")
v3 = os.path.join(tmp, "v3.mp4")
make_video(v3, [(200, (200, 200, 200))])
p3, info3, raw3, cuts3, sig3 = detect(v3, max_scene_frames=60)
check(raw3 == [(0, 200)], f"检测前提：单场景（{raw3}）")
got_on, used_on = apply(raw3, info3.fps, p3, sig3, 200, True)
check(got_on == [(0, 50), (50, 100), (100, 150), (150, 200)] and not used_on,
      f"智能开：无峰可吸→均匀且标记未启用（{got_on}, used={used_on}）")
got_off, _ = apply(raw3, info3.fps, p3, sig3, 200, False)
check(got_off == got_on, "开关结果一致")

print("\n===== 结果 =====")
print("全部通过 ✅" if not FAILED else f"失败 {len(FAILED)} 项 ❌：{FAILED}")
sys.exit(1 if FAILED else 0)
