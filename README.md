# vcompress — 硬件加速与 VMAF 画质门限保障的视频压缩引擎

`vcompress` 是一个专门针对个人家庭影像、移动硬盘与 NAS 视频资产设计的独立视频转码与画质拦截工具。

本仓库同时提供**两种互补的高效引擎实现**：

1. **`src/vcompress.py` (纯 Python + FFmpeg 真机完整版)**：
   * 吸收自 `vcat` 项目在真机上打磨的全部六大修复（支持 `-e vt / vaapi / x265`）。
   * 具备两道门槛拦截与**近无损对照标尺 (`--control-floor`)** 机制，确保不会误判好文件。
2. **`src/vcompress_abav1.py` (集成 Rust `ab-av1` 后端的高级搜索引擎)**：
   * 基于 Rust 开源神器 `ab-av1` 后端，采用**无损流切片 + 二分法 (Binary Search)** 自动搜索精确达到目标 VMAF（如 93.0 分）的最佳 CRF/QP。
   * 支持下一代 **AV1 (`svt-av1`)**、H.265/HEVC、`hevc_videotoolbox` 及 `hevc_vaapi`。

---

## 核心功能与亮点

1. **Mac Apple Silicon 硬件加速 (`vt`)**：
   * 原生支持 Apple Silicon (`hevc_videotoolbox`)，转码速度提升 5~10 倍。
2. **NAS / Intel 核显硬件加速 (`vaapi`)**：
   * 支持 Linux/群晖 NAS 核显硬件加速，转码速度比 CPU 软解提升 23 倍。
3. **二分法 CRF/QP 搜寻与 VMAF 门限保护**：
   * 彻底避免线性阶梯试压，通过二分法快速定位满足 `VMAF >= 93.0` 且 `省空间 >= 20%` 的最高压缩率参数。
4. **EXIF 元数据与拍摄时间完整继承**：
   * 调用 `exiftool` 自动将原视频创建时间 (`FileCreateDate`, `CreationDate`) 及相机拍摄 Tag 无缝复制至压缩产物。

---

## 安装依赖

```bash
# 安装 FFmpeg、ExifTool 以及 Rust 后端 ab-av1
brew install ffmpeg exiftool ab-av1
```

---

## 使用指南

### 方式一：使用基于 Rust `ab-av1` 后端的高级引擎 (`vcompress_abav1.py`)

#### 1. 试压预览模式 (仅做二分法测算评估，不保存产物文件)
```bash
python3 src/vcompress_abav1.py \
  -i /Volumes/T7/DCIM \
  -o /tmp/preview \
  -e vt \
  --vmaf-min 93.0 \
  --preview
```

#### 2. 正式批量转码 (Mac 硬件加速 HEVC)
```bash
python3 src/vcompress_abav1.py \
  -i /Volumes/T7/PhotosAndVideos \
  -o /Volumes/T7/Compressed_Output \
  -e vt \
  --vmaf-min 93.0 \
  --min-saving-pct 20.0
```

#### 3. 压制为下一代 AV1 格式 (`svt-av1`)
```bash
python3 src/vcompress_abav1.py \
  -i /Volumes/T7/Movies \
  -o /Volumes/T7/Movies_AV1 \
  -e svt-av1 \
  --vmaf-min 95.0
```

---

### 方式二：使用真机完整版引擎 (`vcompress.py`)

```bash
python3 src/vcompress.py \
  -i /Volumes/T7/PhotosAndVideos \
  -o /Volumes/T7/Compressed_Output \
  -e vt \
  --allow-vt-hevc \
  --recompress-hevc \
  --vmaf-min 93.0 \
  --min-saving-pct 20.0
```

---

## 目录结构说明

```
vcompress/
├── README.md                  # 项目主说明文档
├── CLAUDE.md                  # 项目宪法与 AI 协作指导规范
├── REVIEW_FROM_VCAT.md        # 来自 vcat AI 的评审与合并答复
├── memory/
│   └── ACTIVE_MEMORY.md       # 运行状态与 AI 上下文记忆
├── docs/
│   ├── ARCHITECTURE.md        # 架构设计与 VMAF 采样原理
│   ├── RED-LINES.md           # 踩坑记录与真机红线防范
│   └── REGRESSION.md          # 采样回归测试说明
├── src/
│   ├── vcompress.py           # 原真机完整版 Python 压缩引擎 (保留)
│   ├── vcompress_abav1.py     # 集成 Rust ab-av1 后端的高级二分搜寻引擎 (新增)
│   └── diag_vmaf_window.py    # VMAF 窗口采样诊断工具
└── examples/
    ├── example_usage.sh       # 常用命令示例脚本
    └── sample_run_log.txt     # 示例运行输出日志
```
