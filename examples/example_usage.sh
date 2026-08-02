#!/usr/bin/env bash
# vcompress 常用场景示例脚本

SCRIPT_PATH="../src/vcompress.py"

echo "=== 场景 1: 对外接移动硬盘单个文件进行画质测分与压缩 ==="
python3 "$SCRIPT_PATH" \
  -i "/Volumes/T7/DCIM/IMG_9999.MOV" \
  -o "/Volumes/T7/Compressed/IMG_9999.mp4" \
  --encoder vt \
  --vmaf-min 92.0

echo "=== 场景 2: 批量处理移动硬盘整个目录（只压大视频且保持92分以上） ==="
python3 "$SCRIPT_PATH" \
  -i "/Volumes/T7/PhotosAndVideos" \
  -o "/Volumes/T7/Compressed_Output" \
  --encoder vt \
  --vt-quality 65 \
  --vmaf-min 92.0 \
  --min-saving-pct 20.0 \
  --min-file-mb 100.0

echo "=== 场景 3: 快速转码模式（跳过 VMAF 计算以获得最高速度） ==="
python3 "$SCRIPT_PATH" \
  -i "/Volumes/T7/QuickProcess" \
  -o "/Volumes/T7/QuickOutput" \
  --encoder vt \
  --no-vmaf
