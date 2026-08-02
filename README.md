# vcompress — 硬件加速与 VMAF 画质门限保障的视频压缩引擎

`vcompress` 是一个专门针对个人家庭影像、移动硬盘与 NAS 视频资产设计的独立视频转码与画质拦截工具。

它不盲目压缩，而是在压缩前/中通过 **VMAF (Video Multi-Method Assessment Fusion) 客观画质评估** 建立“质量门限”，确保只保留**视觉无损且高压缩率**的转码版本。若画质不达标或空间节省率不足，会自动拦截并保留原片。

---

## 核心功能与亮点

1. **Mac Apple Silicon 硬件加速**：
   * 原生支持 Apple Silicon (`hevc_videotoolbox`)，转码速度比传统 CPU 软解提升 5~10 倍，低功耗高效率。
   * 同时保留 CPU `x265` 极致编码器选项，满足多样化压缩需求。

2. **帧精确对齐的 VMAF 画质门限保障**：
   * 采用多窗口采样 (Beginning / Middle / End) 评估视频最高瑕疵段。
   * 采用 `setpts=PTS-STARTPTS,fps=30,format=yuv420p` 滤镜链，彻底解决 VFR (变帧率) 手机视频在 VMAF 计算时的**串帧/时间轴漂移假低分**问题。

3. **智能拦截与安全回滚**：
   * 支持 `--vmaf-min`（画质阈值，默认 92 分）与 `--min-saving-pct`（最低节省比例，默认 20%）。
   * 压缩后如果不划算（如小文件 `<100MB` 或画质下降过大），脚本会自动取消全片编码或丢弃产物，绝不破坏/覆盖原始文件。

4. **EXIF 元数据与拍摄时间完整无损迁移**：
   * 调用 `exiftool` 自动将原视频的创建时间 (`FileCreateDate`, `CreationDate`) 及相机拍摄 Tag 无缝复制至压缩视频。

---

## 安装依赖

在使用 `vcompress` 之前，请确认系统已安装 `ffmpeg` (带 `libvmaf` 支持) 和 `exiftool`：

```bash
brew install ffmpeg exiftool
```

---

## 使用指南

### 1. 单个文件压缩与测分
```bash
python3 src/vcompress.py -i /Volumes/T7/Videos/family.mov -o /Volumes/T7/Videos_Compressed/family.mp4
```

### 2. 外接移动硬盘 / 目录批量处理（推荐预设）
```bash
python3 src/vcompress.py \
  -i /Volumes/MobileDisk/PhotosAndVideos \
  -o /Volumes/MobileDisk/CompressedVideos \
  --encoder vt \
  --vt-quality 65 \
  --vmaf-min 92.0 \
  --min-saving-pct 20.0 \
  --min-file-mb 100.0
```

### 3. 全速无测分转码模式 (用于快速整理)
```bash
python3 src/vcompress.py -i /Volumes/MobileDisk/DCIM -o /Volumes/MobileDisk/Compressed --no-vmaf
```

---

## 参数说明

| 参数 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `-i, --input` | **必填** | 输入文件或源视频目录 |
| `-o, --output` | **必填** | 输出文件或目标目录 |
| `-e, --encoder` | `vt` | 编码引擎: `vt` (Apple 硬件加速) 或 `x265` (CPU 软解) |
| `--vt-quality` | `65` | VideoToolbox 质量参数 (推荐 60-70) |
| `--crf` | `22` | x265 CRF 质量参数 |
| `--vmaf-min` | `92.0` | VMAF 最低允许画质得分 (低于此值将拦截放弃转码) |
| `--min-saving-pct` | `20.0` | 最低要求节省的体积百分比 |
| `--min-file-mb` | `100.0` | 跳过压缩的小文件体积阈值 (MB) |
| `--no-vmaf` | `false` | 跳过 VMAF 画质采样计算，直接全速编码 |

---

## 目录结构说明

```
vcompress/
├── README.md               # 项目主说明文档
├── CLAUDE.md               # 项目宪法与 AI 协作指导规范
├── memory/
│   └── ACTIVE_MEMORY.md    # 运行状态与 AI 上下文记忆
├── docs/
│   └── ARCHITECTURE.md     # 架构设计与 VMAF 帧同步原理解析
├── src/
│   └── vcompress.py        # 核心压缩与评分引擎
└── examples/
    ├── example_usage.sh    # 常用命令示例脚本
    └── sample_run_log.txt  # 示例运行输出日志
```
