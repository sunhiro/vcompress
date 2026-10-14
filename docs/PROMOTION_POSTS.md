# vcompress 社区推广与发帖模板文案 (Promotion Drafts)

本文件包含用于国内社区（V2EX / 少数派 / 掘金 / 贴吧 / 恩山）与国际社区（Reddit / Hacker News）的推广贴文案。发布时请将链接替换为你真实的 GitHub 地址：`https://github.com/sunhiro/vcompress`。

---

## 目录 / Table of Contents
1. [国内中文推广贴 (V2EX / 少数派 / 掘金风格)](#1-国内中文推广贴)
2. [Reddit 发帖文案 (r/selfhosted / r/ffmpeg / r/homelab)](#2-reddit-发帖文案)
3. [Hacker News (Show HN) 极简贴文案](#3-hacker-news-show-hn-文案)

---

## 1. 国内中文推广贴

### 推荐标题
> **写了一个用 VMAF 画质门限守护家庭视频压缩的小工具（支持 Mac 硬件加速 / 群晖 Intel 核显）**

### 正文内容

大家好，

相信不少折腾 NAS、家庭相册（群晖 Photos / Immich / Apple Photos）或者喜欢用手机、运动相机记录生活的朋友，都遇到过一个很头疼的问题：**视频体积膨胀得太快，硬盘告急。**

### 为什么之前不愿批量压缩视频？
我也试过很多常见的批量转码脚本或工具，但总有一些绕不过去的痛点：
1. **怕画质崩（翻车）**：一般脚本都用固定的 CRF（比如 CRF 22 一刀切）。对静止画面可能很好，但遇到高动态、水面、草地或者暗光视频，固定参数很容易把细节压缩成大马赛克。
2. **负收益/得不偿失**：有些视频本身码率就不高，费劲压了半天体积反而没小多少，甚至体积倒挂。
3. **相册时间线全乱**：很多转码工具会丢失原始 EXIF 和相机拍摄时间（创建时间直接变成转码当天的时间），导致导入相册后时间线全毁。
4. **编码慢 / CPU 满载**：在群晖或普通机器上纯靠 CPU 软解压 H.265/AV1，不仅慢到怀疑人生，还会把 NAS 服务直接跑死。

---

### 我的解法：vcompress

为了安全、省心、批量地整理我的家庭影像，我写了这个独立的小引擎：**[vcompress](https://github.com/sunhiro/vcompress)**。

它的核心理念不是“盲目压”，而是**“视觉无损门限保障 + 失败自动撤销”**：

- **二分法搜寻最优 CRF + VMAF 门槛拦截**：
  基于 Rust 开源后端 `ab-av1` 快速切片采样，通过二分查找自动寻找能刚好满足 `VMAF >= 93.0`（Netflix 视觉无损基准）的最佳参数。如果算出来达不到画质目标，或者节省空间小于门槛（如 `< 20%`），**系统自动放弃压缩并原样保留原片**，绝不劣化素材。
- **硬件加速飞快**：
  - **Apple Silicon (`-e vt`)**：原生调用 M 芯片 `hevc_videotoolbox`，实测 7~15 倍实时速度，几秒钟过一段片子；
  - **Intel 核显 (`-e vaapi`)**：群晖 / Linux NAS 接核显做**硬件编码**（只硬编不硬解——核显解码器对付老旧 MPEG-2 不够稳，全片长跑会崩，红线 §9），大幅压低 CPU 占用；
  - 也支持下一代 **AV1 (`-e svt-av1`)**。
- **EXIF 与拍摄时间 1:1 继承**：
  调用 `exiftool` 自动将原视频创建时间（`FileCreateDate` / `CreationDate`）及拍摄标签无缝写入新文件，相册时间线完全不乱。
- **可视化台账（HTML 仪表盘 + Excel）**：
  每次跑完不仅有命令行日志，还会自动输出一份交互式 Web HTML 仪表盘和带色阶的 Excel 表格。顶部带节省空间 KPI 卡片，表格支持按文件名搜索、状态过滤与排序，哪些文件省了多少、画质几分一目了然。
- **支持断点续压**与 iPhone 拍摄视频 `mebx` 数据轨隔离，防止封装闪退。

---

### 快速上手

代码已完全开源（MIT 协议），使用非常轻量：

```bash
# 1. 安装依赖 (Mac)
brew install ffmpeg exiftool ab-av1
pip3 install openpyxl

# 2. 一键压缩一个目录
python3 src/vcompress_abav1.py \
  -i /Volumes/你的硬盘/原视频目录 \
  -o /Volumes/你的硬盘/压缩后目录 \
  -e vt \
  --vmaf-min 93.0 \
  --min-saving-pct 20.0 \
  --min-file-mb 20.0

# 3. 仅预览评估（不生成文件，先看能省多少）
python3 src/vcompress_abav1.py -i /Volumes/你的硬盘/DCIM -o /tmp/preview -e vt --preview
```

GitHub 地址：👉 **https://github.com/sunhiro/vcompress**

项目刚脱敏开源，代码有完整的单元测试覆盖。欢迎各位折腾 NAS、家庭影像与视频剪辑的朋友试用、提 Issue 或 PR！大家如果有其他硬件平台优化或者特别的视频转码踩坑经验，欢迎在帖子里一起交流！

---

## 2. Reddit 发帖文案

**适合版块**：`r/selfhosted`、`r/ffmpeg`、`r/homelab`、`r/macapps`

### Title
> **[Self-Hosted / Tool] I built vcompress: Batch video compressor with automated VMAF quality gates (Apple Silicon / Intel VAAPI / AV1)**

### Post Content

Hey everyone,

Like many of you managing a self-hosted media server or a growing family photo/video library (Immich, Synology Photos, Nextcloud), I was constantly struggling with storage exhaustion from high-bitrate phone videos and camera clips.

Standard batch re-encoding scripts (e.g., blanket CRF 22) always scared me because:
1. **Risk of visual degradation**: Fast dynamic scenes or low-light shots often get destroyed by static presets.
2. **Negative space gain**: Sometimes re-encoding already-compressed files yields negligible space savings or even bloated outputs.
3. **Broken timestamps**: Stripping EXIF tags ruins timeline sorting in photo gallery apps.

To solve this properly, I developed and open-sourced **[vcompress](https://github.com/sunhiro/vcompress)**.

### What makes it different?

- **Binary-search VMAF Quality Gates**: Instead of blind encoding, it samples slices via the Rust `ab-av1` backend and uses binary search to find the optimal CRF/QP that meets your perceptual threshold (e.g., **VMAF ≥ 93.0**).
- **Auto-Rollback / Safety-First**: If a video fails to meet the target VMAF or doesn't achieve your space savings threshold (e.g., minimum 20% reduction), it **skips the file and keeps the untouched original**.
- **Hardware Acceleration**:
  - **Apple Silicon (`-e vt`)**: 7x–15x realtime speed via `hevc_videotoolbox`.
  - **Intel Quicksync / NAS (`-e vaapi`)**: Hardware transcoding for Linux / Synology NAS servers to avoid pinning the CPU.
  - **AV1 (`-e svt-av1`)**: Ready for next-gen workflows.
- **EXIF & Date Preservation**: Replicates `FileCreateDate`, `CreationDate`, and camera tags via `exiftool` so timelines in Immich/Apple Photos stay intact.
- **Visual Reporting**: Generates a self-contained interactive **HTML Web Dashboard** and styled Excel sheets alongside CSV logs, complete with search, sorting, and savings KPI summaries.
- **Resilient**: Incremental skip / resume support and QuickTime `mebx` track crash isolation.

### Quick Command Example

```bash
# Compress with Apple Silicon HEVC acceleration
python3 src/vcompress_abav1.py \
  -i /path/to/raw_videos \
  -o /path/to/compressed_output \
  -e vt \
  --vmaf-min 93.0 \
  --min-saving-pct 20.0 \
  --min-file-mb 20.0
```

Repository: https://github.com/sunhiro/vcompress  
License: MIT

I'd love to hear your thoughts, feedback, or edge-case test results!

---

## 3. Hacker News (Show HN) 文案

### Title
> **Show HN: vcompress – Video compression with automated VMAF quality gates**

### Content
Hi HN,

I built `vcompress` (https://github.com/sunhiro/vcompress) to solve the dilemma of compressing personal video archives without accidental quality degradation.

Most automated compression scripts use static CRF values, which easily cause visible artifacts in complex scenes or waste computation when the reduction is negligible. `vcompress` uses binary search (backed by Rust `ab-av1` stream sampling) to calculate the exact CRF needed to achieve a target VMAF score (default ≥ 93.0, Netflix's threshold for perceptual transparency).

Key properties:
- **Gated rollback**: If the VMAF score or space-saving percentage fails to pass the gate, changes are discarded and the original file is preserved.
- **Hardware support**: Supports Apple Silicon VideoToolbox (`-e vt`, 7x–15x realtime speed) and Intel QuickSync/VAAPI (`-e vaapi`).
- **Metadata**: Reapplies creation dates and EXIF metadata to prevent breaking photo timeline apps (Immich, Apple Photos).
- **Audit reports**: Exports an interactive standalone HTML dashboard with KPI cards and sortable metrics.

It's MIT-licensed Python. Happy to answer any questions about the sampling mechanism or benchmark edge cases!
