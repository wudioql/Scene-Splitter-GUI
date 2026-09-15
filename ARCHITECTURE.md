# ARCHITECTURE

给开发者看的结构说明。用户手册见 [README](README.md)，开发流程见 [CONTRIBUTING](CONTRIBUTING.md)。

## 分层

```
main.py                  程序入口：任务栏归类、图标加载、异常兜底（Windows 无窗口启动不静默退出）
scene_splitter_gui/
├── core.py              ★ 纯逻辑，零 GUI 依赖：探测 → 检测 → 分段整理 → ffmpeg 切割 → 报告导出
├── smartcut.py          智能择优：画面变化信号 + 合并/切分动态规划（被 core 调用）
├── segment_player.py    分段试看播放器（QtMultimedia 内嵌 + ffmpeg 截取后外部播放）
├── worker.py            QThread 工作线程：进度/日志/取消信号
├── config.py            配置持久化（QConfig → ~/.scene_splitter_gui/config.json）
├── widgets.py           Fluent 通用控件（数值/路径/信息卡等）
├── main_window.py       主窗口：导航、任务调度、拖放、提示条
├── pages/               四页：split_page（视频分割）/ batch_page / result_page / settings_page
└── assets/              自带图标（movie_black/white.ico/png，按主题选用）
tools/                    无界面脚本：test_full.py 一键回归、test_gui.py 界面端到端、
                          test_core.py / test_frame_boundaries.py / test_smartcut*.py 单项测试、
                          make_test_video.py 生成测试视频、make_screenshots.py 重拍 README 截图
```

版本号只有两处：`config.py` 的 `APP_VERSION`（界面标题）与 `core.py` 的 `__version__`，发版时同步改。

## 数据流（一次分割任务）

```
视频文件
  → core.detect()          场景检测；同时记录画面变化信号 FrameSignal（智能择优用）
  → core.apply_limits()    分段整理：过短合并（DP 留强去弱）/ 超长切分（吸附变化峰值）
  → core.plan_task()       只规划不切割 = 界面「① 预览分段」看到的同一份结果
  → core.execute_plan()    按规划逐段调 ffmpeg 切割 = 界面「② 开始分割」
  → 报告                   CSV / HTML / 缩略图
```

GUI 的预览与分割**共用同一份规划对象**（单代码路径），不存在"预览一套、执行一套"的不一致；
改了影响分段的参数会把预览标记过期并禁用分割按钮，强制先重新预览。

## 线程与取消

耗时工作（检测/切割）跑在 `worker.py` 的 QThread 里，主线程只收信号更新界面。
取消通过 `reporter.cancelled` 传递：检测在逐帧钩子里检查（当前帧即停），切割在每段之间检查。

`detect()` 内部重写了 PySceneDetect 的逐帧方法，用于实时进度、即时取消与信号记录；
个别版本签名不兼容时自动退回原生行为（仅进度变粗），见 [兼容性说明](README.md#八兼容性说明)。

## 持久化（本项目无数据库）

唯一的持久化是用户目录下的 `~/.scene_splitter_gui/config.json`（外观、行为、上次参数），
由 `config.py` 经 QConfig 读写。除此之外的状态（预览规划、结果列表）都在内存里，关闭即丢。

## 关键决策

1. **预览即规划**：`plan_task()` 既是预览的数据源，又是切割的输入，预览表里的文件名就是最终文件名。
2. **约束优先于智能**：智能择优的候选切点必须严格落在 `[最短, 最长]` 内；无可行解或纯静态内容时逐段回退均匀切分，绝不输出违规片段。
3. **core 零 GUI 依赖**：`core.py` 只 import 标准库 + smartcut（第三方全在函数内按需导入），可当库直接用（示例见 CONTRIBUTING），也方便无界面测试。
4. **图标三级链**（Windows 任务栏曾出现空白按钮，见 CHANGELOG 1.2.1）：自带 `.ico` 绝对路径加载 → Qt `setWindowIcon` → 原生 `WM_SETICON` 兜底；大小图标分流（任务栏跟系统主题、标题栏跟 App 主题），缺文件时回退 FluentIcon。
