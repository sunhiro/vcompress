# vcompress

<p align="center">
  <b>Smart Hardware-Accelerated Video Compression with Perceptual VMAF Quality Gates & Binary Search</b>
  <br>
  <b>二分算法寻优 · Netflix VMAF 画质门限保障 · 硬件加速视频压缩引擎</b>
</p>

<p align="center">
  <a href="#english">English</a> | <a href="#chinese">中文说明</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Homebrew-brew%20install%20sunhiro%2Ftap%2Fvcompress-blue.svg" alt="Homebrew Install">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License MIT">
  <img src="https://img.shields.io/badge/algorithm-Binary%20Search%20CRF-purple.svg" alt="Binary Search">
  <img src="https://img.shields.io/badge/acceleration-Apple%20Silicon%20(vt)%20%7C%20Intel%20VAAPI-orange.svg" alt="Hardware Acceleration">
  <img src="https://img.shields.io/badge/quality%20gate-Netflix%20VMAF%20%E2%89%A5%2093.0-red.svg" alt="VMAF Quality Gate">
</p>

---

<a name="english"></a>
## English

### 💡 Why vcompress?

Most batch compression scripts use a **fixed CRF value** (e.g. CRF 22 across the board). This naive approach has two major flaws:
1. **Visual degradation**: High-motion or dark scenes easily get ruined by artifacts.
2. **Negative / negligible savings**: Low-complexity videos waste compute power with virtually zero space reduction.

`vcompress` solves this mathematically:
- **Binary Search Parameter Optimization**: Powered by Rust `ab-av1` lossless slice probing, it executes an automated **binary search** across the CRF/QP space to discover the exact balance point that achieves your target visual quality (**VMAF ≥ 93.0**) with maximum compression.
- **Strict Quality Gating & Auto-Rollback**: If a video fails to meet the target VMAF or doesn't yield meaningful space reduction (e.g., `< 20%`), `vcompress` **automatically discards the attempt and leaves the original file completely untouched**.

### ⚡ Key Highlights
- **Binary Search Tuning**: No more guessing CRF. The binary search algorithm finds the sweet spot in seconds.
- **Hardware Acceleration**:
  - **Apple Silicon (`-e vt`)**: Native `hevc_videotoolbox` hardware encoding at **7x–15x realtime speed**.
  - **Intel QuickSync / NAS (`-e vaapi`)**: Hardware transcoding for Linux / Synology NAS.
  - **Next-Gen AV1 (`-e svt-av1`)**: Future-proof compression.
- **100% EXIF & Date Preservation**: Copies `FileCreateDate`, `CreationDate`, and camera EXIF tags using `exiftool`. Timelines in Apple Photos, Immich, or Synology Photos will **never be corrupted**.
- **Interactive Visual Reporting**: Outputs incremental CSV, styled Excel spreadsheets (`.xlsx`), and a standalone Web dashboard (`.html`) featuring search, KPI cards, and sortable tables.
- **AI Agent & LLM Friendly**: Zero interactive prompts required. Clean CLI design with JSON progress logs makes it straightforward for AI assistants (like Claude, Cursor, ChatGPT, Gemini) to automate and supervise overnight tasks.

---

### 🚀 Quick Start (macOS Homebrew)

#### 1. One-Line Installation
```bash
brew install sunhiro/tap/vcompress
```
*(Homebrew automatically installs FFmpeg, ExifTool, ab-av1, and python dependencies for you!)*

#### 2. Usage Examples

```bash
# Production: Batch compress with Apple Silicon HEVC acceleration (Direct CLI command)
vcompress \
  -i /path/to/raw_videos \
  -o /path/to/compressed_videos \
  -e vt \
  --vmaf-min 93.0 \
  --min-saving-pct 20.0 \
  --min-file-mb 20.0 \
  --vmaf-threads 6

# Preview / Dry-run: Test savings & VMAF without saving heavy files
vcompress -i /path/to/raw_videos -o /tmp/preview -e vt --preview

# AV1 Encoding
vcompress -i /path/to/movies -o /path/to/movies_av1 -e svt-av1 --vmaf-min 95.0
```

*(If running from source, replace `vcompress` with `python3 src/vcompress_abav1.py`)*

#### 3. AI Copilot / LLM Automation
You can directly instruct your AI coding assistant (Cursor / Claude / Copilot):
> *"Please run `vcompress` on `/Volumes/T7/Footage` to `/Volumes/T7/Out` in dry-run mode (`--preview`), parse the generated CSV report, and report how much space we can save with VMAF ≥ 93."*

---

<a name="chinese"></a>
## 中文说明

### 💡 核心亮点：为什么需要 vcompress？

