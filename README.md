# vcompress

<p align="center">
  <b>Smart Hardware-Accelerated Video Compression with Perceptual VMAF Quality Gates</b>
  <br>
  <b>面向家庭影像与 NAS 资产的硬件加速智能视频压缩引擎（基于 VMAF 视觉无损门限保障）</b>
</p>

<p align="center">
  <a href="#english">English</a> | <a href="#chinese">中文说明</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License MIT">
  <img src="https://img.shields.io/badge/python-3.8+-brightgreen.svg" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/acceleration-Apple%20Silicon%20(vt)%20%7C%20Intel%20VAAPI-orange.svg" alt="Hardware Acceleration">
  <img src="https://img.shields.io/badge/metric-VMAF%20%7C%20SSIM-red.svg" alt="Quality Gate">
</p>

---

<a name="english"></a>
## English

### Overview
`vcompress` is an autonomous, hardware-accelerated batch video compression tool engineered specifically for personal photo libraries, NAS servers, and external drives.

Unlike conventional encoders that blindly re-encode videos with fixed parameters, `vcompress` enforces **strict VMAF perceptual quality gates (e.g., VMAF ≥ 93.0) and space-saving thresholds (e.g., Saving ≥ 20%)** using automated binary search across CRF/QP space. If a compressed version suffers visual degradation or fails to achieve significant space reduction, `vcompress` **aborts the modification and preserves the original file intact**.

### Key Highlights
- **Binary Search Parameter Tuning**: Leverages Rust-powered `ab-av1` backend to locate the optimal CRF/QP without brute-force linear passes.
- **Hardware Acceleration**:
  - **Apple Silicon (`-e vt`)**: Native VideoToolbox HEVC encoding at 7x–15x realtime speed.
  - **Intel QuickSync / NAS (`-e vaapi`)**: Intel iHD VAAPI hardware encoding.
  - **AV1 Support (`-e svt-av1`)**: Next-gen compression for modern playback setups.
- **Strict Quality Gating & Auto-Rollback**: Protects originals at all costs. Never silently overwrites or degrades media.
- **Complete Metadata & Date Preservation**: Extracts and reapplies original creation date (`FileCreateDate`, `CreationDate`) and EXIF tags via `exiftool`.
- **Multi-Format Visual Reports**: Automatically outputs incremental CSV logs, styled Excel spreadsheets (`.xlsx`), and an interactive web dashboard (`.html`) featuring search, KPI metrics, and sortable tables.
- **Resilient & Resume-Ready**: Built-in resume support, Apple `mebx` track isolation, and negative-gain protection.

### Quick Start

#### Installation (macOS via Homebrew - Recommended)
```bash
# One-line install (automatically pulls FFmpeg, ExifTool, ab-av1 and vcompress)
brew install sunhiro/tap/vcompress
```

Alternatively, run from source:
```bash
brew install ffmpeg exiftool ab-av1
pip3 install openpyxl
```

#### Usage Examples
```bash
# 1. Full batch compression with Apple Silicon HEVC acceleration
python3 src/vcompress_abav1.py \
  -i /path/to/raw_videos \
  -o /path/to/compressed_videos \
  -e vt \
  --vmaf-min 93.0 \
  --min-saving-pct 20.0 \
  --min-file-mb 20.0 \
  --vmaf-threads 6

# 2. Preview / Dry-run mode (computes VMAF & savings without writing files)
python3 src/vcompress_abav1.py \
  -i /path/to/raw_videos \
  -o /tmp/preview \
  -e vt \
  --vmaf-min 93.0 \
  --preview

# 3. Generate HTML & Excel visual dashboard
python3 src/export_report.py /path/to/compressed_videos
```

---

<a name="chinese"></a>
## 中文说明

### 项目背景与简介
`vcompress` 是一个专门针对个人家庭影像、移动硬盘与 NAS 视频资产设计的独立视频转码与画质拦截工具。

传统压缩脚本通常使用固定的 CRF 参数，极易导致复杂动态场景画质崩坏，或者低动态场景过度浪费算力。`vcompress` 引入了**二分法 CRF/QP 搜寻与 Netflix VMAF 画质门限控制（默认 VMAF ≥ 93.0 且节省空间 ≥ 20%）**：
- **画质达标且省空间**：输出高质量压缩文件；
- **画质受损或压缩收益微弱**：自动撤销并原样保留原片，坚决不劣化素材。

