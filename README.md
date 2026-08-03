# vcompress — 硬件加速与 VMAF 画质门限保障的视频压缩引擎

`vcompress` 是一个专门针对个人家庭影像、移动硬盘与 NAS 视频资产设计的独立视频转码与画质拦截工具。

本仓库同时提供**两种互补的高效引擎实现**：

1. **`src/vcompress_abav1.py` (推荐：集成 Rust `ab-av1` 后端的高级搜索引擎)**：
   * 基于 Rust 开源神器 `ab-av1` 后端，采用**无损流切片 + 二分法 (Binary Search)** 自动搜索精确达到目标 VMAF（如 93.0 分）的最佳 CRF/QP。
   * 支持 **8GB 内存多线程加速 (`--vmaf-threads 6`)**，VMAF 测算效率提升 60%+。
   * 支持 **20MB+ 黄金性价比门限 (`--min-file-mb 20.0`)**，精准覆盖家庭影像与手机录制素材。
   * 自动生成 **CSV / 格式化 Excel (`.xlsx`) / 交互式 Web 仪表盘 (`.html`) 三重台账**。
   * 支持 **断点续压** 与 Apple 设备 `mebx` 沉浸数据轨隔离。
2. **`src/vcompress.py` (纯 Python + FFmpeg 真机完整版)**：
   * 吸收自 `vcat` 项目在真机上打磨的全部六大修复（支持 `-e vt / vaapi / x265`）。
   * 具备两道门槛拦截与**近无损对照标尺 (`--control-floor`)** 机制，确保不会误判好文件。

---

## 核心功能与亮点

1. **Mac Apple Silicon 硬件加速 (`vt`)**：
   * 原生支持 Apple Silicon (`hevc_videotoolbox`)，硬件转码速度可达 **7~15 倍实时速度**。
2. **NAS / Intel 核显硬件加速 (`vaapi`)**：
   * 支持 Linux/群晖 NAS 核显硬件加速，转码速度比 CPU 软解提升 23 倍。
3. **二分法 CRF/QP 搜寻与 VMAF 门限保护**：
   * 彻底避免线性阶梯试压，通过二分法快速定位满足 `VMAF >= 93.0` 且 `省空间 >= 20%` 的最高压缩率参数。
4. **多格式可视化台账系统 (`CSV` / `Excel` / `HTML`)**：
   * 所有数值严格格式化为 `MB` / `Mbps` / `%` 提升直观度（保留 1 位小数）。
   * 自动导出高颜值 Excel 表格与包含 KPI 统计卡片、实时搜索、排序的 HTML 仪表盘。
5. **EXIF 元数据与拍摄时间完整继承**：
   * 调用 `exiftool` 自动将原视频创建时间 (`FileCreateDate`, `CreationDate`) 及相机拍摄 Tag 无缝复制至压缩产物。

---

## 安装依赖

```bash
# 安装 FFmpeg、ExifTool、Rust 后端 ab-av1 以及 openpyxl
brew install ffmpeg exiftool ab-av1
pip3 install openpyxl
```

---

## 使用指南

### 方式一：使用基于 Rust `ab-av1` 后端的高级引擎 (`vcompress_abav1.py`) 【推荐】

#### 1. 20MB 黄金门限 + 8GB 内存加速全量压缩 (Mac 硬件加速 HEVC)
```bash
python3 src/vcompress_abav1.py \
  -i /Volumes/T7/nas \
  -o /Volumes/T7/nas-压缩后 \
  -e vt \
  --vmaf-min 93.0 \
  --min-saving-pct 20.0 \
  --min-file-mb 20.0 \
  --vmaf-threads 6
```

#### 2. 试压预览模式 (仅做二分法测算评估，不保存产物文件)
```bash
python3 src/vcompress_abav1.py \
  -i /Volumes/T7/DCIM \
  -o /tmp/preview \
  -e vt \
  --vmaf-min 93.0 \
  --preview
```

#### 3. 手动导出/刷新 Excel 与 HTML 仪表盘台账
```bash
python3 src/export_report.py /Volumes/T7/nas-压缩后
```

#### 4. 压制为下一代 AV1 格式 (`svt-av1`)
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
│   ├── vcompress_abav1.py     # 集成 Rust ab-av1 后端的高级二分搜寻引擎 (核心)
│   ├── export_report.py       # Excel (.xlsx) 与 Web HTML 仪表盘台账生成器 (新增)
│   ├── vcompress.py           # 原真机完整版 Python 压缩引擎 (保留)
│   └── diag_vmaf_window.py    # VMAF 窗口采样诊断工具
└── examples/
    ├── example_usage.sh       # 常用命令示例脚本
    └── sample_run_log.txt     # 示例运行输出日志
```
