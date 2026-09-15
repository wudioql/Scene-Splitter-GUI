# -*- coding: utf-8 -*-
"""回归测试：重编码切片必须严格输出 [start_frame, end_frame) 的视频帧。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

from scene_splitter_gui import core


class Silent(core.Reporter):
    def log(self, _message, _level="info"):
        pass

    def stage(self, _text):
        pass


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="scene_splitter_frame_test_") as temp:
        root = Path(temp)
        src = root / "input.mp4"
        out_dir = root / "out"
        fps = 25.0
        total = 125
        writer = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"mp4v"),
                                 fps, (160, 90))
        if not writer.isOpened():
            raise RuntimeError("OpenCV 无法创建测试视频")
        expected_means = []
        for index in range(total):
            frame = np.full((90, 160, 3),
                            ((index * 3) % 256, (index * 5) % 256, (index * 7) % 256),
                            dtype=np.uint8)
            expected_means.append(float(frame[:, :, 0].mean()))
            writer.write(frame)
        writer.release()

        info = core.probe_video(str(src))
        scenes = [
            core.SceneSpan(1, 0, 25, fps),
            core.SceneSpan(2, 25, 50, fps),
            core.SceneSpan(3, 50, 79, fps),
            core.SceneSpan(4, 79, 125, fps),
        ]
        params = core.TaskParams(
            video_path=str(src), output_dir=str(out_dir),
            template="clip_$SCENE_NUMBER", reencode=True, audio="none",
            overwrite=True, save_csv=False, do_split=True,
        )
        outputs, problems = core.split_video(
            params, scenes, info, core.resolve_ffmpeg(), Silent(), str(out_dir))
        if problems or len(outputs) != len(scenes):
            raise AssertionError(f"切割失败：{problems}")

        for scene, path in zip(scenes, outputs):
            capture = cv2.VideoCapture(path)
            actual = []
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                actual.append(float(frame[:, :, 0].mean()))
            capture.release()
            if len(actual) != scene.frame_count:
                raise AssertionError(
                    f"{Path(path).name}: 帧数 {len(actual)} != {scene.frame_count}")
            if abs(actual[0] - expected_means[scene.start_frame]) > 8:
                raise AssertionError(f"{Path(path).name}: 首帧边界不正确")
            if abs(actual[-1] - expected_means[scene.end_frame - 1]) > 8:
                raise AssertionError(f"{Path(path).name}: 尾帧边界不正确")

    print("重编码帧边界测试通过：所有输出严格符合 [start_frame, end_frame)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