传统转码工具大多采用**“一刀切”的固定 CRF 压制**（例如盲目设 CRF 22）：
- **遇到高动态或暗光场景**：画质严重劣化、暗部色阶断层断裂；
- **遇到本身体积不大的视频**：耗费大量时间转码，体积却几乎没减甚至反向膨胀。

`vcompress` 引入了**二分搜索算法 (Binary Search) 与 Netflix VMAF 画质门限控制**：
1. **二分算法寻找最优解**：
   底层基于 Rust `ab-av1` 进行无损快速流切片采样，在 CRF/QP 取值区间内执行**二分搜索**，在短短数十秒内精确锁定刚好满足 `VMAF ≥ 93.0`（视觉无损基准）的最佳压缩率参数。
2. **两道硬门槛安全拦截（失败自动撤销）**：
   - 画质门槛：实测 `VMAF < 93.0` 拦截；
   - 收益门槛：节省空间 `< 20%` 拦截；
   - 触发拦截时**自动撤销转码产物并原样保留原片**，绝不劣化任何素材。
3. **拍摄时间与 EXIF 1:1 完美保留**：
   调用 `exiftool` 无损复制 `FileCreateDate`、`CreationDate` 与相机元数据。导入 Apple Photos、群晖 Photos、Immich 等相册软件时，**时间线完全不错乱**。
4. **AI 友好，极易通过 AI Agent 自动化下发**：
   全自动化流程，无任何交互弹窗打断，输出结构化 JSON 与 CSV。可让 Cursor、Claude Code 等 AI 助手直接接管通宵跑批。

---

### 🚀 快速安装（macOS Homebrew 推荐）

只需终端一行命令（自动安装全部底层依赖 FFmpeg、ExifTool、ab-av1）：

```bash
brew install sunhiro/tap/vcompress
```

*(也可以通过源码直接运行：`brew install ffmpeg exiftool ab-av1 && pip3 install openpyxl`)*

---

### 💻 常用命令示例

安装完成后，可以在任意目录下直接调用 `vcompress` 命令：

#### 1. 黄金性价比模式（推荐：Mac 硬件加速 + 20MB 门限）
```bash
vcompress \
  -i /Volumes/外接盘/视频素材 \
  -o /Volumes/外接盘/压缩后产物 \
  -e vt \
  --vmaf-min 93.0 \
  --min-saving-pct 20.0 \
  --min-file-mb 20.0 \
  --vmaf-threads 6
```

#### 2. 试压预览模式（仅测算评估，不占用磁盘写入大文件）
```bash
vcompress -i /Volumes/外接盘/DCIM -o /tmp/preview -e vt --preview
```

#### 3. 压制为下一代 AV1 格式
```bash
vcompress -i /Volumes/外接盘/Movies -o /Volumes/外接盘/Movies_AV1 -e svt-av1 --vmaf-min 95.0
```

#### 4. 生成或刷新独立 Web HTML 仪表盘与 Excel 台账
```bash
python3 src/export_report.py /Volumes/外接盘/压缩后产物
```

---

### 🤖 借助 AI Agent (Claude / Cursor / ChatGPT) 协同工作

因为 `vcompress` 完全支持非交互式运行与结构化台账，你可以直接把任务交给 AI：
> **你可以对 AI 说**：
> “请调用 `vcompress` 扫描我的移动硬盘 `/Volumes/T7/2024`，先用 `--preview` 预览，并读取生成的 `_report.csv` 告诉我预计可以释放多少 GB 空间、有多少个视频满足 VMAF 93 门槛。”

---

## 目录结构 / Repository Layout

```
vcompress/
├── README.md                  # 中英文主说明文档 / Bilingual Documentation
├── LICENSE                    # MIT 开源协议
├── CLAUDE.md                  # 项目核心规范与约束 / AI Guidelines
├── docs/
│   ├── ARCHITECTURE.md        # 架构设计与 VMAF 采样原理
│   ├── RED-LINES.md           # 12 条真机踩坑红线与质量守护
│   ├── PROMOTION_POSTS.md     # 社区发帖推广草稿 (V2EX / Reddit / HN)
│   └── REGRESSION.md          # 质量回归测试说明
├── src/
│   ├── vcompress_abav1.py     # 集成 Rust ab-av1 的高级二分搜寻引擎 (核心)
│   ├── vcompress.py           # 纯 Python + FFmpeg 真机版引擎
│   ├── export_report.py       # Excel (.xlsx) 与 Web HTML 仪表盘台账生成器
│   └── diag_vmaf_window.py    # VMAF 窗口采样诊断工具
├── tests/                     # 单元测试套件 (93+ 项断言锁)
└── win/                       # Windows 辅助工具与说明
```

## 开源协议 / License

本项目遵循 [MIT License](LICENSE)。欢迎提交 Issue 与 Pull Request！
