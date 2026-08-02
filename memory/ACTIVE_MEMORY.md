# ACTIVE_MEMORY.md — vcompress 项目记忆与当前状态

> **最近更新时间**：2026-08-02
> **当前版本**：v1.0.0 (从 `nas`/`vcat` 独立解耦的独立引擎版本)

---

## 1. 本次独立与变更背景 (Extraction Summary)

根据用户指令，我们将视频转码与质量评估逻辑从原本的 `nas` 文件夹和 `vcat` 项目中独立解耦，创建了全新的独立项目 `vcompress`（路径：`/Users/sun/Projects/vcompress`）。

### 本次重构关键技术变更：
1. **彻底修复 VMAF 串帧 (Frame Desync) / 假低分 Bug**：
   * **历史原因分析**：过去在 `nas/run_comparison.py` 中使用了前置 `-ss` 命令分别 Seek 压缩前后的视频，因 GOP 关键帧位置不一致导致起跑线错开数帧；同时 iPhone 录制的视频多为 VFR（变帧率），导致 VMAF 评分波动巨大或暴跌。
   * **本次修复解法**：在 [`src/vcompress.py`](file:///Users/sun/Projects/vcompress/src/vcompress.py) 的 VMAF 计算滤镜链中添加了 `fps=30,format=yuv420p` 重采样规范化。使得两路视频在 VMAF 比对时在 `0.000s` 精确对齐，极度稳定。
2. **优化 Mac VideoToolbox 硬件转码参数**：
   * 支持通过 `-e vt --vt-quality 65` 调用 Apple M 系列芯片编码器，压片速度提高 5~10 倍。
3. **完善三窗口 (Beginning/Middle/End) 画质闸门**：
   * 优先使用 15 秒三段截取测试，取最低 VMAF 分数做门限。若 `VMAF < vmaf_min` (默认 92) 或 `saving < min_saving_pct` (默认 20%)，自动拦截全片编码或丢弃产物。
4. **规范化项目架构**：
   * 遵循 `Projects/[Name]/src` 结构规范，创建了 `README.md`、`CLAUDE.md`、`docs/ARCHITECTURE.md` 与 `examples/`。

---

## 2. 供下一个 AI 评审的要点 (Review Checklist for Next AI)

请下一个接手会话的 AI 对以下 3 个设计要点进行评审并提出优化建议：

### 🔍 评审项 1：VMAF 采样帧率动态化
- **现状**：当前 [`src/vcompress.py`](file:///Users/sun/Projects/vcompress/src/vcompress.py) 中的 VMAF 滤镜写死了 `fps=30`。
- **评审思考**：对于 60fps 的高帧率素材（如 4K 60fps 运动相机画面），强制降低到 30fps 计算 VMAF 是否会导致丢帧细节丢失？是否应该根据 `ffprobe` 获得的实际原片帧率动态决定 `fps=target_fps`？

### 🔍 评审项 2：多文件并发队列 (`ThreadPoolExecutor`)
- **现状**：目前处理多文件目录时为单线程串行循环处理。
- **评审思考**：Apple Silicon 芯片的 Media Engine（如 M1/M2/M3 Max/Pro）具备多个 VideoToolbox 编解码硬件核心。在面对上千个短视频时，是否增加 `--concurrency` 参数开启并行处理？

### 🔍 评审项 3：对接 `vcat` 台账系统的接口规范
- **现状**：目前日志输出为纯文本和终端控制台。
- **评审思考**：`vcat` 项目需要记录每个视频的压缩得分与状态。是否为 `vcompress` 增加一个 `--json-out` 参数，输出结构化的任务报告 (JSON)，方便 `vcat` 直接读取并入库？

---

## 3. 当前文件状态一览

- [`src/vcompress.py`](file:///Users/sun/Projects/vcompress/src/vcompress.py)：主工具脚本，已赋予执行权限。
- [`README.md`](file:///Users/sun/Projects/vcompress/README.md)：用户使用说明书与参数介绍。
- [`CLAUDE.md`](file:///Users/sun/Projects/vcompress/CLAUDE.md)：宪法级规则规范。
- [`docs/ARCHITECTURE.md`](file:///Users/sun/Projects/vcompress/docs/ARCHITECTURE.md)：VMAF 滤镜链架构原理解析。
- [`examples/example_usage.sh`](file:///Users/sun/Projects/vcompress/examples/example_usage.sh)：常见命令示例。
