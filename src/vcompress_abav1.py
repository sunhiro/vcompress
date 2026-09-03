#!/usr/bin/env python3
"""
vcompress_abav1.py - 基于 Rust 后端 ab-av1 的高级择优视频压缩与 VMAF 质量搜索引擎
支持多线程高性能模式 (--vmaf-threads 6，预算约 8GB RAM)，自动生成 CSV/Excel/HTML 三重台账。
已支持断点续压（自动跳过目标目录下已压过的产物）。
"""

import os
import sys
import subprocess
import shutil
import argparse
import json
import logging
import csv
import datetime as dt
from pathlib import Path
from export_report import build_excel_report, build_html_report
from naming import marked_name
from fullset import (read_manifest, write_manifest, manifest_row, full_hash,
                     plan_move_untouched, apply_moves, HASHING_AVAILABLE)

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
            '-select_streams', 'v:0',
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
            # ⚠ 别改成 `-overwrite_original_in_place`。它不是「就地改」——
            # 它写临时文件后把内容**整体拷回原文件**（官方文档：约慢 3 倍），
            # 中途断开就留下一个**非空但损坏**的产物，而续压守卫只看「存在且非空」，
            # 照收不误。`-overwrite_original` 走 rename，是原子的：临时文件写坏了
            # 原产物一个字节不动。**宁可慢，不可错。**
            #
            # 已知代价：rename 会被群晖回收站接住，真机攒了 1010 个与产物同名、
            # 小 1KB 左右（差的正是标签）的中间版，占 40GB。**那是清理问题，
            # 不是正确性问题**，正解是产物先写临时名、元数据写完再 rename 就位
            # （连带解决残片问题），不是拿原子性去换清爽。
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
        # 🔴 「闸门拒了」是**素材结论**（终态，重跑还是这个结果），
        # 「编码失败」是**机器故障**（值得重试）。两者都叫 failed，下游就只能
        # 靠字符串匹配 detail 去猜，而 vcat 服务端 P2-44 刚花力气把这两件事分开。
        return "quality_rejected", orig_mb, 0.0, 0.0, "NA", "NA", f"未过质量门限(VMAF<{args.vmaf_min})", pix_fmt

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
        # 🔴 ffmpeg 是直接往最终路径写的（-y），失败时盘上留着半个文件。
        # 不删它，下一轮的续压守卫（存在且非空 → 跳过）就会把这个**没过闸门的
        # 残片**当成压好的产物，而残片和真产物同名、外部扫描也认不出来。
        # 真机上 MVI_0046.AVI 那次 SMB 掉线走的就是这条路。
        # 🔴 删之前必须确认要删的不是源片本身。单文件模式下 out_path 原样等于
        # `-o`，`-i x.mp4 -o x.mp4` 这种误用就会让这一刀落到原片上。
        # 当前调用链走不到（续压守卫会先跳过，工人的 marked_name 非幂等），
        # 但「当前走不到」不是不设防的理由——红线 1：绝不损坏原始文件。
        same_as_source = False
        try:
            same_as_source = out_path.exists() and out_path.samefile(src_path)
        except OSError:
            same_as_source = False
        if same_as_source:
            logger.error(f"   [拒绝清理] 输出路径与源片是同一个文件，不动它: {out_path}")
        elif out_path.exists():
            try:
                out_path.unlink()
                logger.warning(f"   [清理] 已删除未通过验证的半成品: {out_path}")
            except OSError as e:
                logger.error(f"   [清理失败] 半成品仍在盘上，下轮可能被误当成产物: {out_path} ({e})")
        return "failed", orig_mb, 0.0, 0.0, best_vmaf, best_crf, "编码失败", pix_fmt

    new_bytes = out_path.stat().st_size
    new_mb = new_bytes / (1024 * 1024)
    actual_saving = round(((orig_bytes - new_bytes) / orig_bytes) * 100, 1)

    preserve_metadata(src_path, out_path)

    # ADR-019 决策 2/3：色度/位深降级**默认放行，但一律记录**，而且记的必须是
    # 压完后 ffprobe 实测的产物格式，不是按编码器推断的。此前这里只写了源片
    # 那一半，于是「放行」照做、「记录」没做——2026-09-03 实测查出 46 个
    # yuvj422p 源片的产物变成 yuv420p（色度减半），账上一个字都看不出来。
    out_pix = get_video_metadata(out_path)[4] or ""
    # ⚠ 探不出来要写成 `源->?`，不能回落成单值——单值和「测了，没变化」长得
    # 一模一样，那正是 ADR-027 要消灭的半条记录（账上看不出是没变还是没测出来）。
    pix_col = f"{pix_fmt}->{out_pix or '?'}" if pix_fmt else (out_pix or "")
    if pix_fmt and out_pix and pix_fmt != out_pix:
        logger.info(f"   [像素格式变化] {pix_fmt} -> {out_pix}（已记入台账第 8 列）")

    logger.info(f"   ✅ 转码成功! 原始 {orig_mb:.1f}MB ➔ 压缩后 {new_mb:.1f}MB (实际节省 {actual_saving}%)")

    return "success", orig_mb, new_mb, actual_saving, best_vmaf, best_crf, "保留压缩版", pix_col

