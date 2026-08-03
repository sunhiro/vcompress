#!/usr/bin/env python3
"""
vcompress_abav1.py - 基于 Rust 后端 ab-av1 的高级择优视频压缩与 VMAF 质量搜索引擎
支持多线程高性能测算 (--vmaf-threads 6，预算约 8GB RAM)，自动生成台账文件 (_report.csv)。
使用 ab-av1 crf-search 二分查找最佳 CRF，全量转码采用原生 FFmpeg 隔离 Apple mebx 数据轨。
"""

import os
import sys
import subprocess
import shutil
import argparse
import json
import logging
import csv
from pathlib import Path

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("vcompress_abav1.log", mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger("vcompress_abav1")

SUPPORTED_EXTENSIONS = {
    '.mp4', '.mov', '.m4v', '.avi', '.mkv', '.mts', '.m2ts',
    '.3gp', '.wmv', '.flv', '.webm', '.vob', '.mpg', '.rmvb'
}

def check_dependencies():
    """检查系统环境依赖 binary：ab-av1, ffmpeg, ffprobe, exiftool"""
    missing = []
    for binary in ['ab-av1', 'ffmpeg', 'ffprobe', 'exiftool']:
        if shutil.which(binary) is None:
            missing.append(binary)
    if missing:
        logger.error(f"缺少必要依赖工具: {', '.join(missing)}")
        if 'ab-av1' in missing:
            logger.error("请使用 Homebrew 安装 ab-av1: brew install ab-av1")
        if 'exiftool' in missing:
            logger.error("请使用 Homebrew 安装 exiftool: brew install exiftool")
        sys.exit(1)

def get_video_metadata(file_path):
    """提取视频基础元数据：编码格式、高度、时长、码率、像素格式"""
    try:
        probe_cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'stream=codec_name,height,width,pix_fmt',
            '-show_entries', 'format=duration,bit_rate',
            '-of', 'json',
            str(file_path)
        ]
        probe_output = subprocess.check_output(probe_cmd, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        data = json.loads(probe_output.decode('utf-8'))

        codec = None
        height = 0
        pix_fmt = ""
        if 'streams' in data and len(data['streams']) > 0:
            stream = data['streams'][0]
            codec = stream.get('codec_name')
            height = int(stream.get('height', 0))
            pix_fmt = stream.get('pix_fmt', '')

        duration = 0.0
        bit_rate = 0
        if 'format' in data:
            fmt = data['format']
            try:
                duration = float(fmt.get('duration', 0.0))
            except ValueError:
                duration = 0.0
            try:
                bit_rate = int(fmt.get('bit_rate', 0))
            except ValueError:
                bit_rate = 0

        return codec, height, duration, bit_rate, pix_fmt
    except Exception as e:
        logger.error(f"解析视频元数据失败 {file_path}: {e}")
        return None, 0, 0.0, 0, ""

def preserve_metadata(source, target):
    """复制拍摄时间与 EXIF 标签，保持文件属性一致"""
    try:
        exif_cmd = [
            'exiftool', '-overwrite_original',
            '-tagsFromFile', str(source),
            '-all:all', '-unsafe',
            '-FileCreateDate', '-FileModifyDate',
            str(target)
        ]
        subprocess.run(exif_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        shutil.copystat(source, target)
        return True
    except Exception as e:
        logger.error(f"保存元数据失败: {e}")
        return False

def map_encoder_flag(encoder):
    """映射 vcompress 编码器命名到 ffmpeg / ab-av1 编码器标志"""
    if encoder == 'vt':
        return 'hevc_videotoolbox'
    elif encoder == 'vaapi':
        return 'hevc_vaapi'
    elif encoder == 'svt-av1':
        return 'libsvtav1'
    elif encoder == 'x265':
        return 'libx265'
    elif encoder == 'x264':
        return 'libx264'
    return encoder

def run_ab_av1_search(input_path, encoder_name, min_vmaf, max_percent, sample_sec=10, samples_cnt=3, vmaf_threads=6, scale_1080p=True):
    """使用 ab-av1 crf-search 二分搜寻最佳 CRF/QP"""
    enc_flag = map_encoder_flag(encoder_name)
    cmd = [
        'ab-av1', 'crf-search',
        '-i', str(input_path),
        '--encoder', enc_flag,
        '--min-vmaf', str(min_vmaf),
        '--max-encoded-percent', str(max_percent),
        '--sample-duration', f"{sample_sec}s",
        '--samples', str(samples_cnt),
        '--temp-dir', '/tmp',
        '--vmaf', f"n_threads={vmaf_threads}",
        '--stdout-format', 'json'
    ]

    if scale_1080p:
        cmd.extend(['--vmaf-scale', '1920x1080'])

    if enc_flag == 'hevc_videotoolbox':
        cmd.extend(['--high-crf-means-hq', 'true'])

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        lines = [line.strip() for line in res.stdout.strip().splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                data = json.loads(line)
                if "crf" in data:
                    return data
            except Exception:
                continue
        logger.error(f"无法找到有效的 ab-av1 JSON 结果行，输出为:\n{res.stdout}")
        return None
    except subprocess.CalledProcessError as e:
        logger.error(f"ab-av1 crf-search 失败: {e.stderr}")
        return None
    except Exception as e:
        logger.error(f"解析 ab-av1 JSON 失败: {e}")
        return None

def run_ab_av1_encode(input_path, output_path, encoder_name, crf_val, preset=None):
    """使用原生 FFmpeg 进行全量转码，隔离 Apple mebx 沉浸数据轨"""
    enc_flag = map_encoder_flag(encoder_name)
    cmd = [
        'ffmpeg', '-y',
        '-i', str(input_path),
        '-map', '0:v:0',
        '-map', '0:a?',
        '-c:v', enc_flag,
        '-c:a', 'copy',
        '-movflags', '+faststart'
    ]
    if enc_flag == 'hevc_videotoolbox':
        cmd.extend(['-q:v', str(int(crf_val)), '-tag:v', 'hvc1'])
    elif enc_flag == 'libx265':
        cmd.extend(['-crf', str(crf_val), '-tag:v', 'hvc1'])
    elif enc_flag == 'libsvtav1':
        cmd.extend(['-crf', str(crf_val)])
    elif enc_flag == 'libx264':
        cmd.extend(['-crf', str(crf_val)])
    else:
        cmd.extend(['-q:v', str(crf_val)])

    if preset:
        cmd.extend(['-preset', str(preset)])

    cmd.append(str(output_path))

    try:
        process = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if process.returncode != 0:
            logger.error(f"FFmpeg 编码失败: {process.stderr.decode('utf-8', errors='ignore')}")
            return False
        return True
    except Exception as e:
        logger.error(f"执行 FFmpeg 转码异常: {e}")
        return False

def process_file_abav1(src_path, out_path, args):
    """单文件处理逻辑：智能规则择优过滤 -> 评估 -> 编码 -> 元数据复制"""
    orig_bytes = src_path.stat().st_size
    orig_mb = orig_bytes / (1024 * 1024)

    # 1. 跳过小文件
    if orig_mb < args.min_file_mb:
        logger.info(f"   [跳过: 低性价比] 文件大小 {orig_mb:.1f}MB < 门限 {args.min_file_mb:.1f}MB。")
        return "skipped_small_file", orig_mb, 0.0, 0.0, "NA", "NA", f"小文件(跳过 <{args.min_file_mb:.0f}MB)", ""

    codec, height, duration, bit_rate, pix_fmt = get_video_metadata(src_path)
    if not codec:
        logger.warning(f"   [跳过] 无法读取元数据: {src_path}")
        return "failed", orig_mb, 0.0, 0.0, "NA", "NA", "无元数据(跳过)", pix_fmt

    bitrate_mbps = bit_rate / 1000000.0
    logger.info(f"   原文件信息: 编码={codec}, 分辨率={height}p, 像素格式={pix_fmt}, 时长={duration:.1f}s, 码率={bitrate_mbps:.2f}Mbps")

    # 2. 择优规则过滤 1：跳过已是低码率 HEVC (码率 < max_hevc_bitrate，默认 30Mbps)
    if codec == 'hevc' and bitrate_mbps <= args.max_hevc_bitrate:
        logger.info(f"   [跳过: 已高效压缩] HEVC 码率 {bitrate_mbps:.1f}Mbps <= 门限 {args.max_hevc_bitrate:.1f}Mbps (无需二次重压)。")
        return "skipped_low_hevc", orig_mb, 0.0, 0.0, "NA", "NA", "已是高效HEVC(跳过)", pix_fmt

    # 3. 择优规则过滤 2：跳过 10-bit HDR / 高色彩深度视频 (耗时长、性价比低)
    if args.skip_hdr and ('10' in pix_fmt or 'p10' in pix_fmt or '12' in pix_fmt):
        logger.info(f"   [跳过: 10-bit HDR] 像素格式 {pix_fmt} 为 10-bit/12-bit，转码耗时长性价比低，跳过。")
        return "skipped_hdr", orig_mb, 0.0, 0.0, "NA", "NA", "10-bit HDR(跳过)", pix_fmt

    # 4. 择优规则过滤 3：跳过本身码率极低的视频
    if bitrate_mbps < args.min_bitrate:
        logger.info(f"   [跳过: 极低码率] 视频码率 {bitrate_mbps:.2f}Mbps < 最低限制 {args.min_bitrate:.2f}Mbps，原样保留。")
        return "skipped_low_bitrate", orig_mb, 0.0, 0.0, "NA", "NA", "极低码率(跳过)", pix_fmt

    # 5. 运行 ab-av1 二分查找测算最佳 CRF/QP
    logger.info(f"   [ab-av1 二分寻找] 目标 VMAF >= {args.vmaf_min}, 最低空间节省率 >= {args.min_saving_pct}%, VMAF线程={args.vmaf_threads}...")
    max_percent = 100.0 - args.min_saving_pct

    search_res = run_ab_av1_search(
        src_path, args.encoder,
        min_vmaf=args.vmaf_min,
        max_percent=max_percent,
        sample_sec=args.sample_sec,
        samples_cnt=args.samples,
        vmaf_threads=args.vmaf_threads,
        scale_1080p=args.vmaf_scale_1080p
    )

    if not search_res:
        logger.warning("   [拦截/跳过] ab-av1 质量搜索未找到达标 CRF (无法兼顾 VMAF 93 分与 20% 空间节省)。")
        return "failed", orig_mb, 0.0, 0.0, "NA", "NA", f"未过质量门限(VMAF<{args.vmaf_min})", pix_fmt

    best_crf = search_res.get("crf")
    best_vmaf = round(search_res.get("vmaf", 0.0), 2)
    pred_percent = search_res.get("predicted_encode_percent", 100.0)
    pred_saving = round(100.0 - pred_percent, 1)
    pred_size_mb = search_res.get("predicted_encode_size", 0) / (1024 * 1024)

    logger.info(f"   ab-av1 搜寻成功: 最佳 Quality/CRF={best_crf} | VMAF={best_vmaf} | 预估省 {pred_saving}% | 预估产物大小 {pred_size_mb:.1f}MB")

    if args.preview:
        logger.info("   ✅ [PREVIEW 成功] （Preview 模式试压测算完毕，不保存文件）。")
        return "success", orig_mb, pred_size_mb, pred_saving, best_vmaf, best_crf, "PREVIEW成功", pix_fmt

    # 6. 执行全量转码
    logger.info(f"   [全量转码中] 目标文件: {out_path}...")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ok = run_ab_av1_encode(src_path, out_path, args.encoder, best_crf, args.preset)
    if not ok or not out_path.exists():
        logger.error("   [失败] 全量编码异常终止。")
        return "failed", orig_mb, 0.0, 0.0, best_vmaf, best_crf, "编码失败", pix_fmt

    new_bytes = out_path.stat().st_size
    new_mb = new_bytes / (1024 * 1024)
    actual_saving = round(((orig_bytes - new_bytes) / orig_bytes) * 100, 1)

    preserve_metadata(src_path, out_path)
    logger.info(f"   ✅ 转码成功! 原始 {orig_mb:.1f}MB ➔ 压缩后 {new_mb:.1f}MB (实际节省 {actual_saving}%)")

    return "success", orig_mb, new_mb, actual_saving, best_vmaf, best_crf, "保留压缩版", pix_fmt

def main():
    check_dependencies()
    parser = argparse.ArgumentParser(description="vcompress_abav1 - 高性价比择优视频压缩与 VMAF 质量搜索引擎 (支持多线程高性能模式及台账自动导出)")
    parser.add_argument('-i', '--input', required=True, type=Path, help='输入文件或源目录')
    parser.add_argument('-o', '--output', required=True, type=Path, help='输出文件或目标目录')
    parser.add_argument('-e', '--encoder', choices=['vt', 'svt-av1', 'x265', 'vaapi', 'x264'], default='vt', help='编码引擎 (vt=Mac硬件, svt-av1=AV1, x265=H.265, vaapi=Intel核显)')
    parser.add_argument('--vmaf-min', type=float, default=93.0, help='最低允许 VMAF 画质门限 (默认 93.0)')
    parser.add_argument('--min-saving-pct', type=float, default=20.0, help='最低空间节省率比例 (默认 20.0%%)')
    parser.add_argument('--min-file-mb', type=float, default=20.0, help='跳过压缩的小文件阈值 MB (默认 20.0MB)')
    parser.add_argument('--max-hevc-bitrate', type=float, default=30.0, help='HEVC 视频最大跳过码率 Mbps，低于此码率的 HEVC 不再重压 (默认 30.0Mbps)')
    parser.add_argument('--min-bitrate', type=float, default=2.5, help='视频最低码率 Mbps，低于此码率视频跳过重压 (默认 2.5Mbps)')
    parser.add_argument('--skip-hdr', action='store_true', default=True, help='自动跳过 10-bit / HDR 视频以保持极高性价比 (默认开启)')
    parser.add_argument('--vmaf-threads', type=int, default=6, help='VMAF 计算允许的最大线程数 (默认 6，预算 ~8GB RAM)')
    parser.add_argument('--no-vmaf-scale', dest='vmaf_scale_1080p', action='store_false', help='关闭 VMAF 计算时的 1080p 缩放 (默认开启 1080p 缩放)')
    parser.set_defaults(vmaf_scale_1080p=True)
    parser.add_argument('--sample-sec', type=int, default=15, help='每个采样切片的秒数 (默认 15s)')
    parser.add_argument('--samples', type=int, default=3, help='采样切片数量 (默认 3 段)')
    parser.add_argument('--preset', help='编码器 Preset 预设')
    parser.add_argument('--preview', action='store_true', help='Preview 模式: 仅做二分法测算评估，不输出全量转码文件')

    args = parser.parse_args()

    if args.input.is_file():
        files = [args.input]
        base_dir = args.input.parent
    else:
        files = [p for p in args.input.rglob('*') if p.suffix.lower() in SUPPORTED_EXTENSIONS]
        base_dir = args.input

    out_dir = args.output if args.output.is_dir() or not args.output.suffix else args.output.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    report_csv = out_dir / "_report.csv"

    if not report_csv.exists():
        with open(report_csv, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['文件', '原MB', '新MB', '省%', 'VMAF', 'CRF/Q', '决定', '像素格式'])

    logger.info(f"启动多线程择优压缩流程，源目录: {args.input} ➔ 目标目录: {args.output}")
    logger.info(f"待扫描视频文件总数: {len(files)} 个。内存优化: VMAF线程={args.vmaf_threads} (预算 ~8GB RAM) | 小文件门限={args.min_file_mb}MB")

    success_cnt, skipped_cnt, failed_cnt = 0, 0, 0
    total_saved_mb = 0.0

    for idx, file_path in enumerate(files, 1):
        rel_path = file_path.relative_to(base_dir) if args.input.is_dir() else file_path.name
        out_path = args.output / rel_path if args.input.is_dir() else args.output

        logger.info(f"\n[{idx}/{len(files)}] 检查中: {rel_path}")
        status, orig_mb, new_mb, saving, vmaf, crf_val, decision, pix_fmt = process_file_abav1(file_path, out_path, args)

        # 写入台账 CSV
        rel_csv_str = str(rel_path).replace(',', ' ')
        with open(report_csv, 'a', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([rel_csv_str, f"{orig_mb:.2f}", f"{new_mb:.2f}", f"{saving:.1f}", str(vmaf), str(crf_val), decision, pix_fmt])

        if status == "success":
            success_cnt += 1
            saved = orig_mb - new_mb
            total_saved_mb += saved
        elif status.startswith("skipped"):
            skipped_cnt += 1
        else:
            failed_cnt += 1

    logger.info("\n" + "="*60)
    logger.info(f"择优压缩任务完成: 高性价比压缩成功 {success_cnt} 个, 低性价比/已压缩跳过 {skipped_cnt} 个, 拦截/失败 {failed_cnt} 个")
    logger.info(f"累计释放磁盘空间: {total_saved_mb/1024:.2f} GB ({total_saved_mb:.1f} MB)")
    logger.info(f"结构化压缩台账文件已保存至: {report_csv}")
    logger.info("="*60)

if __name__ == "__main__":
    main()
