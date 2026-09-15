# -*- coding: utf-8 -*-
"""core.py 无界面自测（开发用）。"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scene_splitter_gui import core

SRC = sys.argv[1] if len(sys.argv) > 1 else '/tmp/test_input.mp4'
OUT = sys.argv[2] if len(sys.argv) > 2 else '/tmp/out'

class Rep(core.Reporter):
    def __init__(self): self.t0 = time.time()
    def log(self, m, level='info'): print(f"[{time.time()-self.t0:6.1f}s][{level}] {m}", flush=True)
    def stage(self, t): print(f"--- 阶段: {t}", flush=True)
    def progress(self, c, t): pass

print('PySceneDetect', core.SCENEDETECT_VERSION, '可用' if core.SCENEDETECT_AVAILABLE else '不可用')
print('ffmpeg ->', core.resolve_ffmpeg())
print('视频信息:', core.probe_video(SRC))

p = core.TaskParams(
    video_path=SRC, output_dir=OUT, detector='content',
    detector_params={'threshold': 27.0, 'min_scene_len': 15},
    min_scene_sec=1.0, max_scene_sec=4.0, min_scene_frames=0, max_scene_frames=0,
    template='$VIDEO_NAME_第$SCENE_NUMBER段', pad=2, container='mp4',
    reencode=False, save_csv=True, save_html=True, save_images=True, image_count=2, overwrite=True,
)
res = core.run_task(p, Rep())
print('\n===== 结果 =====')
print('场景数', res.scene_count, '输出数', len(res.outputs), '耗时', round(res.elapsed,2))
for s in res.scenes:
    print(f'  #{s.index:02d} {s.start_tc} -> {s.end_tc}  {s.duration:6.2f}s {s.frame_count:4d}帧')
print('输出:', res.outputs)
print('csv:', res.csv_path, os.path.exists(res.csv_path))
print('html:', res.html_path, os.path.exists(res.html_path))
