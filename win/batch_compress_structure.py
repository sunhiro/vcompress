#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
递归压缩一个目标目录下所有子文件夹里的视频（H.265/mp4），保留相对路径。
在目标目录下建立两个子文件夹（默认 .old / .new，前导点使其在资源管理器置顶）：
  - .old : 被压缩文件的【原件】（从原位置移入，保留相对路径）
  - .new : 压缩后的输出（.mp4，保留相对路径）
不需要压缩的文件（体积/码率未达阈值）原封不动留在原目录。
压缩完成后默认在目标目录生成对比报告 .report.csv / .report.xlsx。

示例：mov/a/c/d.mov 被压缩 →
  原件  移到  mov/.old/a/c/d.mov
  压缩片放到 mov/.new/a/c/d.mp4
"""

import os
import sys
import re
import subprocess
import shutil
import time
import argparse

FFMPEG = r"D:\Program Files\digiKam\ffmpeg.exe"
VIDEO_EXT = set("mp4 mov m4v avi mkv wmv flv mpg mpeg 3gp 3g2 ts m2ts mts webm vob ogv f4v asf divx m2v mxf rm rmvb".split())
DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")

def ffdur(path):
    """Probe duration of a video file using ffmpeg."""
    try:
        p = subprocess.run([FFMPEG, "-hide_banner", "-i", path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
        m = DUR_RE.search(p.stderr.decode("utf-8", "replace"))
        if not m:
            return None
        h, mn, s = m.groups()
        return int(h) * 3600 + int(mn) * 60 + float(s)
    except Exception:
        return None

def transcode_video(input_path, output_path, threads=3):
    """
    Transcodes a video to H.265 MP4 using CPU libx265.
    Limits threads to specified count and runs with below normal priority.
    Uses fallback to aac audio encoding if copy fails (e.g. PCM audio).
    """
    # Try copying audio first (extremely fast)
    cmd = [
        FFMPEG, "-y", "-hide_banner",
        "-threads", str(threads),
        "-i", input_path,
        "-c:v", "libx265", "-crf", "23", "-preset", "veryfast",
        "-x265-params", f"pools={threads}",
        "-c:a", "copy",
        output_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=0x00004000)
        if res.returncode == 0:
            return True

        # Fallback: transcode audio to aac (useful for PCM or unsupported audio containers)
        cmd_fallback = [
            FFMPEG, "-y", "-hide_banner",
            "-threads", str(threads),
            "-i", input_path,
            "-c:v", "libx265", "-crf", "23", "-preset", "veryfast",
            "-x265-params", f"pools={threads}",
            "-c:a", "aac", "-b:a", "128k",
            output_path
        ]
        res_fallback = subprocess.run(cmd_fallback, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=0x00004000)
        return res_fallback.returncode == 0
    except Exception as e:
        print(f"Error executing FFmpeg for {os.path.basename(input_path)}: {e}")
        return False

def human_size(n):
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024.0
    return f"{n:.1f} TB"


def main():
    parser = argparse.ArgumentParser(description="递归压缩目录下视频，原件/压缩片分别归入 .old/.new，并出对比报告。")
    parser.add_argument("target_dir", help="目标目录（递归其下所有子文件夹）")
    parser.add_argument("--execute", action="store_true", help="真正转码/移动文件（默认仅预览 dry-run）")
    parser.add_argument("--min-size", type=float, default=15.0, help="判定需压缩的最小体积 MiB（默认 15）")
    parser.add_argument("--min-bitrate", type=float, default=8.0, help="判定需压缩的最小码率 Mbps（默认 8）")
    parser.add_argument("--threads", type=int, default=3, help="FFmpeg 线程数（默认 3）")
    parser.add_argument("--old-dir", default=".old", help="存放【原件】的子目录名（默认 .old）")
    parser.add_argument("--new-dir", default=".new", help="存放【压缩片】的子目录名（默认 .new）")
    parser.add_argument("--no-report", action="store_true", help="压缩后不自动生成 .report 报告")
    parser.add_argument("--quality", action="store_true", help="报告计算 SSIM/PSNR 质量分（慢）")
    parser.add_argument("--quality-sample", type=int, default=0, help="质量评分只分析前 N 秒（0=整片）")

    args = parser.parse_args()

    root = os.path.abspath(args.target_dir)
    if not os.path.exists(root):
        print(f"Error: 目标目录不存在: {root}")
        sys.exit(1)
    if not os.path.exists(FFMPEG):
        print(f"Error: FFmpeg not found at {FFMPEG}")
        sys.exit(1)

    OLD = os.path.join(root, args.old_dir)
    NEW = os.path.join(root, args.new_dir)
    # 遍历时要跳过的目录（含我们自己产出的 .old/.new，避免二次处理）
    IGNORE_DIRS = {args.old_dir, args.new_dir, "#recycle", "@eaDir"}

    print("==================================================")
    print("      Recursive Video Compressor (.old/.new)      ")
    print("==================================================")
    print(f"Target Directory : {root}")
    print(f"原件目录 (.old)  : {OLD}")
    print(f"压缩目录 (.new)  : {NEW}")
    print(f"Mode             : {'EXECUTE' if args.execute else 'DRY-RUN (Preview)'}")
    print(f"Threads          : {args.threads}")
    print(f"Rules            : Size >= {args.min_size} MiB AND Bitrate >= {args.min_bitrate} Mbps")
    print("==================================================\n")

    # ── 递归收集视频文件（跳过 .old/.new 等）──
    all_videos = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith("@eaDir")]
        for f in files:
            if f.startswith("._") or f == ".DS_Store":
                continue
            ext = os.path.splitext(f)[1][1:].lower()
            if ext in VIDEO_EXT:
                all_videos.append(os.path.join(cur, f))

    print(f"递归发现视频文件: {len(all_videos)} 个")

    # ── 判定哪些需要压缩 ──
    compress_plan = []   # (fpath, size, dur, rate)
    print("扫描中（探测时长/码率）...")
    for fpath in all_videos:
        try:
            size = os.path.getsize(fpath)
        except OSError:
            continue
        dur = ffdur(fpath)
        rate = (size * 8) / (dur * 1_000_000) if (dur and dur > 0) else 0.0
        if dur and dur > 0 and size >= args.min_size * 1024 * 1024 and rate >= args.min_bitrate:
            compress_plan.append((fpath, size, dur, rate))

    plan_bytes = sum(x[1] for x in compress_plan)
    print(f"\n扫描完成：")
    print(f"  需要压缩 : {len(compress_plan)} 个, {human_size(plan_bytes)}")
    print(f"  保持原样 : {len(all_videos) - len(compress_plan)} 个（留在原目录）")

    if not args.execute:
        print("\nDRY-RUN 预览，未改动任何文件。加 --execute 开始真正压缩。")
        if compress_plan:
            print("\n将被压缩的文件（前 20 个）：")
            for fpath, size, dur, rate in compress_plan[:20]:
                print(f"  {os.path.relpath(fpath, root)}  ({human_size(size)}, {rate:.1f} Mbps)")
        return

    # ── 执行 ──
    os.makedirs(OLD, exist_ok=True)
    os.makedirs(NEW, exist_ok=True)
    temp_dir = os.path.join(NEW, ".temp_work")
    os.makedirs(temp_dir, exist_ok=True)

    print("\n------------------ 压缩中 ------------------")
    success_count = skipped_count = failed_count = 0
    total_saved_bytes = 0
    start_time = time.time()

    for idx, (fpath, size, dur, rate) in enumerate(compress_plan):
        rel = os.path.relpath(fpath, root)
        old_target = os.path.join(OLD, rel)                                  # 原件（原扩展名）
        new_target = os.path.join(NEW, os.path.splitext(rel)[0] + ".mp4")    # 压缩片（.mp4）
        print(f"[{idx+1}/{len(compress_plan)}] {rel} ({human_size(size)}, {rate:.1f} Mbps)")

        # 增量跳过：压缩片已存在
        if os.path.exists(new_target) and os.path.getsize(new_target) > 0:
            # 若原件还在原位（上次中断），补移进 .old
            if os.path.exists(fpath) and not os.path.exists(old_target):
                os.makedirs(os.path.dirname(old_target), exist_ok=True)
                shutil.move(fpath, old_target)
            print("  -> 跳过（已处理）")
            success_count += 1
            print("-" * 50)
            continue

        temp_out = os.path.join(temp_dir, f"temp_{int(time.time())}_{os.path.basename(new_target)}")
        ok = transcode_video(fpath, temp_out, args.threads)

        if ok and os.path.exists(temp_out):
            comp_size = os.path.getsize(temp_out)
            if comp_size < size:
                saved = size - comp_size
                total_saved_bytes += saved
                shutil.copystat(fpath, temp_out)          # 保留原始时间戳
                os.makedirs(os.path.dirname(new_target), exist_ok=True)
                os.makedirs(os.path.dirname(old_target), exist_ok=True)
                shutil.move(temp_out, new_target)         # 压缩片 -> .new
                shutil.move(fpath, old_target)            # 原件   -> .old
                print(f"  -> 成功: {human_size(comp_size)} (省 {human_size(saved)}, {saved/size*100:.1f}%)")
                success_count += 1
            else:
                print(f"  -> 跳过（无收益）: {human_size(comp_size)} >= {human_size(size)}，原件留在原处")
                os.remove(temp_out)
                skipped_count += 1
        else:
            print(f"  -> 失败：原件留在原处")
            if os.path.exists(temp_out):
                os.remove(temp_out)
            failed_count += 1
        print("-" * 50)

    try:
        shutil.rmtree(temp_dir)
    except Exception:
        pass

    elapsed = time.time() - start_time
    print("\n==================== 压缩摘要 ====================")
    print(f"耗时              : {elapsed/60:.2f} 分钟")
    print(f"成功压缩          : {success_count} 个")
    print(f"跳过（无收益）    : {skipped_count} 个")
    print(f"失败              : {failed_count} 个")
    print(f"共节省            : {human_size(total_saved_bytes)}")
    print(f"原件目录 (.old)   : {OLD}")
    print(f"压缩目录 (.new)   : {NEW}")
    print("=================================================")

    # ── 默认生成对比报告（.old vs .new）──
    if not args.no_report and success_count > 0 and os.path.isdir(OLD):
        cmp_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "compare_compressed.py")
        report_csv = os.path.join(root, ".report.csv")
        cmd = [sys.executable, cmp_script, OLD, "--compressed", NEW, "--out", report_csv]
        if args.quality:
            cmd += ["--quality"]
            if args.quality_sample:
                cmd += ["--quality-sample", str(args.quality_sample)]
        print("\n>>> 生成对比报告 (.report.csv / .report.xlsx):")
        subprocess.run(cmd)


if __name__ == "__main__":
    main()
