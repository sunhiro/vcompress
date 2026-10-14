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
- **Bit-Depth Guard**: VMAF is a **luma-only** metric — it can't see 10-bit → 8-bit banding at all. Since `-e vaapi` output is 8-bit by design (the target iGPUs can't do 10-bit HEVC anyway), a 10-bit source on that path is **refused by default** rather than silently flattened (`--allow-bitdepth-downgrade` to override). Chroma (4:2:2 → 4:2:0) is allowed but always recorded in the report's pixel-format column (`--no-chroma-downgrade` to refuse it instead).

### ⚡ Key Highlights
- **Binary Search Tuning**: No more guessing CRF. The binary search algorithm finds the sweet spot in seconds.
- **Hardware Acceleration**:
  - **Apple Silicon (`-e vt`)**: Native `hevc_videotoolbox` hardware encoding at **7x–15x realtime speed** (Supports all M-series: **M1 / M2 / M3 / M4 / Pro / Max / Ultra**).
  - **Intel QuickSync / NAS (`-e vaapi`)**: Hardware transcoding for Linux / Synology NAS.
  - **CPU Software Encoding (`-e x265`)**: Universal compatibility across any PC or cloud server.
  - **Next-Gen AV1 (`-e svt-av1`)**: Future-proof compression for high-efficiency archiving.
- **100% EXIF & Date Preservation**: Copies `FileCreateDate`, `CreationDate`, and camera EXIF tags using `exiftool`. Timelines in Apple Photos, Immich, or Synology Photos will **never be corrupted**.
- **Interactive Visual Reporting**: Outputs incremental CSV, styled Excel spreadsheets (`.xlsx`), and a standalone Web dashboard (`.html`) featuring search, KPI cards, and sortable tables.
- **AI Agent & LLM Friendly**: Zero interactive prompts required. Clean CLI design with JSON progress logs makes it straightforward for AI assistants (like Claude, Cursor, ChatGPT, Gemini) to automate and supervise overnight tasks.

### 🖥️ Hardware Compatibility Matrix

| Hardware Platform | Support | Encoder (`-e`) | Notes / Performance |
|---|:---:|---|---|
| **Apple Silicon (M1 / M2 / M3 / M4)** | 🌟 **Best** | `-e vt` | **7x–15x realtime speed**, silent, lowest power draw. |
| **Intel Mac (Older macOS)** | ✅ Full | `-e vt` | Uses VideoToolbox with Intel QuickSync. |
| **Synology / Linux NAS (Intel iGPU)** | ✅ Full | `-e vaapi` | Native VAAPI hardware transcoding without pinning CPU. |
| **Standard Linux / Cloud Server** | ✅ Full | `-e x265` / `-e svt-av1` | Universal CPU encoding. Slower but highest efficiency. |
| **Windows PC** | ✅ Full | See `win/` or `-e x265` | Supported via standalone scripts in `win/` or pure Python. |

---

### 🚀 Quick Start & Installation

#### Option 1: macOS via Homebrew (Recommended for Mac)
```bash
# One-line install: automatically sets up FFmpeg, ExifTool, ab-av1, and vcompress
brew install sunhiro/tap/vcompress
```

#### Option 2: Docker / Synology NAS (Zero dependencies on host)
```bash
# Pull and run directly on NAS / Linux with Intel VAAPI hardware acceleration
docker run --rm --device /dev/dri \
  -v /volume1/videos:/data/in \
  -v /volume1/compressed:/data/out \
  ghcr.io/sunhiro/vcompress:latest \
  -i /data/in -o /data/out -e vaapi
```

#### Option 3: Python Pip / Git (Cross-Platform)
```bash
# Install directly from GitHub
pip install git+https://github.com/sunhiro/vcompress.git
```

> ⚠ **pip only installs the Python engine.** The external binaries are *not* bundled —
> you still need `ffmpeg` (built **with `--enable-libvmaf`**), `ab-av1` and `exiftool`
> on `PATH`, or the VMAF quality gate cannot be computed at all:
> ```bash
> # macOS
> brew install ffmpeg exiftool ab-av1
> # Debian/Ubuntu: distro ffmpeg has NO libvmaf — use a static build
> #   https://github.com/BtbN/FFmpeg-Builds/releases  (linux64-gpl)
> ```

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
2. **硬门槛安全拦截（失败自动撤销）**：
   - 画质门槛：实测 `VMAF < 93.0` 拦截；
   - 收益门槛：节省空间 `< 20%` 拦截；
   - 位深门槛：`-e vaapi` 路径的产物**写死 8bit**（目标核显本来也只能编 8bit HEVC），
     所以 10bit 源在这条路上**默认拒绝**（`--allow-bitdepth-downgrade` 才放行）——
     VMAF 只在亮度通道上算，**测不出 10bit→8bit 的色带**，而色带不可逆；
   - 色度降级（4:2:2→4:2:0）默认放行，但一律记进报表第 8 列，让人知道发生了什么
     （`--no-chroma-downgrade` 可改为拒绝）；
   - 触发拦截时**自动撤销转码产物并原样保留原片**，绝不劣化任何素材；
   - 输出路径若与源片是同一个文件（`-i x.mp4 -o x.mp4` 这种误用），**编码前直接拒绝**，
     绝不覆写原片。
