#!/usr/bin/env python3
"""
vcompress - 硬件加速与 VMAF 质量门限保障的专业视频压缩引擎
支持 Apple Silicon (hevc_videotoolbox) 与 x265 转码，具备帧对齐 VMAF 质量评估与自动回滚。
"""

import os
import sys
import subprocess
import shutil
import argparse
import json
import logging
import tempfile
from pathlib import Path

# 设置日志格式与日志文件记录
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("vcompress.log", mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger("vcompress")

SUPPORTED_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.avi', '.mkv', '.mts', '.m2ts', '.3gp', '.wmv', '.flv', '.webm'}

def check_dependencies():
    """检查系统环境依赖 binary：ffmpeg, ffprobe, exiftool"""
    missing = []
    for binary in ['ffmpeg', 'ffprobe', 'exiftool']:
        if shutil.which(binary) is None:
            missing.append(binary)
    if missing:
        logger.error(f"缺少必要依赖工具: {', '.join(missing)}")
        if 'exiftool' in missing:
            logger.error("请使用 Homebrew 安装: brew install ffmpeg exiftool")
        sys.exit(1)

def find_vmaf_models():
    """查找本地 libvmaf 模型的路径 (macOS Homebrew 常见默认路径)"""
    search_paths = [
        Path('/opt/homebrew'),
        Path('/usr/local')
    ]
    model_dir = None
    for sp in search_paths:
        if not sp.exists():
            continue
        for path in sp.glob('**/vmaf_v0.6.1.json'):
            model_dir = path.parent
            break
        if model_dir:
            break
            
    if not model_dir:
        model_dir = Path('/opt/homebrew/share/model')
        
    model_hd = model_dir / 'vmaf_v0.6.1.json'
    model_4k = model_dir / 'vmaf_4k_v0.6.1.json'
    return model_hd, model_4k

def get_video_metadata(file_path):
    """提取视频基础元数据：编码格式、高度、时长、码率"""
    try:
        probe_cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'stream=codec_name,height,width',
            '-show_entries', 'format=duration,bit_rate',
            '-of', 'json',
            str(file_path)
        ]
        probe_output = subprocess.check_output(probe_cmd, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        data = json.loads(probe_output.decode('utf-8'))
        
        codec = None
        height = 0
        if 'streams' in data and len(data['streams']) > 0:
            stream = data['streams'][0]
            codec = stream.get('codec_name')
            height = int(stream.get('height', 0))
            
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
                
        return codec, height, duration, bit_rate
    except Exception as e:
        logger.error(f"解析视频元数据失败 {file_path}: {e}")
        return None, 0, 0.0, 0

def encode_video(input_path, output_path, encoder, q_val, preset, audio_mode="copy", vt_bitrate=None, clip=None):
    """
    编码视频：支持 VideoToolbox 硬件加速或 x265 软解。
    clip=(ss, dur) 用于提取部分片段单独测试。
    """
    if encoder == 'vt':
        cmd = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-i', str(input_path),
            '-map', '0:v:0', '-map', '0:a?',
            '-c:v', 'hevc_videotoolbox'
        ]
        if vt_bitrate:
            bitrate_str = f"{int(vt_bitrate * 1000000)}"
            maxrate_str = f"{int(vt_bitrate * 1200000)}"
            bufsize_str = f"{int(vt_bitrate * 2000000)}"
            cmd.extend(['-b:v', bitrate_str, '-maxrate', maxrate_str, '-bufsize', bufsize_str])
        else:
            cmd.extend(['-q:v', str(q_val)])
            
        cmd.extend([
            '-tag:v', 'hvc1',
            '-c:a', audio_mode,
            '-map_metadata', '0',
            '-movflags', '+faststart+use_metadata_tags',
            str(output_path)
        ])
    else:  # x265
        cmd = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-i', str(input_path),
            '-map', '0:v:0', '-map', '0:a?',
            '-c:v', 'libx265',
            '-preset', preset,
            '-crf', str(q_val),
            '-tag:v', 'hvc1',
            '-c:a', audio_mode,
            '-map_metadata', '0',
            '-movflags', '+faststart+use_metadata_tags',
            str(output_path)
        ]

    if clip:
        i = cmd.index('-i')
        cmd[i + 2:i + 2] = ['-ss', f"{clip[0]:.2f}", '-t', f"{clip[1]:.2f}"]

    process = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if process.returncode != 0:
        logger.error(f"FFmpeg 编码失败 ({process.returncode}): {process.stderr.decode('utf-8', errors='ignore')}")
    return process.returncode == 0

