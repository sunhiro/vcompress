#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compare_compressed.py
---------------------
扫描"原版目录"和"压缩后目录"，逐一对比每对视频的：
  码率、大小、编码、色深、色彩空间、分辨率、帧率
并给出建议，输出 CSV 报告 + 文字摘要。

用法:
    # 自动推断压缩后目录（源目录名 + -压缩后）
    python compare_compressed.py E:/path/to/source

    # 手动指定压缩后目录
    python compare_compressed.py E:/source --compressed E:/compressed

    # 指定输出 CSV 路径
    python compare_compressed.py E:/source --out E:/report.csv
"""

import os
import re
import sys
import csv
import hashlib
from typing import Tuple, Optional, Dict
import subprocess
import argparse
from datetime import datetime

FFMPEG = r"D:\Program Files\digiKam\ffmpeg.exe"
VIDEO_EXT = set("mp4 mov m4v avi mkv wmv flv mpg mpeg 3gp 3g2 ts m2ts mts webm vob".split())

# ──────────────────────────────────────────────
# FFmpeg 探针
# ──────────────────────────────────────────────
DUR_RE      = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
TOTAL_BR_RE = re.compile(r"Duration:.*?bitrate:\s*(\d+)\s*kb/s")
VIDEO_RE    = re.compile(
    r"Stream.*?Video:\s*(\w+)"                   # codec
    r".*?(?:(\d+x\d+))"                          # resolution
    r".*?(?:(\d+(?:\.\d+)?)\s*fps)?"             # fps
    r".*?(?:,\s*(\d+)\s*kb/s)?"                  # video bitrate
)
PIX_FMT_RE  = re.compile(r"yuv420p(10|12)?")
COLOR_RE    = re.compile(r"(bt\d+(?:nc)?|srgb|unknown)")
HDR_RE      = re.compile(r"(bt2020|arib-std-b67|smpte2084|hlg|hdr10)", re.I)
AUDIO_RE    = re.compile(r"Stream.*?Audio:\s*(\w+).*?(\d+)\s*kb/s")
SSIM_RE     = re.compile(r"SSIM\s+Y:.*?All:([0-9.]+)")
PSNR_RE     = re.compile(r"PSNR\b.*?average:(inf|-?[0-9.]+)")
# 更稳的分辨率/帧率(VIDEO_RE 对部分文件会误抓 codec fourcc 如 0x3163... 当分辨率)
RES_RE      = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")
FPS_RE      = re.compile(r"(\d+(?:\.\d+)?)\s*fps")

def probe(path: str) -> dict:
    """Call ffmpeg -i and parse metadata. Returns dict with all fields."""
    result = {
        "exists": False,
        "size_mb": 0.0,
        "duration_sec": 0.0,
        "total_bitrate_kbps": 0,
        "codec": "",
        "resolution": "",
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "video_bitrate_kbps": 0,
        "bit_depth": "",
        "color_space": "",
        "is_hdr": False,
        "is_10bit": False,
        "audio_codec": "",
        "audio_bitrate_kbps": 0,
        "raw": "",
    }
    if not os.path.exists(path):
        return result

    result["exists"] = True
    result["size_mb"] = round(os.path.getsize(path) / (1024 * 1024), 2)

    try:
        proc = subprocess.run(
            [FFMPEG, "-hide_banner", "-i", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        raw = proc.stderr.decode("utf-8", errors="replace")
        result["raw"] = raw
    except Exception as e:
        result["raw"] = str(e)
        return result

    # Duration + total bitrate
    m = DUR_RE.search(raw)
    if m:
        h, mn, s = m.groups()
        result["duration_sec"] = round(int(h) * 3600 + int(mn) * 60 + float(s), 2)
    m = TOTAL_BR_RE.search(raw)
    if m:
        result["total_bitrate_kbps"] = int(m.group(1))

    # Video stream
    m = VIDEO_RE.search(raw)
    if m:
        result["codec"] = m.group(1) or ""
        if m.group(2):
            result["resolution"] = m.group(2)
            parts = m.group(2).split("x")
            result["width"]  = int(parts[0])
            result["height"] = int(parts[1])
        result["fps"] = float(m.group(3)) if m.group(3) else 0.0
        result["video_bitrate_kbps"] = int(m.group(4)) if m.group(4) else 0

    # 用专用正则覆盖分辨率/帧率(比上面的组合正则可靠)
    rm = RES_RE.search(raw)
    if rm:
        w, h = int(rm.group(1)), int(rm.group(2))
        result["resolution"] = f"{w}x{h}"
        result["width"], result["height"] = w, h
    fm = FPS_RE.search(raw)
    if fm:
        try:
            result["fps"] = float(fm.group(1))
        except ValueError:
            pass

    # If video bitrate not parsed from stream, estimate from total - audio
    if result["video_bitrate_kbps"] == 0 and result["total_bitrate_kbps"] > 0:
        audio_br = 0
        am = AUDIO_RE.search(raw)
        if am:
            result["audio_codec"] = am.group(1)
            audio_br = int(am.group(2))
            result["audio_bitrate_kbps"] = audio_br
        result["video_bitrate_kbps"] = max(0, result["total_bitrate_kbps"] - audio_br)
    else:
        am = AUDIO_RE.search(raw)
        if am:
            result["audio_codec"] = am.group(1)
            result["audio_bitrate_kbps"] = int(am.group(2))

    # Pixel format / bit depth
    pm = PIX_FMT_RE.search(raw)
    if pm:
        bits = pm.group(1)
        result["bit_depth"] = f"{bits}-bit" if bits else "8-bit"
        result["is_10bit"] = bits in ("10", "12")
    else:
        result["bit_depth"] = "8-bit"

    # Color space
    cm = COLOR_RE.search(raw)
    if cm:
        result["color_space"] = cm.group(1)

    # HDR detection
    result["is_hdr"] = bool(HDR_RE.search(raw)) and result["is_10bit"]

    return result


def is_same_file_quick(path1: str, path2: str) -> bool:
    """通过大小和头尾快速哈希判定两个文件是否完全一致（用于快速跳过原片拷贝）"""
    try:
        if not path1 or not path2:
            return False
        if not os.path.exists(path1) or not os.path.exists(path2):
            return False
        size1 = os.path.getsize(path1)
        size2 = os.path.getsize(path2)
        if size1 != size2:
            return False
        
        block_size = 64 * 1024
        h1 = hashlib.md5()
        h2 = hashlib.md5()
        
        with open(path1, "rb") as f1, open(path2, "rb") as f2:
            buf1 = f1.read(block_size)
            buf2 = f2.read(block_size)
            if buf1 != buf2:
                return False
            h1.update(buf1)
            h2.update(buf2)
            
            if size1 > block_size:
                f1.seek(-block_size, os.SEEK_END)
                f2.seek(-block_size, os.SEEK_END)
                buf1 = f1.read(block_size)
                buf2 = f2.read(block_size)
                if buf1 != buf2:
                    return False
                h1.update(buf1)
                h2.update(buf2)
                
        return h1.hexdigest() == h2.hexdigest()
    except Exception:
        return False


# ──────────────────────────────────────────────
# 建议逻辑
# ──────────────────────────────────────────────
def make_recommendation(src: dict, dst: dict) -> dict:
    """
    分析并返回各项警告标志和建议说明。
    """
    res = {
        "tag": "ACCEPTABLE",
        "no_benefit": False,
        "hdr_loss": False,
        "bitrate_drop": False,
        "low_bitrate": False,
        "res_change": False,
        "reasons": [],
        "remark": ""
    }

    if not src["exists"]:
        res["tag"] = "SRC_MISSING"
        res["reasons"].append("原版文件不存在")
        return res
    if not dst["exists"]:
        res["tag"] = "MISSING_COMPRESSED"
        res["reasons"].append("压缩版不存在")
        return res

    # 1. 压缩无收益
    if dst["size_mb"] >= src["size_mb"]:
        res["tag"] = "NO_BENEFIT"
        res["no_benefit"] = True
        res["reasons"].append(f"压缩无收益（压缩版 {dst['size_mb']} MB ≥ 原版 {src['size_mb']} MB）")
        return res

    # 2. 原版是 10-bit HDR
    if src["is_hdr"] or src["is_10bit"]:
        res["tag"] = "KEEP_ORIGINAL"
        res["hdr_loss"] = True
        res["reasons"].append("原版为 10-bit HDR，压缩损失色深")

    # 3. 压缩率过高且视频码率极低
    size_reduction = (src["size_mb"] - dst["size_mb"]) / src["size_mb"]
    if size_reduction > 0.85 and dst["video_bitrate_kbps"] < 3000:
        res["low_bitrate"] = True
        res["reasons"].append(f"压缩率 {size_reduction*100:.0f}%，压缩版码率仅 {dst['video_bitrate_kbps']} kbps，画画质损耗风险大")
        res["tag"] = "KEEP_ORIGINAL"

    # 4. 码率骤降
    if src["video_bitrate_kbps"] > 10000 and dst["video_bitrate_kbps"] < 3000:
        res["bitrate_drop"] = True
        res["reasons"].append(f"原版码率 {src['video_bitrate_kbps']} kbps → 压缩版 {dst['video_bitrate_kbps']} kbps，降幅过大")
        res["tag"] = "KEEP_ORIGINAL"

    # 5. 分辨率改变
    if src["width"] > 0 and dst["width"] > 0:
        sw, sh = src["width"], src["height"]
        dw, dh = dst["width"], dst["height"]
        if not ({sw, sh} == {dw, dh}):
            res["res_change"] = True
            res["reasons"].append(f"分辨率改变：{src['resolution']} -> {dst['resolution']}")
            res["tag"] = "KEEP_ORIGINAL"

    # 6. 正常重编码
    if res["tag"] == "ACCEPTABLE":
        ratio = size_reduction * 100
        res["reasons"].append(f"H.265重编码节省{ratio:.0f}%，码率{src['video_bitrate_kbps']}→{dst['video_bitrate_kbps']}kbps")

    return res


# ──────────────────────────────────────────────
# 文件匹配
# ──────────────────────────────────────────────
def find_compressed(src_path: str, src_root: str, dst_root: str) -> Optional[str]:
    """Given a source file path, find its counterpart in dst_root."""
    rel = os.path.relpath(src_path, src_root)
    stem, _ = os.path.splitext(rel)

    # Try same extension first, then .mp4
    for ext in [os.path.splitext(rel)[1], ".mp4", ".MP4", ".mov", ".MOV"]:
        candidate = os.path.join(dst_root, stem + ext)
        if os.path.exists(candidate):
            return candidate
    return None


def human_size(mb: float) -> str:
    if mb >= 1024:
        return f"{mb/1024:.2f} GB"
    return f"{mb:.1f} MB"


# ──────────────────────────────────────────────
# 质量评分 (SSIM / PSNR)
# ──────────────────────────────────────────────
# 注意：原片与压缩片时基/帧率常不同（尤其 iPhone 可变帧率），
# 直接比会逐帧错位得到假分数，必须先把两路重采样到同一 fps 再逐帧比。
def _run_ssim_psnr(src_path, dst_path, fps, threads, ss=None, t=None):
    """跑一段 ssim+psnr。ss/t 为 None 时算整片；否则从 ss 秒起算 t 秒。"""
    f = f"fps=fps={fps:.4f},settb=AVTB"
    # scale2ref: 运行时把压缩片缩放到原片实际尺寸，天然兼容手机旋转元数据/分辨率差异
    # （硬编码 scale 到编码尺寸会和自动旋转后的画面打架，导致 ssim 报错、分数空白）
    graph = (
        f"[0:v]{f}[a0];[1:v]{f}[b0];"
        f"[b0][a0]scale2ref=flags=bicubic[b1][a1];"
        f"[a1]split=2[a1a][a1b];[b1]split=2[b1a][b1b];"
        f"[a1a][b1a]ssim;[a1b][b1b]psnr"
    )
    # -nostdin: 禁止 ffmpeg 读 stdin(否则个别文件会让 ffmpeg 等输入而永久卡死)
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-threads", str(threads)]
    if ss is not None: cmd += ["-ss", str(ss)]
    if t  is not None: cmd += ["-t", str(t)]
    cmd += ["-i", src_path]
    if ss is not None: cmd += ["-ss", str(ss)]
    if t  is not None: cmd += ["-t", str(t)]
    cmd += ["-i", dst_path, "-lavfi", graph, "-an", "-f", "null", "-"]
    try:
        # 超时 240s/段: 卡住也会被杀掉并跳过(返回 None),不再拖垮整批
        raw = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, timeout=240).stderr.decode("utf-8", "replace")
    except Exception:
        return (None, None)
    ssim = psnr = None
    m = SSIM_RE.search(raw)
    if m:
        try: ssim = float(m.group(1))
        except ValueError: pass
    m = PSNR_RE.search(raw)
    if m:
        try: psnr = float(m.group(1))
        except ValueError: pass
    return (ssim, psnr)


def compute_quality(src_path, dst_path, src_fps, duration, dst_duration=0, sample_sec=0, windows=3, threads=3):
    """返回 (ssim, psnr)，失败 (None, None)。
    sample_sec<=0 或 >=时长 → 整片(最准)；
    否则把 sample_sec 拆成 windows 段，均匀撒在 前/中/后，规避片尾，
    这样视频中段/后段才爆发的运动也能被采到（不像只截开头会漏）。

    关键：任何一段都必须用 -t 收在结尾前一点。原片(可变帧率)与压缩片(恒定帧率)
    跑到结尾时总帧数对不齐，会触发 ffmpeg scale2ref 断言崩溃(best_input>=0)。
    有效时长取原/压两者较小值，避免任一路先结束。"""
    fps = src_fps if (src_fps and src_fps > 0) else 30.0
    durs = [d for d in (duration, dst_duration) if d and d > 0]
    eff = min(durs) if durs else 0
    GUARD = 0.5                                       # 结尾预留，规避尾部帧数不齐

    # 整片（或采样时长≥有效时长）
    if not sample_sec or sample_sec <= 0 or (eff and sample_sec >= eff):
        t = max(1.0, eff - GUARD) if (eff and eff > 1.5) else None
        return _run_ssim_psnr(src_path, dst_path, fps, threads, ss=None, t=t)

    if not eff or eff <= 0:                            # 拿不到时长，退回从头截
        return _run_ssim_psnr(src_path, dst_path, fps, threads, ss=0, t=sample_sec)

    W = max(1, min(windows, int(sample_sec)))         # 每段至少 1 秒
    seg = max(1.0, sample_sec / W)
    usable = max(0.0, eff - seg - GUARD)              # 末段也不碰结尾
    ssims, psnrs = [], []
    for i in range(W):
        off = usable * (i + 0.5) / W                  # 各段落在各自区间中点，覆盖 前/中/后
        s, p = _run_ssim_psnr(src_path, dst_path, fps, threads, ss=round(off, 2), t=round(seg, 2))
        if s is not None:
            ssims.append(s)
        if p is not None and p != float("inf"):
            psnrs.append(p)
    if not ssims:
        return (None, None)
    ssim = sum(ssims) / len(ssims)
    if psnrs:
        psnr = sum(psnrs) / len(psnrs)
    else:
        psnr = float("inf") if all(s >= 0.9999 for s in ssims) else None
    return (ssim, psnr)


def ssim_rating(ssim):
    if ssim is None:
        return ""
    if ssim >= 0.98:
        return "几乎无损"
    if ssim >= 0.95:
        return "优"
    if ssim >= 0.90:
        return "良"
    return "可见损失"


def combined_reco(src, dst, tag, ssim):
    """结构化综合建议（枚举值，便于 Excel 筛选）：综合 质量分 + 码率 + HDR。"""
    if not src.get("exists"):
        return "缺原版"
    if not dst.get("exists"):
        return "缺压缩版"
    if tag == "NO_BENEFIT":
        return "无收益-未压"
    if src.get("is_hdr") or src.get("is_10bit"):
        return "保留原版-HDR/10bit"
    if ssim is not None:                       # 有质量分：以客观分为准
        if ssim >= 0.98:
            return "可删原版-质量优"
        if ssim >= 0.95:
            return "可删原版-质量良"
        if ssim >= 0.90:
            return "谨慎核对-质量中"
        return "保留原版-质量降"
    # 无质量分：退回码率启发式
    if tag == "KEEP_ORIGINAL":
        return "待核对-码率降幅大"
    return "可删原版-未评分"


# ──────────────────────────────────────────────
# Excel 输出 (首行筛选 + 冻结表头)
# ──────────────────────────────────────────────
def write_xlsx(rows, fieldnames, path):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        print("  (未安装 openpyxl，跳过 Excel；pip install openpyxl 可启用)")
        return False

    wb = Workbook()
    ws = wb.active
    ws.title = "对比报告"
    ws.append(fieldnames)
    for r in rows:
        ws.append([r.get(k, "") for k in fieldnames])

    hdr_font = Font(bold=True, color="FFFFFF")
    hdr_fill = PatternFill("solid", fgColor="4472C4")
    for c in ws[1]:
        c.font = hdr_font
        c.fill = hdr_fill
        c.alignment = Alignment(horizontal="center", vertical="center")

    ws.auto_filter.ref = ws.dimensions   # 首行筛选
    ws.freeze_panes = "A2"               # 冻结表头

    for i, name in enumerate(fieldnames, 1):
        sample = [len(str(name))] + [len(str(r.get(name, ""))) for r in rows[:300]]
        ws.column_dimensions[get_column_letter(i)].width = min(max(max(sample) + 2, 8), 42)

    try:
        wb.save(path)
        return True
    except Exception as e:
        print(f"  写 Excel 失败: {e}")
        return False


# ──────────────────────────────────────────────
# 主逻辑
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Compare original vs compressed video directories and generate a CSV report."
    )
    parser.add_argument("source_dir", help="原版目录 / Source directory")
    parser.add_argument("--compressed", help="压缩后目录（默认: source_dir + '-压缩后'）")
    parser.add_argument("--out", help="输出 CSV 路径（默认: source_dir 同级 compare_report_<日期>.csv）")
    parser.add_argument("--min-size", type=float, default=0,
                        help="忽略小于此大小（MB）的文件，默认 0（全部扫描）")
    parser.add_argument("--quality", action="store_true",
                        help="计算 SSIM/PSNR 质量分（慢！不加则不评分）")
    parser.add_argument("--quality-sample", type=int, default=0,
                        help="质量评分总采样秒数（0=整片，最准）。>0 时把这些秒数均匀撒在全片前/中/后")
    parser.add_argument("--quality-windows", type=int, default=3,
                        help="配合 --quality-sample: 拆成几段分布采样（默认 3，覆盖 前/中/后）")
    parser.add_argument("--threads", type=int, default=3,
                        help="质量评分解码线程数（默认 3）")
    args = parser.parse_args()

    src_root = os.path.abspath(args.source_dir)
    dst_root = os.path.abspath(args.compressed) if args.compressed else \
               src_root.rstrip("\\/") + "-压缩后"

    if not os.path.exists(src_root):
        print(f"错误：源目录不存在: {src_root}")
        sys.exit(1)
    if not os.path.exists(dst_root):
        print(f"错误：压缩后目录不存在: {dst_root}")
        sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_csv = os.path.join(os.path.dirname(src_root), f"compare_report_{ts}.csv")
    out_csv = os.path.abspath(args.out) if args.out else default_csv

    print("=" * 60)
    print("  Video Compression Comparison Report Generator")
    print("=" * 60)
    print(f"源目录      : {src_root}")
    print(f"压缩后目录  : {dst_root}")
    print(f"输出 CSV    : {out_csv}")
    print()

    # ── 收集源文件 ──
    src_files = []
    for root, dirs, files in os.walk(src_root):
        dirs[:] = [d for d in dirs if d not in ("#recycle", "@eaDir") and not d.startswith(".")]
        for f in sorted(files):
            if f.startswith("._") or f == ".DS_Store":
                continue
            ext = os.path.splitext(f)[1][1:].lower()
            if ext in VIDEO_EXT:
                full = os.path.join(root, f)
                if os.path.getsize(full) >= args.min_size * 1024 * 1024:
                    src_files.append(full)

    print(f"发现源视频文件: {len(src_files)} 个\n")

    # ── CSV 列定义（同一属性 原/新 相邻，差值列紧跟其后）──
    FIELDNAMES = [
        "文件名", "相对路径",
        "大小MB_原", "大小MB_新", "节省空间MB", "节省比例%",
        "编码_原", "编码_新",
        "视频码率kbps_原", "视频码率kbps_新", "码率降低%",
        "总码率kbps_原", "总码率kbps_新",
        "色深_原", "色深_新",
        "色彩空间_原", "色彩空间_新",
        "是否HDR_原", "是否HDR_新",
        "分辨率_原", "分辨率_新",
        "帧率_原", "帧率_新",
        # 质量（仅 --quality 时有值）
        "质量_SSIM", "质量_PSNR_dB", "质量评级",
        # 静态警告打钩列
        "无收益", "HDR损耗", "码率骤降", "极低码率", "分辨率改变",
        # 结论
        "综合建议", "建议说明", "备注",
    ]

    rows = []
    stats = {
        "total": 0, "compressed_found": 0, "compressed_missing": 0,
        "keep_original": 0, "acceptable": 0, "no_benefit": 0,
        "total_src_mb": 0.0, "total_dst_mb": 0.0, "total_saved_mb": 0.0,
        "hdr_count": 0,
    }

    for idx, src_path in enumerate(src_files):
        rel = os.path.relpath(src_path, src_root)
        fname = os.path.basename(src_path)
        print(f"[{idx+1}/{len(src_files)}] 探测: {rel}")

        dst_path = find_compressed(src_path, src_root, dst_root)

        src_info = probe(src_path)
        dst_info = probe(dst_path) if dst_path else {"exists": False, "size_mb": 0, "video_bitrate_kbps": 0, "total_bitrate_kbps": 0, "codec": "", "bit_depth": "", "color_space": "", "resolution": "", "width": 0, "height": 0, "fps": 0.0, "is_hdr": False, "is_10bit": False}

        tag_info = make_recommendation(src_info, dst_info)

        saved_mb = round(src_info["size_mb"] - dst_info["size_mb"], 2) if dst_info.get("exists") else 0
        size_pct = round(saved_mb / src_info["size_mb"] * 100, 1) if src_info["size_mb"] > 0 and dst_info.get("exists") else 0
        br_src   = src_info.get("video_bitrate_kbps", 0)
        br_dst   = dst_info.get("video_bitrate_kbps", 0)
        br_drop  = round((1 - br_dst / max(br_src, 1)) * 100, 1) if dst_info.get("exists") and br_src > 0 else 0

        # ── 质量评分（仅 --quality）──
        ssim_v = psnr_v = None
        is_skipped_by_hash = False

        if dst_path and src_info["exists"] and dst_info.get("exists"):
            if is_same_file_quick(src_path, dst_path):
                ssim_v, psnr_v = 1.0, float("inf")
                is_skipped_by_hash = True
                print("      [跳过] 检测到大小及哈希完全一致，判定为原片拷贝，跳过客观对比")

        if args.quality and dst_path and src_info["exists"] and dst_info.get("exists") and not is_skipped_by_hash:
            print("      计算质量分 SSIM/PSNR ...", end="", flush=True)
            ssim_v, psnr_v = compute_quality(
                src_path, dst_path,
                src_info.get("fps", 0), src_info.get("duration_sec", 0),
                dst_info.get("duration_sec", 0),
                args.quality_sample, args.quality_windows, args.threads,
            )
            print(f" SSIM={ssim_v} PSNR={psnr_v}dB")

        # 根据客观评分调整综合建议
        final_reco = combined_reco(src_info, dst_info, tag_info["tag"], ssim_v)

        # 判定是否要忽略静态警告（当客观评分优秀 SSIM >= 0.95 且没有分辨率和 HDR 异常时）
        ignore_warnings = False
        if ssim_v is not None and ssim_v >= 0.95 and not tag_info["res_change"] and not tag_info["hdr_loss"]:
            ignore_warnings = True

        # 生成精简版“建议说明”与详细版“备注”
        short_explain = ""
        detail_remark = "；".join(tag_info["reasons"])

        if tag_info["tag"] == "NO_BENEFIT":
            short_explain = "压缩无收益"
        elif tag_info["tag"] == "SRC_MISSING":
            short_explain = "缺原版"
        elif tag_info["tag"] == "MISSING_COMPRESSED":
            short_explain = "缺压缩版"
        elif tag_info["hdr_loss"]:
            short_explain = "HDR损耗"
        elif tag_info["res_change"]:
            short_explain = "分辨率改变"
        elif ssim_v is not None:
            if ssim_v >= 0.98:
                short_explain = "画质极佳，可删原版"
                if ignore_warnings and (tag_info["bitrate_drop"] or tag_info["low_bitrate"]):
                    detail_remark += "（客观评分极佳，已忽略静态码率骤降警报）"
            elif ssim_v >= 0.95:
                short_explain = "画质优良，可删原版"
                if ignore_warnings and (tag_info["bitrate_drop"] or tag_info["low_bitrate"]):
                    detail_remark += "（客观评分优秀，已忽略静态码率骤降警报）"
            elif ssim_v >= 0.90:
                short_explain = "画质尚可，建议核对"
            else:
                short_explain = "画质下降，保留原版"
        else:
            # 未评分时的退回逻辑
            if tag_info["tag"] == "KEEP_ORIGINAL":
                reasons_list = []
                if tag_info["bitrate_drop"]: reasons_list.append("码率骤降")
                if tag_info["low_bitrate"]: reasons_list.append("低码率")
                short_explain = f"待核对（{' & '.join(reasons_list)}）"
            else:
                short_explain = "可删原版（未评分）"

        row = {
            "文件名": fname,
            "相对路径": rel,
            "大小MB_原": src_info["size_mb"],
            "大小MB_新": dst_info.get("size_mb", ""),
            "节省空间MB": saved_mb,
            "节省比例%": size_pct,
            "编码_原": src_info.get("codec", ""),
            "编码_新": dst_info.get("codec", ""),
            "视频码率kbps_原": src_info.get("video_bitrate_kbps", ""),
            "视频码率kbps_新": dst_info.get("video_bitrate_kbps", ""),
            "码率降低%": br_drop,
            "总码率kbps_原": src_info.get("total_bitrate_kbps", ""),
            "总码率kbps_新": dst_info.get("total_bitrate_kbps", ""),
            "色深_原": src_info.get("bit_depth", ""),
            "色深_新": dst_info.get("bit_depth", ""),
            "色彩空间_原": src_info.get("color_space", ""),
            "色彩空间_新": dst_info.get("color_space", ""),
            "是否HDR_原": "是" if src_info.get("is_hdr") else "否",
            "是否HDR_新": "是" if dst_info.get("is_hdr") else "否",
            "分辨率_原": src_info.get("resolution", ""),
            "分辨率_新": dst_info.get("resolution", ""),
            "帧率_原": src_info.get("fps", ""),
            "帧率_新": dst_info.get("fps", ""),
            "质量_SSIM": round(ssim_v, 4) if ssim_v is not None else "",
            "质量_PSNR_dB": ("∞" if psnr_v == float("inf") else round(psnr_v, 2)) if psnr_v is not None else "",
            "质量评级": ssim_rating(ssim_v),
            # 打钩列 (触发为 ✓，未触发为空)
            "无收益": "✓" if tag_info["no_benefit"] else "",
            "HDR损耗": "✓" if tag_info["hdr_loss"] else "",
            "码率骤降": "✓" if tag_info["bitrate_drop"] else "",
            "极低码率": "✓" if tag_info["low_bitrate"] else "",
            "分辨率改变": "✓" if tag_info["res_change"] else "",
            # 最终结论
            "综合建议": final_reco,
            "建议说明": short_explain,
            "备注": detail_remark,
        }
        rows.append(row)

        # 统计
        stats["total"] += 1
        stats["total_src_mb"] += src_info["size_mb"]
        if dst_info.get("exists"):
            stats["compressed_found"] += 1
            stats["total_dst_mb"] += dst_info["size_mb"]
            stats["total_saved_mb"] += max(0, saved_mb)
        else:
            stats["compressed_missing"] += 1
        # 根据最终的综合建议来进行分布统计
        if final_reco == "无收益-未压":
            stats["no_benefit"] += 1
        elif "保留原版" in final_reco or "待核对" in final_reco or "谨慎核对" in final_reco:
            stats["keep_original"] += 1
        elif "可删原版" in final_reco or "可接受" in final_reco:
            stats["acceptable"] += 1
        if src_info.get("is_hdr"):
            stats["hdr_count"] += 1

    # ── 写 CSV ──
    print(f"\n写入 CSV: {out_csv}")
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig for Excel
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    # ── 写 Excel（首行筛选 + 冻结表头）──
    out_xlsx = os.path.splitext(out_csv)[0] + ".xlsx"
    print(f"写入 Excel: {out_xlsx}")
    xlsx_ok = write_xlsx(rows, FIELDNAMES, out_xlsx)

    # ── 统计摘要 ──
    summary_path = out_csv.replace(".csv", "_summary.txt")
    summary = build_summary(stats, src_root, dst_root, rows)
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)

    print("\n" + summary)
    print(f"\nCSV 报告  : {out_csv}")
    if xlsx_ok:
        print(f"Excel 报告: {out_xlsx}")
    print(f"文字摘要  : {summary_path}")


def build_summary(stats: dict, src_root: str, dst_root: str, rows: list) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("  视频压缩对比报告摘要")
    lines.append(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)
    lines.append(f"源目录      : {src_root}")
    lines.append(f"压缩后目录  : {dst_root}")
    lines.append("")
    lines.append("── 文件统计 ──────────────────────────────────────────")
    lines.append(f"  源视频总数       : {stats['total']} 个")
    lines.append(f"  找到压缩版       : {stats['compressed_found']} 个")
    lines.append(f"  缺少压缩版       : {stats['compressed_missing']} 个")
    lines.append("")
    lines.append("── 空间统计 ──────────────────────────────────────────")
    lines.append(f"  原版总大小       : {human_mb(stats['total_src_mb'])}")
    lines.append(f"  压缩版总大小     : {human_mb(stats['total_dst_mb'])}")
    lines.append(f"  总节省空间       : {human_mb(stats['total_saved_mb'])}")
    if stats["total_src_mb"] > 0:
        lines.append(f"  整体压缩率       : {stats['total_saved_mb']/stats['total_src_mb']*100:.1f}%")
    lines.append("")
    lines.append("── 建议分布 ──────────────────────────────────────────")
    lines.append(f"  [OK]   可接受（可删除原版）  : {stats['acceptable']} 个")
    lines.append(f"  [KEEP] 建议保留原版          : {stats['keep_original']} 个")
    lines.append(f"  [SKIP] 压缩无收益            : {stats['no_benefit']} 个")
    lines.append(f"  [HDR]  含 HDR 10-bit 原版    : {stats['hdr_count']} 个")
    lines.append("")

    # 建议保留原版的文件列表
    keep_rows = [r for r in rows if "保留原版" in r["综合建议"] or "待核对" in r["综合建议"] or "谨慎核对" in r["综合建议"]]
    if keep_rows:
        lines.append("── 建议保留原版的文件 ────────────────────────────────")
        for r in keep_rows:
            lines.append(f"  {r['相对路径']}")
            lines.append(f"    原版: {r['大小MB_原']} MB, {r['编码_原']} {r['色深_原']} {r['视频码率kbps_原']} kbps")
            lines.append(f"    压缩: {r['大小MB_新']} MB, {r['编码_新']} {r['色深_新']} {r['视频码率kbps_新']} kbps")
            lines.append(f"    原因: {r['建议说明']} | 备注: {r['备注']}")
            lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def human_mb(mb: float) -> str:
    if mb >= 1024:
        return f"{mb/1024:.2f} GB"
    return f"{mb:.1f} MB"


if __name__ == "__main__":
    main()