3. **拍摄时间与 EXIF 1:1 完美保留**：
   调用 `exiftool` 无损复制 `FileCreateDate`、`CreationDate` 与相机元数据。导入 Apple Photos、群晖 Photos、Immich 等相册软件时，**时间线完全不错乱**。
4. **AI 友好，极易通过 AI Agent 自动化下发**：
   全自动化流程，无任何交互弹窗打断，输出结构化 JSON 与 CSV。可让 Cursor、Claude Code 等 AI 助手直接接管通宵跑批。

### 🖥️ 硬件兼容性与机型支持矩阵

无论你使用的是最新款 Mac、还是群晖 NAS 或普通 PC，`vcompress` 均提供对应的高效转码路径：

| 设备类型 / 硬件平台 | 推荐指数 | 对应编码器参数 (`-e`) | 说明与实测表现 |
|---|:---:|---|---|
| **Apple Silicon (M1 / M2 / M3 / M4 全系列)** | 🌟 **最佳体验** | `-e vt` (`hevc_videotoolbox`) | **7~15 倍实时速度**，不挑机型（M1 到 M4 均享满血硬编），发热极低、静音省电。 |
| **Intel 芯片老款 Mac** | ✅ **完全支持** | `-e vt` | 自动调用 Intel QuickSync 核显硬件加速。 |
| **群晖 / Linux NAS (带 Intel 核显)** | ✅ **完全支持** | `-e vaapi` | Linux / 群晖原生 VAAPI 核显加速，比 CPU 软压快 20+ 倍且绝不卡死 NAS 服务。 |
| **通用 Linux / 云服务器** | ✅ **完全支持** | `-e x265` 或 `-e svt-av1` | 纯 CPU 算力压制，速度稍慢但具备工业级极致极限压缩率。 |
| **Windows PC** | ✅ **完全支持** | 见 `win/` 工具或 `-e x265` | 支持 `win/` 目录下的专用 SSIM 引擎或纯 Python 跨平台运行。 |

---

### 🚀 快速安装方式

#### 方式一：macOS Homebrew 一键安装（苹果用户首选推荐）
只需终端一行命令（自动安装全部底层依赖 FFmpeg、ExifTool、ab-av1 与 vcompress 命令）：
```bash
brew install sunhiro/tap/vcompress
```

#### 方式二：Docker / 群晖 NAS 容器运行（零环境污染，免装依赖）
镜像内已预装好完整版 FFmpeg、VAAPI 驱动、ExifTool 与 ab-av1：
```bash
# 群晖/Linux 下接线 Intel 核显做**硬件编码**（红线 §9：只硬编、不硬解）：
docker run --rm --device /dev/dri \
  -v /volume1/video:/data/in \
  -v /volume1/video-compressed:/data/out \
  ghcr.io/sunhiro/vcompress:latest \
  -i /data/in -o /data/out -e vaapi
```

#### 方式三：Python pip / git 跨平台安装
```bash
pip install git+https://github.com/sunhiro/vcompress.git
```

> ⚠ **pip 只装 Python 引擎本体，不带外部二进制。** `ffmpeg`、`ab-av1`、`exiftool`
> 仍需自行装好并放进 `PATH`，否则 VMAF 门限根本算不出来：
> ```bash
> # macOS
> brew install ffmpeg exiftool ab-av1
> # Debian/Ubuntu：发行版自带的 ffmpeg 没有 --enable-libvmaf，必须换静态构建
> #   https://github.com/BtbN/FFmpeg-Builds/releases  (linux64-gpl)
> ```

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
│   ├── RED-LINES.md           # 15 条真机踩坑红线与质量守护
│   ├── PROMOTION_POSTS.md     # 社区发帖推广草稿 (V2EX / Reddit / HN)
│   └── REGRESSION.md          # 质量回归测试说明
├── src/
│   ├── vcompress_abav1.py     # 集成 Rust ab-av1 的高级二分搜寻引擎 (核心)
│   ├── vcompress.py           # 纯 Python + FFmpeg 真机版引擎
│   ├── export_report.py       # Excel (.xlsx) 与 Web HTML 仪表盘台账生成器
│   ├── naming.py              # 产物命名契约 (与 vcat compress_worker.py 逐字一致, ADR-020)
│   ├── fullset.py             # 全集清单与整树内容哈希
│   ├── hot_folder_worker.py   # 监视目录的常驻工人
│   └── diag_vmaf_window.py    # VMAF 窗口采样诊断工具
├── tests/                     # 单元测试套件 (201 项断言锁)
└── win/                       # Windows 辅助工具与说明
```

## 开源协议 / License

本项目遵循 [MIT License](LICENSE)。欢迎提交 Issue 与 Pull Request！