def compute_vmaf_sample(distorted_clip, reference, height, clip_ss, clip_dur, model_hd, model_4k, target_fps=30):
    """
    计算给定采样片段的 VMAF 画质得分。
    彻底解决串帧的核心：在滤镜图中加入 fps=target_fps 重采样与 format=yuv420p 色彩格式规范化。
    """
    version = "vmaf_4k_v0.6.1" if height >= 2000 else "vmaf_v0.6.1"
    nthreads = os.cpu_count() or 4
    end = clip_ss + clip_dur

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        vlog_path = tf.name

    # 关键滤镜链：trim 时间切片 -> setpts 时间戳归零 -> fps 强制帧率重采样 -> format 统一色彩
    filtergraph = (
        f"[1:v]trim=start={clip_ss:.2f}:end={end:.2f},setpts=PTS-STARTPTS,fps={target_fps},format=yuv420p[r];"
        f"[0:v]setpts=PTS-STARTPTS,fps={target_fps},format=yuv420p[d];"
        f"[d][r]libvmaf=model=version={version}:n_threads={nthreads}:log_path={vlog_path}:log_fmt=json"
    )
    
    vmaf_cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-i', str(distorted_clip),
        '-i', str(reference),
        '-filter_complex', filtergraph,
        '-f', 'null', '-'
    ]
    
    try:
        process = subprocess.run(vmaf_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if process.returncode != 0:
            logger.error(f"VMAF 计算失败: {process.stderr.decode('utf-8', errors='ignore')}")
            return "NA"
        with open(vlog_path, 'r', encoding='utf-8') as f:
            vmaf_data = json.load(f)
        if "pooled_metrics" in vmaf_data and "vmaf" in vmaf_data["pooled_metrics"]:
            return round(vmaf_data["pooled_metrics"]["vmaf"]["mean"], 2)
        return "NA"
    except Exception as e:
        logger.error(f"解析 VMAF 评估结果异常: {e}")
        return "NA"
    finally:
        if os.path.exists(vlog_path):
            os.unlink(vlog_path)

def measure_quality_windowed(src_path, encoder, q_val, preset, audio, vt_bitrate,
                             duration, height, model_hd, model_4k, temp_dir):
    """
    多窗口采样测量 (Beginning / Middle / End 15s 片段)。
    对各自片段单独编码并与原片逐帧精确对齐比对 VMAF，返回最低得分与预估节省比例。
    """
    seg = 15.0
    if duration >= (seg * 3 + 5):
        windows = [("Beginning", 5.0),
                   ("Middle", duration / 2.0 - seg / 2.0),
                   ("End", duration - seg - 5.0)]
        wlen = seg
    elif duration >= 8:
        windows = [("Middle", max(0.0, duration / 2.0 - seg / 2.0))]
        wlen = min(seg, duration)
    else:
        windows = [("Whole", 0.0)]
        wlen = duration

    orig_bytes = src_path.stat().st_size
    scores = []
    comp_bytes = 0
    win_secs = 0.0

    for name, ss in windows:
        wd = min(wlen, max(0.0, duration - ss))
        if wd < 1.0:
            continue
        with tempfile.NamedTemporaryFile(dir=temp_dir, suffix=".mp4", delete=False) as tf:
            wtmp = Path(tf.name)
        try:
            ok = encode_video(src_path, wtmp, encoder, q_val, preset, audio,
                              vt_bitrate=vt_bitrate, clip=(ss, wd))
            if not ok or not wtmp.exists():
                logger.warning(f"      窗口 {name}@{ss:.0f}s 编码失败")
                continue
            comp_bytes += wtmp.stat().st_size
            win_secs += wd
            v = compute_vmaf_sample(wtmp, src_path, height, ss, wd, model_hd, model_4k)
            if v != "NA":
                scores.append(v)
                logger.info(f"      窗口 {name}@{ss:.0f}s VMAF 得分: {v}")
            else:
                logger.warning(f"      窗口 {name}@{ss:.0f}s VMAF 计算失败")
        finally:
            if wtmp.exists():
                wtmp.unlink()

    if not scores:
        return "NA", 0.0, 0.0, 0

    min_v = round(min(scores), 2)
    orig_win_bytes = orig_bytes * (win_secs / duration) if duration > 0 else orig_bytes
    saving = ((orig_win_bytes - comp_bytes) / orig_win_bytes) * 100 if orig_win_bytes > 0 else 0.0
    est_new_mb = (orig_bytes / (1024 * 1024)) * (1 - saving / 100.0)
    return min_v, saving, est_new_mb, len(scores)

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

def process_file(src_path, out_path, args, model_hd, model_4k):
    """处理单个文件：体积过滤 -> 窗口质量评估 -> 校验通过后全片编码 -> 元数据保存"""
    orig_bytes = src_path.stat().st_size
    orig_mb = orig_bytes / (1024 * 1024)

    if orig_mb < args.min_file_mb:
        logger.info(f"   [跳过] 文件体积 {orig_mb:.1f}MB < 门限 {args.min_file_mb:.1f}MB，原样保留。")
        return "skipped_small_file", orig_mb, 0.0, 0.0, "NA", "NA"

    codec, height, duration, bit_rate = get_video_metadata(src_path)
    if not codec:
        logger.warning(f"   [跳过] 无法获取元数据: {src_path}")
        return "failed", orig_mb, 0.0, 0.0, "NA", "NA"

    logger.info(f"   原文件元数据: 编码={codec}, 分辨率高度={height}p, 时长={duration:.1f}s, 码率={bit_rate/1000000:.2f}Mbps")

    # 预设参数逻辑
    if args.encoder == 'vt':
        q_val = args.vt_quality
        preset = 'medium'
        vt_bitrate = None
        q_label = "VT-Quality"
    else:
        q_val = args.crf
        preset = args.preset
        vt_bitrate = None
        q_label = "x265-CRF"

    # 无 VMAF 测分直接转码
    if args.no_vmaf:
        best_vmaf = "SKIPPED"
        logger.info(f"   [--no-vmaf] 跳过质量测分，直接全量转码中...")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ok = encode_video(src_path, out_path, args.encoder, q_val, preset, audio_mode="copy")
        if not ok:
            return "failed", orig_mb, 0.0, 0.0, best_vmaf, q_val

        new_bytes = out_path.stat().st_size
        new_mb = new_bytes / (1024 * 1024)
        saving = ((orig_bytes - new_bytes) / orig_bytes) * 100

        if saving < args.min_saving_pct:
            logger.info(f"   [跳过] 压缩体积削减未达标 (省{saving:.1f}% < {args.min_saving_pct}%)，丢弃转码结果。")
            if out_path.exists():
                out_path.unlink()
            return "failed", orig_mb, 0.0, saving, best_vmaf, q_val

        preserve_metadata(src_path, out_path)
        return "success", orig_mb, new_mb, saving, best_vmaf, q_val

    # VMAF 窗口质量预估门限校验
    with tempfile.TemporaryDirectory(prefix="vcompress_") as temp_dir:
        vmaf, saving, est_new_mb, nwin = measure_quality_windowed(
            src_path, args.encoder, q_val, preset, "copy", vt_bitrate,
            duration, height, model_hd, model_4k, temp_dir
        )

        logger.info(f"   评估结果 [{q_label}={q_val}]: VMAF(最低)={vmaf} | 预估体积省 {saving:.1f}% | 预期大小 {est_new_mb:.1f}MB")

        if vmaf == "NA" or vmaf < args.vmaf_min or saving < args.min_saving_pct:
            logger.info(f"   [拦截] VMAF ({vmaf}) 低于阈值 ({args.vmaf_min}) 或 空间节省率 ({saving:.1f}%) 不达标，放弃全片转码。")
            return "failed", orig_mb, 0.0, saving, vmaf, q_val

        # 质量与节省率均通过，执行全片转码
        logger.info(f"   [通过] 质量符合要求，开始执行全片转码...")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ok = encode_video(src_path, out_path, args.encoder, q_val, preset, audio_mode="copy")
        if not ok:
            return "failed", orig_mb, 0.0, 0.0, vmaf, q_val

        new_bytes = out_path.stat().st_size
        new_mb = new_bytes / (1024 * 1024)
        actual_saving = ((orig_bytes - new_bytes) / orig_bytes) * 100
        preserve_metadata(src_path, out_path)

        return "success", orig_mb, new_mb, actual_saving, vmaf, q_val

def main():
    check_dependencies()
    parser = argparse.ArgumentParser(description="vcompress - 具备 VMAF 质量检测保护的智能视频压缩工具")
    parser.add_argument('-i', '--input', required=True, type=Path, help='输入文件或目录路径')
    parser.add_argument('-o', '--output', required=True, type=Path, help='输出文件或目录路径')
    parser.add_argument('-e', '--encoder', choices=['vt', 'x265'], default='vt', help='编码引擎: vt (Apple Hardware) 或 x265 (CPU 软解)')
    parser.add_argument('--vt-quality', type=int, default=65, help='VideoToolbox 质量系数 (默认 65)')
    parser.add_argument('--crf', type=int, default=22, help='x265 CRF 质量值 (默认 22)')
    parser.add_argument('--preset', default='medium', help='x265 预设速度 (medium, slow, etc.)')
    parser.add_argument('--vmaf-min', type=float, default=92.0, help='VMAF 最低画质门限 (默认 92.0)')
    parser.add_argument('--min-saving-pct', type=float, default=20.0, help='最低省空间比例 (默认 20.0%%)')
    parser.add_argument('--min-file-mb', type=float, default=100.0, help='跳过压缩的小文件下限 (默认 100.0MB)')
    parser.add_argument('--no-vmaf', action='store_true', help='跳过 VMAF 采样检测，直接全速编码')

    args = parser.parse_args()

    model_hd, model_4k = find_vmaf_models()
    logger.info(f"使用 VMAF 模型: HD={model_hd.name}, 4K={model_4k.name}")

    if args.input.is_file():
        files = [args.input]
        base_dir = args.input.parent
    else:
        files = [p for p in args.input.rglob('*') if p.suffix.lower() in SUPPORTED_EXTENSIONS]
        base_dir = args.input

    logger.info(f"找到 {len(files)} 个待处理视频文件。")

    success_cnt, skipped_cnt, failed_cnt = 0, 0, 0
    total_saved_mb = 0.0

    for idx, file_path in enumerate(files, 1):
        rel_path = file_path.relative_to(base_dir) if args.input.is_dir() else file_path.name
        out_path = args.output / rel_path if args.input.is_dir() else args.output

        logger.info(f"\n[{idx}/{len(files)}] 处理中: {file_path.name}")
        status, orig_mb, new_mb, saving, vmaf, q_val = process_file(file_path, out_path, args, model_hd, model_4k)

        if status == "success":
            success_cnt += 1
            saved = orig_mb - new_mb
            total_saved_mb += saved
            logger.info(f"   ✅ 完成! 压缩前 {orig_mb:.1f}MB ➔ 压缩后 {new_mb:.1f}MB (节省 {saving:.1f}%, 省下 {saved:.1f}MB)")
        elif status == "skipped_small_file":
            skipped_cnt += 1
        else:
            failed_cnt += 1

    logger.info("\n" + "="*50)
    logger.info(f"处理任务完成: 成功 {success_cnt} 个, 跳过 {skipped_cnt} 个, 放弃/失败 {failed_cnt} 个")
    logger.info(f"累计节省磁盘空间: {total_saved_mb/1024:.2f} GB ({total_saved_mb:.1f} MB)")
    logger.info("="*50)

if __name__ == "__main__":
    main()