### 核心功能与特性
1. **Apple Silicon 硬件加速 (`vt`)**：
   - 原生支持 M 系列芯片的 `hevc_videotoolbox`，转码速度可达 **7~15 倍实时速度**。
2. **Intel 核显 / NAS 硬件加速 (`vaapi`)**：
   - 支持 Linux / 群晖 NAS 核显硬件加速，相比纯 CPU 软解大幅降低负载。
3. **二分法 CRF/QP 搜寻与 VMAF 门槛拦截**：
   - 基于 Rust `ab-av1` 高效无损切片采样，利用二分算法快速锁定最优编码参数，杜绝画质翻车。
4. **拍摄时间与 EXIF 元数据完美继承**：
   - 联动 `exiftool` 将原视频的拍摄时间 (`FileCreateDate`、`CreationDate`) 与相机元数据 1:1 复制到新视频，相册（如 Apple Photos、Immich、群晖 Photos）时间线绝不紊乱。
5. **多格式可视化台账系统 (`CSV` / `Excel` / `HTML`)**：
   - 自动生成数据清晰的 `_report.csv`、高颜值带单位格式的 Excel 表格（`.xlsx`）以及开箱即用的 Web 交互仪表盘（`.html`，支持按文件名搜索、状态过滤与指标排序）。
6. **生产级安全防护**：
   - 原片零改动、支持断点续压、自动隔离 iPhone `mebx` 沉浸数据轨防止封装闪退。

### 快速安装（推荐 macOS Homebrew 一键安装）

```bash
# 一键安装（自动安装依赖 FFmpeg, ExifTool, ab-av1 与 vcompress 命令）
brew install sunhiro/tap/vcompress
```

也可以选择从源码运行：
```bash
brew install ffmpeg exiftool ab-av1
pip3 install openpyxl
```

### 常用运行示例

```bash
# 1. 生产推荐：20MB 黄金门限 + 8GB 内存加速全量压缩 (Mac 硬件加速 HEVC)
python3 src/vcompress_abav1.py \
  -i /Volumes/ExternalDrive/Videos \
  -o /Volumes/ExternalDrive/Videos-Compressed \
  -e vt \
  --vmaf-min 93.0 \
  --min-saving-pct 20.0 \
  --min-file-mb 20.0 \
  --vmaf-threads 6

# 2. 试压预览模式 (仅做二分法评估，不写入大文件)
python3 src/vcompress_abav1.py \
  -i /Volumes/ExternalDrive/DCIM \
  -o /tmp/preview \
  -e vt \
  --vmaf-min 93.0 \
  --preview

# 3. 压制为下一代 AV1 格式 (svt-av1)
python3 src/vcompress_abav1.py \
  -i /Volumes/ExternalDrive/Movies \
  -o /Volumes/ExternalDrive/Movies_AV1 \
  -e svt-av1 \
  --vmaf-min 95.0

# 4. 独立生成/刷新 Excel 与 HTML 网页台账
python3 src/export_report.py /Volumes/ExternalDrive/Videos-Compressed
```

---

## 目录结构 / Repository Layout

```
vcompress/
├── README.md                  # 中英文主说明文档 / Bilingual Documentation
├── LICENSE                    # MIT 开源协议
├── CLAUDE.md                  # 项目核心规范与约束 / AI Guidelines
├── REVIEW_FROM_VCAT.md        # 架构评审记录
├── docs/
│   ├── ARCHITECTURE.md        # 架构设计与 VMAF 采样原理
│   ├── RED-LINES.md           # 12 条真机踩坑红线与质量守护
│   └── REGRESSION.md          # 质量回归测试说明
├── src/
│   ├── vcompress_abav1.py     # 集成 Rust ab-av1 的高级二分搜寻引擎 (推荐/核心)
│   ├── vcompress.py           # 纯 Python + FFmpeg 真机版引擎
│   ├── export_report.py       # Excel (.xlsx) 与 Web HTML 仪表盘台账生成器
│   └── diag_vmaf_window.py    # VMAF 窗口采样诊断工具
├── tests/                     # 单元测试套件 (93+ 项断言锁)
└── win/                       # Windows 辅助工具与说明
```

## 开源协议 / License

本项目遵循 [MIT License](LICENSE)。欢迎提交 Issue 与 Pull Request！
