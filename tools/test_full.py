# -*- coding: utf-8 -*-
"""完整回归：core 逻辑 + 预览工作流 + 界面端到端（离屏或 Xvfb 均可）。

用法：
    python tools/test_full.py
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = "/tmp/test_input.mp4"


def run(script: str) -> int:
    print(f"\n=== 运行 {script} ===", flush=True)
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONPATH", HERE)
    proc = subprocess.run([sys.executable, os.path.join(HERE, "tools", script)],
                          env=env, cwd=HERE)
    return proc.returncode


def main() -> int:
    if not os.path.exists(SRC):
        print("生成测试视频…")
        subprocess.run([sys.executable, os.path.join(HERE, "tools", "make_test_video.py"), SRC],
                       check=False)
    codes = [run("test_smartcut.py"), run("test_smartcut_video.py"),
             run("test_frame_boundaries.py"), run("test_core.py"), run("test_gui.py")]
    print("\n================ 总结果 ================")
    print("全部通过 ✅" if all(c == 0 for c in codes) else f"有失败项 ❌ {codes}")
    return 0 if all(c == 0 for c in codes) else 1


if __name__ == "__main__":
    sys.exit(main())
