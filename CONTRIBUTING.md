# CONTRIBUTING

欢迎提 Issue 与 PR。项目小，规矩也少，就下面几条。

## 环境搭建

```bash
git clone <本仓库> && cd <目录>
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows；Linux/macOS 把 Scripts 换成 bin
```

Windows 上双击 `安装依赖.bat` 会代劳（自动创建 `.venv` 并安装）。

## 测试

```bash
python tools/test_full.py          # 完整回归：纯逻辑 → 视频 E2E → 帧边界 → core → GUI（离屏）
python tools/test_smartcut.py      # 智能择优纯逻辑单测（零第三方依赖）
python tools/make_screenshots.py   # 重拍 README 的 6 张截图（改了界面后跑一遍）
```

PR 前请保证 `test_full.py` 全过。GUI 测试离屏运行，需要 Qt 能初始化
（Linux 缺库时按报错装，如 `libxkbcommon0`、`libpulse0`）。

## 代码风格

- 沿用现有风格：**中文注释**、类型注解、函数短小；
- `core.py` 保持零 GUI 依赖（这是架构红线，见 ARCHITECTURE）；
- 改了界面行为：同步更新 README 截图与相关段落；
- 改了版本号（两处：`config.APP_VERSION` + `core.__version__`）：同步记一笔 CHANGELOG。

## 打包与发版

1. 双击 `build_exe.bat`（优先用 `.venv`），产物在 `dist/PySceneDetect视频分割器/`；
2. 发版时同步三处：两处版本号 + `CHANGELOG.md`；
3. GitHub 发 Release 时把整个产物目录打 zip 传上去，注明"解压即用，已内置 ffmpeg"。

## core 当库用（二次开发）

`core.py` 不依赖 GUI，可直接 import（完整参数见 `TaskParams` 定义）：

```python
from scene_splitter_gui import core

params = core.TaskParams(
    video_path="D:/video.mp4",
    output_dir="D:/out",
    detector="content",
    detector_params={"threshold": 27.0, "min_scene_len": 15},
    min_scene_sec=2.0, max_scene_sec=60.0,
    template="$VIDEO_NAME_$SCENE_NUMBER",
    reencode=True, crf=22,
    save_csv=True,
)
result = core.run_task(params, core.Reporter())   # 一步到位
print(result.scene_count, result.outputs)

# 或分两步（= 界面预览/分割的同一逻辑）：
plan = core.plan_task(params, core.Reporter())    # 只检测，不切视频
print(core.summarize(plan.scenes))                # 段数/时长/统计与提示
plan.scenes.pop(3)                                # 手动去掉第 4 段
core.execute_plan(params, plan, core.Reporter())  # 按这份规划切
```