def write_replace_scripts(input_dir: Path, output_dir: Path):
    """在输出目录中生成一键替换/统一合并脚本 `_apply_replace.py` 和 `_apply_replace.sh`"""
    if not input_dir.is_dir():
        return

    src_abs = input_dir.resolve()
    out_abs = output_dir.resolve()

    py_script_path = out_abs / "_apply_replace.py"
    sh_script_path = out_abs / "_apply_replace.sh"

    py_content = f'''#!/usr/bin/env python3
"""
_apply_replace.py - 压缩成果人工确认后的覆盖与文件夹合并工具
自动生成于: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

import os
import sys
import shutil
import stat
from pathlib import Path

SRC_DIR = Path({repr(str(src_abs))})
OUT_DIR = Path({repr(str(out_abs))})

COMPRESSED_MARKER = ".hevc"

def is_marked(rel_path):
    p = Path(rel_path)
    return p.suffix == COMPRESSED_MARKER or p.stem.endswith(COMPRESSED_MARKER)

def unmarked_name(rel_path):
    p = Path(rel_path)
    if p.suffix == COMPRESSED_MARKER:
        return str(p.with_name(p.stem))
    if not p.stem.endswith(COMPRESSED_MARKER):
        return str(rel_path)
    return str(p.with_name(f"{{p.stem[:-len(COMPRESSED_MARKER)]}}{{p.suffix}}"))

def main():
    if not OUT_DIR.exists():
        print(f"❌ 错误: 输出目录不存在: {{OUT_DIR}}")
        sys.exit(1)
    if not SRC_DIR.exists():
        print(f"❌ 错误: 源目录不存在: {{SRC_DIR}}")
        sys.exit(1)

    skip_files = {{"_report.csv", "_report.xlsx", "_report.html", "_manifest.csv", "_apply_replace.py", "_apply_replace.sh"}}
    
    products = []
    for root, dirs, files in os.walk(OUT_DIR):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.startswith(".") or f in skip_files:
                continue
            fpath = Path(root) / f
            rel = fpath.relative_to(OUT_DIR)
            products.append((fpath, rel))

    print("=" * 65)
    print(" 🎬 vcompress 压缩成果人工确认后的覆盖与统一工具")
    print("=" * 65)
    print(f" 源片文件夹 : {{SRC_DIR}}")
    print(f" 产物文件夹 : {{OUT_DIR}}")
    print(f" 待覆盖/移动产物 : {{len(products)}} 个视频文件")
    print("-" * 65)

    if not products:
        print("ℹ️ 输出目录中未找到可覆盖/合并的压缩视频产物。")
        return

    replace_pairs = []
    for fpath, rel in products:
        rel_str = str(rel)
        if is_marked(rel_str):
            orig_rel_str = unmarked_name(rel_str)
        else:
            orig_rel_str = rel_str

        orig_path = SRC_DIR / orig_rel_str
        target_path = SRC_DIR / rel

        orig_mb = orig_path.stat().st_size / (1024 * 1024) if orig_path.exists() else 0.0
        new_mb = fpath.stat().st_size / (1024 * 1024)
        
        replace_pairs.append((fpath, orig_path, target_path, orig_mb, new_mb, orig_rel_str, rel_str))
        print(f"  • 源文件: {{orig_rel_str}} ({{orig_mb:.1f}} MB)")
        print(f"  ➔ 替换为: {{rel_str}} ({{new_mb:.1f}} MB)")

    print("-" * 65)
    print(" ⚠️  警告: 本操作将用压缩版本覆盖/替换源片，并在完成后保留唯一的源文件夹！")
    print(" 请确保您已人工确认播放过压缩后视频，质量满足要求。")
    print("-" * 65)

    confirm = input("输入 'y' 或 'yes' 确认执行覆盖与统一文件夹 (其他取消): ").strip().lower()
    if confirm not in ['y', 'yes']:
        print("❌ 操作取消，所有文件均未修改。")
        return

    print("\\n🚀 开始执行文件覆盖与整理...")
    success_cnt = 0

    for fpath, orig_path, target_path, orig_mb, new_mb, orig_rel_str, rel_str in replace_pairs:
        # 处理源文件: 若源文件存在且为旧片，解除 uchg 锁定并删除
        if orig_path.exists():
            flags = getattr(os.lstat(orig_path), "st_flags", 0)
            if flags & getattr(stat, "UF_IMMUTABLE", 0):
                os.chflags(orig_path, flags & ~stat.UF_IMMUTABLE)
            try:
                orig_path.unlink()
                print(f"  [删除旧源片] {{orig_rel_str}}")
            except Exception as e:
                print(f"  [删除源片失败] {{orig_rel_str}}: {{e}}")
                continue

        # 将压缩文件移动到源目录位置
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(fpath, target_path)
            print(f"  [移动压缩版] {{rel_str}} ➔ {{target_path}}")
            success_cnt += 1
        except Exception as e:
            print(f"  [移动产物失败] {{rel_str}}: {{e}}")

    # 移动台账报表至源目录
    for rfile in ["_report.csv", "_report.xlsx", "_report.html", "_manifest.csv"]:
        rf_path = OUT_DIR / rfile
        if rf_path.exists():
            try:
                os.rename(rf_path, SRC_DIR / rfile)
                print(f"  [迁移台账] {{rfile}} ➔ {{SRC_DIR / rfile}}")
            except Exception as e:
                pass

    # 清理已空或剩余脚本的 OUT_DIR
    for extra in ["_apply_replace.py", "_apply_replace.sh"]:
        p = OUT_DIR / extra
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass
    try:
        shutil.rmtree(OUT_DIR)
        print(f"  [清理临时目录] 已移除 {{OUT_DIR}}")
    except Exception as e:
        print(f"  [提示] 临时目录 {{OUT_DIR}} 尚有遗留，请手动删除: {{e}}")

    print("=" * 65)
    print(f"✅ 覆盖整理成功！已更新 {{success_cnt}} 个文件。")
    print(f"📂 唯一的全集文件夹: {{SRC_DIR}}")
    print("=" * 65)

if __name__ == '__main__':
    main()
'''

    sh_content = f'''#!/bin/bash
# 一键手动确认覆盖与合并脚本
cd "$(dirname "$0")"
python3 _apply_replace.py "$@"
'''

    with open(py_script_path, "w", encoding="utf-8") as f:
        f.write(py_content)
    py_script_path.chmod(0o755)

    with open(sh_script_path, "w", encoding="utf-8") as f:
        f.write(sh_content)
    sh_script_path.chmod(0o755)

    logger.info(f"一键覆盖合并脚本已自动生成: {sh_script_path} (及 {py_script_path})")

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
    parser.add_argument('--skip-hdr', dest='skip_hdr', action='store_true', help='自动跳过 10-bit / HDR 视频以保持极高性价比')
    parser.add_argument('--no-skip-hdr', dest='skip_hdr', action='store_false', help='强制压制 10-bit / HDR 视频')
    parser.set_defaults(skip_hdr=False)
    parser.add_argument('--vmaf-threads', type=int, default=6, help='VMAF 计算允许的最大线程数 (默认 6，预算 ~8GB RAM)')
    parser.add_argument('--no-vmaf-scale', dest='vmaf_scale_1080p', action='store_false', help='关闭 VMAF 计算时的 1080p 缩放 (默认开启 1080p 缩放)')
    parser.set_defaults(vmaf_scale_1080p=True)
    parser.add_argument('--sample-sec', type=int, default=15, help='每个采样切片的秒数 (默认 15s)')
    parser.add_argument('--samples', type=int, default=3, help='采样切片数量 (默认 3 段)')
    parser.add_argument('--preset', help='编码器 Preset 预设')
    parser.add_argument('--preview', action='store_true', help='Preview 模式: 仅做二分法测算评估，不输出全量转码文件')
    parser.add_argument('--move-untouched', action='store_true',
                        help='把不值得压的源片也搬进输出目录，使其成为可整体拷贝的「全集」。'
                             '同分区 rename、秒级。🔴 默认关闭：搬运本身不减少副本数，'
                             '但前提是这些文件在别处还有备份——源目录搬空之后就不是备份了。'
                             '开之前先确认（vcat 用户可看 `vcat status --cold` 的孤本数）。')
    # 默认不生成：这个脚本会删掉 -i 指向的源片。它的杀伤力配不上「每次跑完
    # 都无声生成一份」的行为——尤其当 -i 是一份临时副本时，跑一下就没了。
    parser.add_argument('--emit-replace-script', action='store_true',
                        help='额外生成 _apply_replace.py/sh 人工覆盖脚本（会删除 -i 下的源片，默认不生成）')
    parser.add_argument('--json-out', action='store_true', help='单文件模式：处理完毕后向 stdout 输出一行 JSON 结果（供 vcat compress-worker 解析）')

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
    report_xlsx = out_dir / "_report.xlsx"
    report_html = out_dir / "_report.html"

    REPORT_HEADER = ['文件', '原MB', '新MB', '省%', 'VMAF', 'CRF/Q', '决定', '像素格式']
    if not report_csv.exists():
        with open(report_csv, 'w', encoding='utf-8', newline='') as f:
            csv.writer(f).writerow(REPORT_HEADER)
    elif args.input.is_dir():
        # 开跑前把「本轮要重新处理」的旧行清掉，之后照旧逐条追加。
        #
        # 为什么需要：续压守卫是「产物存在且非空 → 跳过」，而**跳过类的文件根本
        # 不产出产物**，守卫拦不住，于是每轮都重走一遍并再 append 一次。真机上
        # 69 个唯一文件被写成 131 行，「已是高效HEVC(跳过)」12 个记成 51 个，
        # 而 HTML 仪表盘的 KPI 直接按行数汇总——「扫描了多少」是虚的。
        #
        # 🔴 **只在目录模式做**。单文件模式（工人那条路）`out_dir` 是产物的父目录，
        # 动的是**整棵树共用的那份报表**，而这里的行键是裸文件名、树里的行键是
        # 相对路径，根本对不上——删不掉任何行，却把整个文件重写一遍。纯风险零收益。
        #
        # 🔴 **`--preview` 只清它自己上一轮的 preview 行**。preview 显式绕过续压
        # 守卫（`and not args.preview`），若照目录模式的判据走，会把**全部**旧行
        # 当成「本轮会重跑」而清掉——包括「保留压缩版」那些行，而那正是 vcat
        # 血缘登记 `--report` 唯一认的行（`lineage.load_report` 按决定筛）。
        # 对一棵压完的树跑一次 preview（skill 文档标准两趟跑法的第一趟！），
        # VMAF/CRF 与 ADR-027 刚加的第 8 列就一起没了。**这是复检当场抓出来的。**
        targets = set()
        for fp in files:
            rel = fp.relative_to(base_dir)
            op = args.output / marked_name(rel)
            if op.exists() and op.stat().st_size > 0 and not args.preview:
                continue  # 本轮会被续压守卫跳过，它的行原样留着
            targets.add(str(rel).replace(',', ' '))

        def _drop(row):
            if not row or row[0] not in targets:
                return False
            if args.preview:
                # preview 唯一不能碰的是「保留压缩版」——那行带着 VMAF/CRF 与
                # 第 8 列的实测像素格式，是 vcat 血缘登记唯一认的行，而 preview
                # **重新生成不出来**（它写的是 PREVIEW成功）。其余各类（上一轮的
                # PREVIEW 行、各种跳过行、闸门拒绝行）本轮都会照样再产出一遍，
                # 清掉才不会每跑一次 preview 就把整棵树重追一遍。
                return len(row) > 6 and row[6] != "保留压缩版"
            return True

        # 🔴 先写临时文件再 os.replace 就位。直接 open('w') 是**先截断后写**，
        # 中途崩/掉线就把整份报表清零——而那时 except 里那句「可能出现重复行」
        # 会把一次数据丢失说成排版瑕疵。同目录临时文件保证 replace 是同分区原子操作。
        tmp = report_csv.with_suffix(".csv.tmp")
        try:
            with open(report_csv, encoding='utf-8-sig', newline='') as f:
                kept = [r for r in csv.reader(f) if not _drop(r)]
            if kept and kept[0] and kept[0][0] == REPORT_HEADER[0]:
                kept = kept[1:]
            with open(tmp, 'w', encoding='utf-8', newline='') as f:
                w = csv.writer(f)
                w.writerow(REPORT_HEADER)
                w.writerows(kept)
            os.replace(tmp, report_csv)
        except OSError as e:
            logger.warning(f"报表旧行清理未完成，原报表保持不动，本轮可能出现重复行: {e}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    # 内容清单（upsert）。与 _report.csv 的分工：那个是「本次跑了什么」的运行日志，
    # 这个是「这棵树里有什么」的账。追加式日志补不回漏掉的行，清单能从树上重建。
    manifest = read_manifest(out_dir)
    if args.input.is_dir() and not HASHING_AVAILABLE:
        logger.warning("⚠️ 未安装 blake3，内容清单的哈希列将整列为空——这份清单只能按路径"
                       "认账，不能用来校验产物完整性。装上：pip install blake3")

    logger.info(f"启动多线程择优压缩流程，源目录: {args.input} ➔ 目标目录: {args.output}")
    logger.info(f"待扫描视频文件总数: {len(files)} 个。内存优化: VMAF线程={args.vmaf_threads} (预算 ~8GB RAM) | 小文件门限={args.min_file_mb}MB")

    success_cnt, skipped_cnt, failed_cnt = 0, 0, 0
    total_saved_mb = 0.0

    for idx, file_path in enumerate(files, 1):
        rel_path = file_path.relative_to(base_dir) if args.input.is_dir() else file_path.name
        # 产物带 .hevc 标记（naming.py 说明了为什么）。单文件模式下输出路径由
        # 调用方（vcat compress worker）给全，它自己已经加过标记，这里不再动。
        out_path = args.output / marked_name(rel_path) if args.input.is_dir() else args.output

        # 断点续压：若目标压缩产物已存在且非空，自动跳过
        if out_path.exists() and out_path.stat().st_size > 0 and not args.preview:
            logger.info(f"[{idx}/{len(files)}] [断点续压: 已存在] 跳过: {rel_path}")
            skipped_cnt += 1
            # 跳过转码 ≠ 跳过记账。清单是「这棵树里有什么」的账，中断重跑时
            # 上一轮的产物只存在于盘上；这里不补登记它就永远进不了清单，而且
            # 不报任何错——fullset.py 开头讲的「漏记 66 个」正是这个形状。
            # 只在缺行时补：已有的行不动，免得大树上每次续跑都重算全量哈希。
            if args.input.is_dir():
                dst_rel = str(out_path.relative_to(out_dir))
                if dst_rel not in manifest:
                    manifest[dst_rel] = manifest_row(
                        dst_rel=dst_rel, src_rel=str(rel_path),
                        dst_bytes=out_path.stat().st_size, src_bytes=file_path.stat().st_size,
                        dst_hash=full_hash(out_path), src_hash=full_hash(file_path),
                        # 这一轮没测 VMAF，留空而不是编一个数（红线：宁可拒绝，不可错）
                        vmaf=None,
                        encoder=args.encoder, tool="vcompress_abav1.py",
                    )
                    logger.info(f"   [补登记] 上一轮的产物补进内容清单: {dst_rel}（VMAF 本轮未测，留空）")
            # 🔴 这条路径以前**什么都不往 stdout 写就退出 0**，于是调用方
            # （vcat 的 mac_mini_worker）看到「退出码 0 + 无 JSON」，把它当成
            # 「压成功了」记进终态流水账——而盘上那个文件可能是上一轮崩掉留下的
            # 残片。沉默让调用方只能靠猜，说清楚是跳过、跳过的是哪个文件。
            if getattr(args, 'json_out', False):
                sys.stdout.write(json.dumps({
                    "status": "skipped",
                    "out_path": str(out_path),
                    "out_size": out_path.stat().st_size,
                    "vmaf_mean": None, "vmaf_min": None, "crf_val": None,
                    "detail": "断点续压：产物已存在，本轮未重压也未验证",
                }, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            continue

        logger.info(f"\n[{idx}/{len(files)}] 检查中: {rel_path}")
        status, orig_mb, new_mb, saving, vmaf, crf_val, decision, pix_fmt = process_file_abav1(file_path, out_path, args)

        # 写入台账 CSV
        rel_csv_str = str(rel_path).replace(',', ' ')
        with open(report_csv, 'a', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([rel_csv_str, f"{orig_mb:.2f}", f"{new_mb:.2f}", f"{saving:.1f}", str(vmaf), str(crf_val), decision, pix_fmt])

        # --json-out：向 stdout 输出一行机器可读 JSON（供 vcat compress-worker 解析）
        if getattr(args, 'json_out', False):
            out_size = int(new_mb * 1024 * 1024) if new_mb else None
            # 🔴 老版这里是 `status.startswith("skipped")` 里面再判 `status == "failed"`
            # ——恒假，quality_rejected 一次都没发出去过，闸门拒绝与编码失败一起
            # 被下游当成 failed。工人那边对 failed 的处理是「记终态、不重试」，
            # 于是**真正的机器故障永远不会被重做**。
            if status == "success":
                json_status = "success"
            elif status == "quality_rejected":
                json_status = "quality_rejected"
            elif status.startswith("skipped"):
                json_status = "skipped"
            else:
                json_status = "failed"
            result_json = {
                "status": json_status,
                "out_path": str(out_path) if status == "success" and out_path.exists() else None,
                "out_size": out_size if status == "success" else None,
                "vmaf_mean": float(vmaf) if vmaf not in ("NA", None) else None,
                "vmaf_min": float(vmaf) if vmaf not in ("NA", None) else None,
                "crf_val": crf_val if crf_val != "NA" else None,
                "detail": decision,
            }
            # 独立打印到 stdout（与 logger 写 stderr/文件分离，不会被 logger 截断）
            sys.stdout.write(json.dumps(result_json, ensure_ascii=False) + "\n")
            sys.stdout.flush()

        if status == "success":
            success_cnt += 1
            saved = orig_mb - new_mb
            total_saved_mb += saved
            if args.input.is_dir() and out_path.exists():
                dst_rel = str(out_path.relative_to(out_dir))
                manifest[dst_rel] = manifest_row(
                    dst_rel=dst_rel, src_rel=str(rel_path),
                    dst_bytes=out_path.stat().st_size, src_bytes=file_path.stat().st_size,
                    dst_hash=full_hash(out_path), src_hash=full_hash(file_path),
                    vmaf=None if vmaf in ("NA", None) else vmaf,
                    encoder=args.encoder, tool="vcompress_abav1.py",
                )
        elif status.startswith("skipped") or status == "quality_rejected":
            # 闸门拒了不是「失败」——素材本来就不值得压，这是正常结论
            skipped_cnt += 1
        else:
            failed_cnt += 1

    if args.input.is_dir():
        logger.info(f"内容清单已更新: {write_manifest(out_dir, manifest)}（{len(manifest)} 行）")

        if args.move_untouched:
            moves, problems = plan_move_untouched(args.input, out_dir)
            for prob in problems:
                logger.error(f"[搬运跳过] {prob}")
            if problems:
                # 宁可不搬，也不在有疑点的情况下动用户的文件（红线：宁可拒绝，不可错）
                logger.error(f"存在 {len(problems)} 处问题，本次不执行搬运")
            else:
                ok, relocked = apply_moves(moves)
                logger.info(f"全集搬运完成: 移入 {ok} 个未压缩源片"
                            f"（其中 {relocked} 个的 uchg 不可变标志已原样贴回）")
                logger.info(f"输出目录现在是全集，源目录只剩被压过的那批源片（作备份）")

    # 运行完毕自动刷新导出 Excel 与 HTML 报表，并生成一键人工覆盖脚本
    try:
        build_excel_report(report_csv, report_xlsx)
        build_html_report(report_csv, report_html)
        if args.emit_replace_script:
            logger.warning("⚠️ 已生成 _apply_replace.py/sh —— 它会删除 "
                           f"{args.input} 下的源片并把产物搬过去，确认过再跑。")
            write_replace_scripts(args.input, out_dir)
    except Exception as e:
        logger.error(f"导出 Excel/HTML 台账或生成覆盖脚本发生异常: {e}")

    logger.info("\n" + "="*60)
    logger.info(f"择优压缩任务完成: 高性价比压缩成功 {success_cnt} 个, 低性价比/已压缩跳过 {skipped_cnt} 个, 拦截/失败 {failed_cnt} 个")
    logger.info(f"累计释放磁盘空间: {total_saved_mb/1024:.2f} GB ({total_saved_mb:.1f} MB)")
    logger.info(f"结构化台账已导出: CSV={report_csv} | Excel={report_xlsx} | HTML={report_html}")
    logger.info("="*60)

if __name__ == "__main__":
    main()
