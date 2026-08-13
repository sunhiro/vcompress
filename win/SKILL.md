---
name: video-batch-compressor
description: >
  Batch compress videos in a directory to H.265/HEVC using FFmpeg, preserving
  folder structure. Supports dry-run preview, smart bitrate+size filtering,
  incremental skip (resume), timestamp preservation, and negative-gain protection.
  Use when the user wants to compress a video folder, check compression candidates,
  or continue an interrupted compression run.
---

# Video Batch Compressor Skill

## 概述 / Overview

本 Skill 对指定目录下的视频文件进行批量 H.265 压缩，保留目录结构。

**核心脚本:** `D:\work\1_project\nas\video\batch_compress_structure.py`

---

## 快速使用 / Quick Usage

### 1. 预览模式（Dry-run，不写文件）
```powershell
python "D:\work\1_project\nas\video\batch_compress_structure.py" "E:\path\to\videos" --min-size 30 --min-bitrate 12
```

### 2. 执行压缩（后台+日志）
```powershell
python -u "D:\work\1_project\nas\video\batch_compress_structure.py" "E:\path\to\videos" --min-size 30 --min-bitrate 12 --execute > "E:\path\to\videos-compress.log" 2>&1
```

### 3. 指定输出目录（默认为 源目录名 + `-压缩后`）
```powershell
python -u "...\batch_compress_structure.py" "E:\source" --dest "E:\destination" --execute
```

---

## 参数说明 / Parameters

| 参数 | 默认值 | 说明 |
|------|--------|------|
| source_dir | 必填 | 源视频目录（递归扫描） |
| --dest | 源目录 + `-压缩后` | 输出目录 |
| --execute | 否（dry-run）| 加此参数才真正执行 |
| --min-size | 30 MiB | 最小文件体积阈值 |
| --min-bitrate | 12 Mbps | 最小码率阈值 |
| --threads | 3 | FFmpeg CPU 线程数 |

---

## 筛选逻辑 / Filtering Logic

只有同时满足以下两个条件的视频才会被压缩，其余直接复制：
1. 体积 >= min-size MiB
2. 码率 >= min-bitrate Mbps

公式: Bitrate(Mbps) = FileSize(Bytes) * 8 / Duration(sec) / 1,000,000

---

## 安全机制 / Safety Features

| 机制 | 说明 |
|------|------|
| 原版保留 | 原版文件不删除，压缩版写入独立输出目录 |
| 负收益拦截 | 压缩后若体积反增，自动放弃，复制原版 |
| 断点续传 | 输出目录已有同名文件则自动跳过 |
| 时间戳保留 | 压缩成功后，原文件时间戳复制给压缩版 |
| 低优先级 | Windows BELOW_NORMAL_PRIORITY_CLASS，不卡前台 |

⚠️ 重要：脚本不删除原版。确认压缩版质量后，由用户手动删除原版，
切勿自动物理删除（Remove-Item -Force 会绕过回收站）！
建议移入 _待删除/ 文件夹由用户手动确认后清空。

---

## 输出目录结构 / Output Structure

源目录/                          →   源目录-压缩后/
  subdir/
    video_high_bitrate.mov       →     video_high_bitrate.mp4  (压缩版，统一.mp4)
    video_low_bitrate.mp4        →     video_low_bitrate.mp4   (直接复制)
  image.jpg                      →   image.jpg                 (直接复制)

注意：压缩成功的视频统一输出为 .mp4 格式。

---

## 适合压缩的场景 / Best Candidates

| 场景 | 原始编码 | 典型码率 | 预期节省 |
|------|---------|---------|---------|
| 手机短片 iPhone/华为 | HEVC 8-bit | 12~20 Mbps | 30~70% |
| 老摄像机 MJPEG AVI | MJPEG | 30~40 Mbps | 70~90% |
| 屏幕录制/截图视频 | H.264 | 10~20 Mbps | 70~93% |
| 行车记录仪（高速路况）| H.264 | 18~20 Mbps | 差（几乎无收益）|

不适合压缩（建议跳过）：
- iPhone/华为 10-bit HEVC（HDR）— 压缩会丢失10-bit色深
- 本身已是低码率（< 12 Mbps）的视频

---

## 检测 10-bit HEVC / Detect 10-bit Content

用 FFmpeg 检查：
```powershell
$FFMPEG = "D:\Program Files\digiKam\ffmpeg.exe"
& $FFMPEG -hide_banner -i "E:\video.mov" 2>&1 | Where-Object { $_ -match "Video:" }
```
输出含 yuv420p10le 或 yuv420p10 → 10-bit，建议跳过
输出含 yuv420p（不带10）         → 8-bit，可安全压缩

---

## 批量编码信息扫描 / Batch Codec Scan

```powershell
$FFMPEG = "D:\Program Files\digiKam\ffmpeg.exe"
$dir = "E:\your\video\folder"
$videoExts = @('.mp4','.mov','.m4v','.avi','.mkv','.wmv','.flv','.3gp','.ts','.m2ts')

Get-ChildItem -Path $dir -Recurse -File | Where-Object {
    $videoExts -contains $_.Extension.ToLower() -and $_.Length -ge 30MB
} | Sort-Object Length -Descending | ForEach-Object {
    $info = & $FFMPEG -hide_banner -i $_.FullName 2>&1 | Out-String
    $codec    = if ($info -match "Video:\s+(\w+)") { $Matches[1] } else { "?" }
    $bitdepth = if ($info -match "yuv420p10") { "10-bit SKIP" } elseif ($info -match "yuv420p") { "8-bit OK" } else { "?" }
    $vbr      = if ($info -match "Video:.*?(\d+)\s+kb/s") { "$($Matches[1]) kb/s" } else { "?" }
    [PSCustomObject]@{ Name=$_.Name; MB=[math]::Round($_.Length/1MB,1); Codec=$codec; BitDepth=$bitdepth; Bitrate=$vbr }
} | Format-Table -AutoSize
```

---

## 进度监控 / Monitor Progress

```powershell
# 实时查看最新进度
Get-Content "E:\videos-compress.log" -Encoding UTF8 | Select-Object -Last 20

# 完成后查看摘要
Get-Content "E:\videos-compress.log" -Encoding UTF8 | Select-String "SUMMARY|Saved|elapsed"
```

---

## FFmpeg 路径 / FFmpeg Location

D:\Program Files\digiKam\ffmpeg.exe

如路径不同，修改脚本第 16 行:
  FFMPEG = r"D:\Program Files\digiKam\ffmpeg.exe"

---

## 相关文件 / Related Files

| 文件 | 说明 |
|------|------|
| batch_compress_structure.py | 主压缩脚本（目录结构模式）|
| sort_by_duration.py | 按时长分类（1_Clips/2_Records/3_Archives）|
| video_compression_guide.md | 压缩原理、设备对比、跨设备工作流 |
| README.md | 视频整理工作流总览 |
