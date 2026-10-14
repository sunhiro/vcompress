#!/usr/bin/env python3
"""
vcompress_abav1.py - 基于 Rust 后端 ab-av1 的高级择优视频压缩与 VMAF 质量搜索引擎
支持多线程高性能模式 (--vmaf-threads 6，预算约 8GB RAM)，自动生成 CSV/Excel/HTML 三重台账。
已支持断点续压（自动跳过目标目录下已压过的产物）。
"""

import os
import re
import sys
import subprocess
import shutil
import argparse
import json
import logging
import csv
import fnmatch
import datetime as dt
from pathlib import Path
from export_report import build_excel_report, build_html_report
from naming import marked_name
from fullset import (read_manifest, write_manifest, manifest_row, full_hash,
                     plan_move_untouched, apply_moves, HASHING_AVAILABLE)

#: VAAPI 设备节点。与 `vcompress.py` 同一个环境变量、同一个默认值，
#: 保证两条引擎在同一台机器上接到同一块核显。
VAAPI_DEVICE = os.environ.get('VCAT_VAAPI_DEVICE', '/dev/dri/renderD128')

#: 认 `-preset` 的编码器（都是软编）。硬件编码器没有这个 AVOption，
#: 硬塞进去 ffmpeg 会直接报错退出，见 `run_ab_av1_encode` 里的说明。
_PRESET_ENCODERS = {'libx264', 'libx265', 'libsvtav1'}

#: ab-av1 用 `--stdout-format json` 时，**只有**「闸门拒了」会往 stdout 打这一行
#: （源码 `src/command/crf_search.rs`：那个 `println!` 挂在
#: `if let Error::NoGoodCrf { .. }` 里面），其余故障一律只走 stderr。
#: 见 `run_ab_av1_search` 的 docstring —— 这一行是「素材结论」与「机器故障」的
#: 唯一分界线，字符串本身是被当成契约用的，别随手改。
_ABAV1_NO_GOOD_CRF_MSG = "Failed to find a suitable crf"


class AbAv1SearchError(RuntimeError):
    """`ab-av1 crf-search` **自己跑挂了**（机器故障），不是闸门拒了。

    为什么要单独一个类型：这两件事在结果上都是「没拿到 CRF」，但一个该记成
    `quality_rejected`（素材结论，重跑还是这个结果），一个该记成 `failed`
    （机器故障，值得重试）。混成一个会让下游把「核显没接上」读成「这素材画质不行」——
    红线 §2 那一类假结论。见 `process_file_abav1` 第 7 步。
    """


def _log_file_handler():
    """日志文件的落点。

    🔴 **绝不落在当前工作目录。** 老版写死 `FileHandler("vcompress_abav1.log")`，
    落点是当时的 CWD：容器里的 WORKDIR 是 `/data`（用户挂载进来的素材目录），
    跑一次批就在人家的素材树下留一个日志文件；Mac 上也会写进随便 cd 到的目录。
    改为用户级状态目录，`VCOMPRESS_LOG_DIR` 可覆盖（`tests/conftest.py` 靠它
    把 import 期副作用圈进临时目录）。

    目录建不出来就退回只打 stdout —— 日志问题不许把跑批带崩。
    """
    base = os.environ.get('VCOMPRESS_LOG_DIR')
    try:
        log_dir = Path(base) if base else Path.home() / '.cache' / 'vcompress'
        log_dir.mkdir(parents=True, exist_ok=True)
        return logging.FileHandler(log_dir / 'vcompress_abav1.log', mode='a', encoding='utf-8')
    except (OSError, RuntimeError) as exc:
        print(f"[WARN] 日志文件不可写（{exc}），本次仅输出到 stdout", file=sys.stderr)
        return None


# 日志配置
_log_handlers = [logging.StreamHandler(sys.stdout)]
_file_handler = _log_file_handler()
if _file_handler is not None:
    _log_handlers.append(_file_handler)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=_log_handlers
)
logger = logging.getLogger("vcompress_abav1")

SUPPORTED_EXTENSIONS = {
    '.mp4', '.mov', '.m4v', '.avi', '.mkv', '.mts', '.m2ts',
    '.3gp', '.wmv', '.flv', '.webm', '.vob', '.mpg', '.rmvb'
}

#: 台账 CSV 的表头。🔴 **只能加列，不能改列序**——contrib/backfill_compress_report.py
#: 按位取 row[:7]，vcat 的 lineage.load_report 按第 7 列「决定」精确匹配筛行。
REPORT_HEADER = ['文件', '原MB', '新MB', '省%', 'VMAF', 'CRF/Q', '决定', '像素格式']

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
    """使用 ab-av1 crf-search 二分搜寻最佳 CRF/QP。

    返回值踩在两种「没拿到 CRF」上，**必须分开**：

    - ``dict``  —— 找到了达标的 CRF。
    - ``None``  —— **闸门拒了**：ab-av1 正常跑完，但这个素材既压不到
      ``min_vmaf`` 也省不下 ``max_encoded_percent``。这是**素材结论**，
      重跑一万次还是这个结果，记 ``quality_rejected``。

    机器故障则**抛 `AbAv1SearchError`**：ffmpeg 崩了、采样没有输出、
    JSON 解析不出来……这些是**机器故障**，重跑可能就好了，必须记 ``failed``。
    老版把这两种都 return None，于是「NAS 上核显没接上 / ffmpeg 少个滤镜」
    会被写成 `quality_rejected`，detail 还写着「未过质量门限(VMAF<93.0)」——
    **一条把机器故障说成素材差的假结论**，正是红线 §2 点名的东西。

    分界线是 ab-av1 自己给的：`--stdout-format json` 下，只有闸门拒了
    （源码里的 `Error::NoGoodCrf`）才会往 stdout 打一行带
    ``"type":"crf-search-error"`` 的 JSON，其余故障只写 stderr、stdout 是空的。
    """
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
        # errors='replace'：stderr 里混进非本编码的字节时（indicatif 的进度条、
        # 老编码素材的警告文案）不许把**已经成功的这次搜索**变成解码异常。
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, errors='replace')
    except subprocess.CalledProcessError as e:
        stdout = e.stdout or ""
        if _is_gate_rejection(stdout):
            logger.info("   [闸门] ab-av1 判定该素材压不到目标门限，正常结论（不是机器故障）。")
            return None
        raise AbAv1SearchError(
            f"ab-av1 退出码 {e.returncode}，且 stdout 没有闸门拒斥标记："
            f"{(e.stderr or '').strip()[-400:]}"
        ) from e
    except Exception as e:
        raise AbAv1SearchError(f"ab-av1 无法执行（{type(e).__name__}: {e}）") from e

    parsed = []
    for line in (res.stdout or "").strip().splitlines():
        try:
            data = json.loads(line.strip())
        except Exception:
            continue
        if isinstance(data, dict):
            parsed.append(data)

    # 🔴 **先认 type**，再退回老口径「任一带 crf 的行」。ab-av1 中途还会往
    # stdout 打 `sample-encode-done`（它的 `Update::SampleEncodeDone`），那种行
    # **也带 `crf`**，却没有 `vmaf` / `predicted_encode_*` —— 拿错了就会把
    # VMAF 记成 0、节省记成 0%，而台账上看起来只是「压得不好」。
    # 正常情况最后一行就是 `crf-search-done`，倒着找本来就先撞到它；
    # 留着兜底是**不拿 ab-av1 改个 type 名去赌整条路**。
    for data in reversed(parsed):
        if data.get("type") == "crf-search-done" and "crf" in data:
            return data
    for data in reversed(parsed):
        if "crf" in data:
            logger.warning("   [注意] ab-av1 没给 `crf-search-done` 行，"
                           "退用第一条带 crf 的记录（vmaf / 预估节省可能缺失）。")
            return data

    # 退出码 0 却读不出结果行：这不是「素材不行」，是工具或解析出了问题。
    raise AbAv1SearchError(
        f"ab-av1 退出码 0 但 stdout 里没有可解析的 crf 结果行：{(res.stdout or '').strip()[-400:]!r}"
    )


def _is_gate_rejection(stdout):
    """stdout 里有没有 ab-av1 那条「闸门拒了」的标记行。

    只认 `Error::NoGoodCrf` 打出来的那一种（`message` 是定值，见模块顶部
    `_ABAV1_NO_GOOD_CRF_MSG`）。认不出来就一律当机器故障 —— 方向是故意的：
    **宁可多报一次 failed（可重试），也不要把故障伪装成素材结论（不可逆的错误账）。**
    """
    for line in (stdout or "").strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if (isinstance(data, dict)
                and data.get("type") == "crf-search-error"
                and _ABAV1_NO_GOOD_CRF_MSG in str(data.get("message", ""))):
            return True
    return False


def run_ab_av1_encode(input_path, output_path, encoder_name, crf_val, preset=None, audio='copy'):
    """使用原生 FFmpeg 进行全量转码，隔离 Apple mebx 沉浸数据轨

    🔴 VAAPI 这条支路必须手工接硬件：**全量转码这一段是我们自己拼的 ffmpeg 命令**，
    ab-av1 的任何默认值在这里都不生效。老代码只把编码器名换成 `hevc_vaapi` 就发车，
    缺 `-vaapi_device` / `hwupload` —— 裸跑必然 `Impossible to convert between the
    formats`，等于 README 里 NAS 那一条路根本走不通。

    ⚠ **采样段和全量段不是同一套解码**，这个分裂要写下来（2026-10-14 核对 ab-av1
    源码 `src/command/args/encode.rs`）：我们对 `crf-search` **不传** `--enc-input`，
    而 ab-av1 对 `*_vaapi` 有内置输入默认值 —— `default_ffmpeg_input_args()` 里
    `e if e.ends_with("_vaapi") => &[("-hwaccel","vaapi"), ("-hwaccel_output_format","vaapi")]`，
    也就是说**它自己的 15 秒采样默认就是硬解**；我们这边只做硬编、绝不硬解。
    这不是疏忽而是红线 §9 的边界：§9 记录的是「硬解只在全片长跑时才暴露坏点，
    采样窗口碰不上」，所以采样的硬解可以留着。

    好在 ab-av1 没把这条路焊死（同一份源码：`--enc-input hwaccel=none
    --enc-input hwaccel_output_format=none` 可以摘掉默认值），**但摘掉之前先想清楚**：
    ab-av1 全篇没有 `hwupload`，摘掉硬解就得自己用 `--vfilter format=nv12,hwupload`
    把帧送上显存（`-vf` / `-filter:v` 在它那儿是保留字、会报错要求改用 `--vfilter`），
    否则 `hevc_vaapi` 拿到的还是系统内存里的软帧，照样 `Impossible to convert`。
    **这条路没验过**，别想当然，也别顺手去"统一"它。

    ⚠ 只做硬件**编码**，绝不加 `-hwaccel vaapi` 硬解：红线 §9，全长编码会在
    `Failed to sync surface` 上崩。采样段那个硬解是 ab-av1 自己的事，与这条命令无关。

    ⚠ 与 `vcompress.py:247-269` 那条**过了回归的**写法是**同一组参数、不是逐字相同**：
    那边把 `-vf` 放在 `-map` 之前、另加了 `-hide_banner -loglevel error` 与
    `-map_metadata 0`。`-vf` 是输出选项，位置不影响语义；元数据这边走的是
    exiftool 那一步。**别把「同源」当口号**——两条引擎的 ffmpeg 命令是各写各的，
    改这条时唯一可信的参照是真机跑一遍（R1）。

    `audio` 只由调用方在**重试**时传 `'aac'`（默认 `'copy'`，见 `process_file_abav1`
    第 8 步）：MP4/MOV 装不下 MP2/PCM 这类老音轨时 `-c:a copy` 会被 muxer 拒，
    整片编码因此白跑。**这不是「丢音轨」**——是换一种容器认的编码，红线 §10 禁的是
    静默丢，不是不许转码。刻意**不**开成 CLI 参数：备份引擎那个 `--audio copy|aac`
    是给用户手选的，这边自动退路够用，多一个开关就多一行要维护的文档。
    """
    enc_flag = map_encoder_flag(encoder_name)
    cmd = ['ffmpeg', '-y']
    if enc_flag == 'hevc_vaapi':
        # 输入侧：显式指定渲染节点（软编路径不需要）
        cmd.extend(['-vaapi_device', VAAPI_DEVICE])
    cmd.extend([
        '-i', str(input_path),
        '-map', '0:v:0',
        '-map', '0:a?',
        '-c:v', enc_flag,
        '-c:a', str(audio),
        '-movflags', '+faststart'
    ])
    if enc_flag == 'hevc_videotoolbox':
        cmd.extend(['-q:v', str(int(crf_val)), '-tag:v', 'hvc1'])
    elif enc_flag == 'libx265':
        cmd.extend(['-crf', str(crf_val), '-tag:v', 'hvc1'])
    elif enc_flag == 'libsvtav1':
        cmd.extend(['-crf', str(crf_val)])
    elif enc_flag == 'libx264':
        cmd.extend(['-crf', str(crf_val)])
    elif enc_flag == 'hevc_vaapi':
        # 输出侧：帧要从系统内存上传到显存，且 VAAPI 用 -qp，没有 -crf/-q:v
        cmd.extend(['-vf', 'format=nv12,hwupload', '-qp', str(int(crf_val)), '-tag:v', 'hvc1'])
    else:
        cmd.extend(['-q:v', str(crf_val)])

    if preset:
        # 🔴 只有这几个编码器有 `-preset` 这个 AVOption。hevc_vaapi / hevc_videotoolbox
        # 都没有——塞进去 ffmpeg 会 `Option preset not found` 直接报错退出。
        # 旧代码无条件追加，只是以前 vaapi 那条臂根本跑不起来，所以没人撞上；
        # **硬件接线修好之后 `-e vaapi --preset X` 就够得着了**（同一批修复开出的新口子）。
        # 丢掉用户的显式参数必须说出来，不能静默忽略。
        if enc_flag in _PRESET_ENCODERS:
            cmd.extend(['-preset', str(preset)])
        else:
            logger.warning(f"   [注意] {enc_flag} 无 -preset 选项，已忽略 --preset {preset}"
                           f"（硬塞会让 ffmpeg 报 'Option preset not found' 并失败）。")

    cmd.append(str(output_path))

    try:
        process = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if process.returncode != 0:
            err = process.stderr.decode('utf-8', errors='ignore')
            logger.error(f"FFmpeg 编码失败: {err}")
            return False, err
        return True, ""
    except Exception as e:
        logger.error(f"执行 FFmpeg 转码异常: {e}")
        return False, str(e)


# muxer 拒收音轨编解码的报错特征（`-c:a copy` 装不下 MP2/PCM 时 ffmpeg 的原文）。
# 🔴 退路**只**对着这类失败走：盘满 / SMB 断连 / VAAPI 故障重试也照样失败，
# 白跑一遍整片编码不说，日志还把原因硬说成「音轨装不下」——那是说假话。
_AUDIO_MUX_ERROR = re.compile(
    r'Could not find tag for codec|codec not currently supported in container',
    re.IGNORECASE,
)

def pix_column(src_pix, out_pix):
    """台账第 8 列「像素格式」：`源->产物`（ADR-019 决策 2/3、ADR-027）。

    ⚠ 产物探不出来要写 `源->?`，**不能回落成单值**——单值和「测了，没变化」
    长得一模一样，账上就分不出「没变」还是「没测出来」，那正是 ADR-027
    要消灭的半条记录。源片也探不出来时才退化（此时无从记变迁）。
    """
    if src_pix:
        return f"{src_pix}->{out_pix or '?'}"
    return out_pix or ""


# semi-planar 那一族的名字里**不带**色度数字，也不带位深后缀，只能查表。
# 🔴 别想用正则糊过去：`nv12` 的 12 不是位深也不是色度，`p010le` 的 010 才是位深。
#    VAAPI 用的正是这族名字（nv12 / p010），漏了它就会把 10bit 产物读成 8bit。
_SEMI_PLANAR = {
    'nv12': (8, '420'), 'nv21': (8, '420'), 'nv16': (8, '422'), 'nv24': (8, '444'),
    'p010': (10, '420'), 'p012': (12, '420'), 'p016': (16, '420'),
    'p210': (10, '422'), 'p216': (16, '422'),
    'p410': (10, '444'), 'p416': (16, '444'),
}

# 这些编码路径的产物位深/色度是**写死**的，不看源片。
# vaapi 支路写死 `-vf format=nv12,hwupload`，nv12 就是 8bit 4:2:0 —— 10bit 源进去
# 会被静默降位深；而目标硬件（J3455 / Apollo Lake Gen9 及以下）的核显本来也只能编
# 8bit HEVC，改 p010 也救不回来。所以正确做法是**拒绝**（红线 §11），不是偷偷降。
#
# 🔴 这份表与这几个函数跟 `vcompress.py:186-234` **逐字同源**（那边是单一出处），改一边就得改另一边。
FORCED_OUTPUT_PIX = {'vaapi': ('nv12', 8, '420')}


def _strip_endian(pix_fmt):
    return re.sub(r'(le|be)$', '', (pix_fmt or '').strip())


def pix_bit_depth(pix_fmt):
    """读位深：yuv420p10le -> 10、p010le -> 10、yuv420p -> 8。

    ⚠ 「认不出按 8 算」只对**探不出来的元数据**（`?` / `""`）成立——不据此拦人。
    对**认得出的格式名**，位深必须真解析：gray10le 曾一路漏到这里被按 8 放行，
    10-bit 灰度源绕过 bitdepth_guard 静默降成 8bit（2026-10-14 复检发现的真缺陷）。
    """
    base = _strip_endian(pix_fmt)
    if base in _SEMI_PLANAR:
        return _SEMI_PLANAR[base][0]
    m = re.search(r'p(\d+)$', base)          # 平面格式：位深跟在结尾的 p 后面
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    g = re.search(r'gray(\d+)$', base)       # gray 族：gray10le / gray12be …（裸 gray 是 8bit）
    if g:
        return int(g.group(1))
    return 8


def pix_chroma(pix_fmt):
    """读色度采样：444 / 422 / 420 / gray 等。认不出返回 '?'。"""
    base = _strip_endian(pix_fmt)
    if base in _SEMI_PLANAR:
        return _SEMI_PLANAR[base][1]
    if 'gray' in base:
        return 'gray'
    # 平面格式形如 yuv/yuvj/yuva + 三位色度数字，只在**开头那段**里找，
    # 避免把 yuv420p10 里的位深数字误读成色度。
    m = re.match(r'yuv[ja]?(\d{3})', base)
    if m and m.group(1) in ('444', '440', '422', '420', '411', '410'):
        return m.group(1)
    return '?'


def bitdepth_guard(src_pix, encoder, allow_downgrade=False):
    """🔴 位深守卫（红线 §11）：源片位深 > 该编码器写死的产物位深 → 拒绝。

    为什么不能交给闸门：VMAF **只在亮度通道上算**，对 10bit→8bit 的渐变色带
    (banding) 不敏感。「VMAF 94」只说明亮度没问题，对位深降级一个字都没说 ——
    而 banding 不可逆。闸门测不出来的损失，就不该由闸门顺手放行。

    放行返回 `None`；拒绝返回写进台账 decision 列的人话。
    抽成纯函数是为了能被 `tests/` 锁住（与 `pix_column` / `json_status_for` 同样处理）。
    """
    forced = FORCED_OUTPUT_PIX.get(encoder)
    if not forced:
        return None
    _fname, f_depth, _f_chroma = forced
    s_depth = pix_bit_depth(src_pix)
    if s_depth > f_depth and not allow_downgrade:
        return f"位深守卫(拒绝 {s_depth}bit→{f_depth}bit)"
    return None


def chroma_downgrade_note(src_pix, encoder):
    """色度降级：**默认放行，但必须留下痕迹**（红线 §11）。

    与位深不同：4:2:2→4:2:0 是 H.265 交付常规，真机上那 50 个 `yuvj422p` 全是老
    MJPEG SD 素材，影响极小；**且原片始终不动，事后可重压**。所以这里不拦，
    只是让日志说一句，真正的落点在台账第 8 列（实测的 `源->产物`）。
    """
    forced = FORCED_OUTPUT_PIX.get(encoder)
    if not forced:
        return None
    _fname, _f_depth, f_chroma = forced
    s_chroma = pix_chroma(src_pix)
    if s_chroma in ('?', 'gray') or s_chroma == f_chroma:
        return None
    return f"色度将由 {s_chroma} 降到 {f_chroma}（{src_pix or '?'} -> {_fname}）"


def chroma_guard(src_pix, encoder, refuse_downgrade=False):
    """色度守卫（红线 §11）：**只有显式要求时才拦**，默认放行。

    默认放行的理由是色度降级可逆地没那么严重：4:2:2→4:2:0 是 H.265 交付常规，
    原片始终不动、事后可重压。但「默认放行」不等于「不许有人不接受」——
    `--no-chroma-downgrade` 就是给那种场合的。

    🔴 与 `vcompress.py:712-716` 同一语义、同一开关名、同一状态串
    （`skipped_chroma_guard`）。跨引擎只对**默认行为**做逐格式对撞，因为备份引擎
    那段是内联在流程里的，没有可比的纯函数；这条只锁自己的行为。
    """
    if not refuse_downgrade:
        return None
    forced = FORCED_OUTPUT_PIX.get(encoder)
    if not forced:
        return None
    _fname, _f_depth, f_chroma = forced
    s_chroma = pix_chroma(src_pix)
    if s_chroma in ('?', 'gray') or s_chroma == f_chroma:
        return None
    return f"色度守卫(拒绝 {s_chroma}→{f_chroma})"


def json_status_for(status):
    """内部 status → `--json-out` 对外 status（供 vcat 的工人按状态显式分派）。
    🔴 四类**必须各走各的**，合并任意两类都会在下游变成记账错误：
      - `success`          压成功
      - `quality_rejected` 闸门拒了 —— **素材结论**，终态，重跑还是这个结果
      - `skipped`          规则跳过 —— 终态
      - 其余（含 failed）   **机器故障** —— 非终态，工人要删残片重做

    老版是 `status.startswith("skipped")` 里面再判 `status == "failed"`（恒假），
    于是 quality_rejected 一次都没发出去过，闸门拒绝与编码失败一起被当 failed，
    而工人对 failed 是「记终态不重试」→ **真正的机器故障永远不会被重做**。
    """
    if status == "success":
        return "success"
    if status == "quality_rejected":
        return "quality_rejected"
    if status.startswith("skipped"):
        return "skipped"
    return "failed"


def report_prune_targets(files, base_dir, out_root, preview):
    """本轮会重新产出一行的源片集合（报表行键 = 相对路径，逗号换空格）。

    续压守卫是「产物存在且非空 → 跳过」，而**跳过类文件根本不产出产物**，
    守卫拦不住，于是每轮都重走一遍并再 append 一次。真机上 69 个唯一文件
    被写成 131 行，而 HTML 仪表盘的 KPI 按行数汇总，「扫描了多少」是虚的。

    `--preview` 显式绕过续压守卫，所以它眼里**每个文件本轮都会重新产出**。
    """
    targets = set()
    for fp in files:
        rel = fp.relative_to(base_dir)
        op = out_root / marked_name(rel)
        if op.exists() and op.stat().st_size > 0 and not preview:
            continue  # 本轮会被续压守卫跳过，它的行原样留着
        targets.add(str(rel).replace(',', ' '))
    return targets


def prune_report_rows(rows, targets, preview):
    """从旧报表行里挑出该留下的。表头由调用方处理，这里不认表头。

    🔴 **`--preview` 只清它自己上一轮的 preview 行。** preview 眼里所有文件
    都会重跑，若照普通模式的判据走，会把**全部**旧行清掉——包括「保留压缩版」
    那些行，而那正是 vcat 血缘登记 `--report` 唯一认的行（`lineage.load_report`
    按决定筛）。它们带着 VMAF/CRF 与 ADR-027 加的第 8 列实测像素格式，而
    **preview 重新生成不出来**（它写的是 `PREVIEW成功`）。对一棵压完的树跑
    一次 preview（skill 标准两趟跑法的第一趟！）证据就没了。
    """
    def _drop(row):
        if not row or row[0] not in targets:
            return False
        if preview:
            # preview 唯一不能碰的是「保留压缩版」。⚠ 列数不足的畸形行也一并留下
            # （`len(row) > 6` 不成立即不删）——那种行内容未知，静默丢掉就是
            # 又一次「把数据丢失说成排版瑕疵」。
            return len(row) > 6 and row[6] != "保留压缩版"
        return True

    return [row for row in rows if not _drop(row)]


def prune_report_csv(report_csv, targets, preview):
    """就地重写报表，只留 `prune_report_rows` 挑中的行。返回是否真的写了。

    🔴 先写临时文件再 `os.replace` 就位。直接 `open('w')` 是**先截断后写**，
    中途崩/掉线就把整份报表清零——而那时 except 里那句「可能出现重复行」
    会把一次数据丢失说成排版瑕疵。同目录临时文件保证 replace 是同分区原子操作。
    """
    tmp = report_csv.with_suffix(".csv.tmp")
    try:
        with open(report_csv, encoding='utf-8-sig', newline='') as f:
            kept = prune_report_rows(list(csv.reader(f)), targets, preview)
        if kept and kept[0] and kept[0][0] == REPORT_HEADER[0]:
            kept = kept[1:]
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            w = csv.writer(f)
            w.writerow(REPORT_HEADER)
            w.writerows(kept)
        os.replace(tmp, report_csv)
        return True
    except OSError as e:
        logger.warning(f"报表旧行清理未完成，原报表保持不动，本轮可能出现重复行: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def process_file_abav1(src_path, out_path, args):
    """单文件处理逻辑：智能规则择优过滤 -> 评估 -> 编码 -> 元数据复制"""
    orig_bytes = src_path.stat().st_size
    orig_mb = orig_bytes / (1024 * 1024)

    # 0. 🔴 红线 §1：产物路径与源片是同一个文件时，**在动手之前**就拒绝。
    #
    #    单文件模式下 `out_path` 就是调用方给的 `-o`。`-i x.mp4 -o x.mp4` 这种误用
    #    会让全量编码的 ffmpeg `-y` **一边读一边截断同一个文件**——原片当场报废，
    #    而且不报任何错（红线 §1「绝不损坏或静默覆写原始文件」）。
    #
    #    老代码只在**失败之后**用 `samefile` 守卫，那时原片已经被覆盖过了，
    #    守卫挡的其实是第二次伤害（删掉那个文件）。这里把它提到编码之前。
    #
    #    ⚠ 今天这条路由断点续压的「产物已存在且非空 ⇒ 跳过」顺手挡住了——
    #    **那是巧合不是设计**：源片当然非空。换个布局或改掉续压判据它就漏了。
    #    「当前走不到」不是不设防的理由。
    try:
        if out_path.exists() and out_path.samefile(src_path):
            logger.error(f"   [拒绝] 输出路径与源片是同一个文件，绝不覆写原片: {out_path}")
            return "failed", orig_mb, 0.0, 0.0, "NA", "NA", "输出路径等于源片(拒绝覆写)", ""
    except OSError:
        # 探不到（权限/竞态）时不在这里拦——真到写那一步 ffmpeg 会自己失败，
        # 不会静默覆写一个我们连 stat 都做不到的文件。
        pass

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
    #    🔴 判据必须用 pix_bit_depth() 真解析，不能拿 `'12' in pix_fmt` 这种子串去糊：
    #    老写法里 `'12' in 'nv12'` 成立、`'10' in 'yuv410p'` 也成立，于是
    #    **普通 8bit 源被当成 10bit HDR 跳过**，而且报出来的理由是假的。
    src_depth = pix_bit_depth(pix_fmt)
    if args.skip_hdr and src_depth > 8:
        logger.info(f"   [跳过: {src_depth}-bit HDR] 像素格式 {pix_fmt} 位深 {src_depth}bit > 8bit，转码耗时长性价比低，跳过。")
        return "skipped_hdr", orig_mb, 0.0, 0.0, "NA", "NA", f"{src_depth}-bit HDR(跳过)", pix_fmt

    # 4. 位深守卫（红线 §11）—— 放在搜索**之前**：拦下来就不必白跑一次二分。
    verdict = bitdepth_guard(pix_fmt, args.encoder, args.allow_bitdepth_downgrade)
    if verdict:
        logger.warning(
            f"   [跳过: 位深守卫] 源片 {pix_fmt} 是 {src_depth}bit，而 {args.encoder} 路径固定输出 "
            f"8bit —— 会静默降位深，且 VMAF 只测亮度、测不出 banding。"
            f"确需如此用 --allow-bitdepth-downgrade。")
        return "skipped_bitdepth_guard", orig_mb, 0.0, 0.0, "NA", "NA", verdict, pix_fmt

    # 5. 色度守卫 / 留痕（红线 §11）—— 默认放行，只有 --no-chroma-downgrade 才拦。
    verdict = chroma_guard(pix_fmt, args.encoder, args.no_chroma_downgrade)
    if verdict:
        logger.warning(f"   [跳过: 色度守卫] 源片 {pix_fmt} 的色度会被降到 4:2:0，"
                       f"而 --no-chroma-downgrade 生效。")
        return "skipped_chroma_guard", orig_mb, 0.0, 0.0, "NA", "NA", verdict, pix_fmt

    note = chroma_downgrade_note(pix_fmt, args.encoder)
    if note:
        logger.info(f"   [注意] {note} —— VMAF 不评估色度，已记入台账第 8 列。")

    # 6. 择优规则过滤 3：跳过本身码率极低的视频
    if bitrate_mbps < args.min_bitrate:
        logger.info(f"   [跳过: 极低码率] 视频码率 {bitrate_mbps:.2f}Mbps < 最低限制 {args.min_bitrate:.2f}Mbps，原样保留。")
        return "skipped_low_bitrate", orig_mb, 0.0, 0.0, "NA", "NA", "极低码率(跳过)", pix_fmt

    # 7. 运行 ab-av1 二分查找测算最佳 CRF/QP
    logger.info(f"   [ab-av1 二分寻找] 目标 VMAF >= {args.vmaf_min}, 最低空间节省率 >= {args.min_saving_pct}%, VMAF线程={args.vmaf_threads}...")
    max_percent = 100.0 - args.min_saving_pct

    search_res = None
    try:
        search_res = run_ab_av1_search(
            src_path, args.encoder,
            min_vmaf=args.vmaf_min,
            max_percent=max_percent,
            sample_sec=args.sample_sec,
            samples_cnt=args.samples,
            vmaf_threads=args.vmaf_threads,
            scale_1080p=args.vmaf_scale_1080p
        )
    except AbAv1SearchError as e:
        # 🔴 机器故障，**不是**素材结论：ab-av1 崩了 / 没输出 / 跑不起来。
        # 老版这里 return None 一路落到下面那句「未过质量门限」，把「核显没接上」
        # 记成了「这素材画质不行」——一条不可逆的假账（红线 §2）。
        logger.error(f"   [失败: 工具故障] ab-av1 质量搜索没有跑完: {e}")
        return "failed", orig_mb, 0.0, 0.0, "NA", "NA", f"ab-av1 工具故障: {e}", pix_fmt

    if search_res is None:
        # 闸门拒了 = 素材结论（终态，重跑还是这个结果）。两个门都要点名：
        # ab-av1 的 NoGoodCrf 同时覆盖「VMAF 不达标」和「省不够」两种，
        # 只写 VMAF 会在「省不够」那一半上说假话。
        logger.warning(f"   [拦截/跳过] ab-av1 质量搜索未找到达标 CRF "
                       f"(无法兼顾 VMAF {args.vmaf_min} 分与 {args.min_saving_pct}% 空间节省)。")
        return ("quality_rejected", orig_mb, 0.0, 0.0, "NA", "NA",
                f"未过质量门限(VMAF<{args.vmaf_min} 或 省不到 {args.min_saving_pct}%)", pix_fmt)

    best_crf = search_res.get("crf")
    best_vmaf = round(search_res.get("vmaf", 0.0), 2)
    pred_percent = search_res.get("predicted_encode_percent", 100.0)
    pred_saving = round(100.0 - pred_percent, 1)
    pred_size_mb = search_res.get("predicted_encode_size", 0) / (1024 * 1024)

    logger.info(f"   ab-av1 搜寻成功: 最佳 Quality/CRF={best_crf} | VMAF={best_vmaf} | 预估省 {pred_saving}% | 预估产物大小 {pred_size_mb:.1f}MB")

    if args.preview:
        logger.info("   ✅ [PREVIEW 成功] （Preview 模式试压测算完毕，不保存文件）。")
        return "success", orig_mb, pred_size_mb, pred_saving, best_vmaf, best_crf, "PREVIEW成功", pix_fmt

    # 8. 执行全量转码
    logger.info(f"   [全量转码中] 目标文件: {out_path}...")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ok, encode_err = run_ab_av1_encode(src_path, out_path, args.encoder, best_crf, args.preset)
    if not ok and _AUDIO_MUX_ERROR.search(encode_err or ""):
        # 🔴 老音轨退路（2026-10-14 从备份引擎 vcompress.py:861-871 补齐）。
        # DVD 抓轨、老相机 AVI 的音轨常是 MP2/PCM，MP4/MOV 装不下，`-c:a copy`
        # 会被 muxer 拒——闸门好不容易过了，整片编码倒在这一步。
        # 退成 aac 重来一次。**绝不是丢音轨**：换一种容器认的编码而已，红线 §10
        # 禁的是「静默丢」，不是不许转码。宁可笑纳一次白跑，也不静默丢声音。
        # ⚠ 只对 muxer 拒收音轨这类失败走（2026-10-14 复检收紧）：盘满、SMB 断连、
        # VAAPI 故障重试也救不回来，白跑一遍整片编码纯属浪费，如实报 failed 即可。
        logger.warning("   [重试] 音轨 copy 被 muxer 拒收（源音频格式 MP4/MOV 装不下？），改用 aac 重来一次…")
        ok, encode_err = run_ab_av1_encode(src_path, out_path, args.encoder, best_crf, args.preset, audio='aac')
    if not ok or not out_path.exists():
        if encode_err:
            logger.error(f"   [失败] 全量编码异常终止：{_AUDIO_MUX_ERROR.sub('', encode_err).strip()[:300]}")
        else:
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

    # 实测产物真实节省率二次校验门限（全量落盘防膨胀/微小收益兜底）
    # ab-av1 仅抽样 3 段 15s 切片预测，长视频动态场景丰富可能导致全量落地体积踩线甚至反弹
    if actual_saving < args.min_saving_pct:
        logger.warning(
            f"   [拦截/跳过] 全量编码后真实节省率 {actual_saving}% < 最低要求 {args.min_saving_pct}%"
            f"（产物 {new_mb:.1f}MB vs 原片 {orig_mb:.1f}MB），产物已丢弃，保留原片。"
        )
        try:
            out_path.unlink()
        except OSError as e:
            logger.error(f"   [清理失败] 节省空间不足的产物删除失败: {out_path} ({e})")
        return "skipped_saving", orig_mb, 0.0, 0.0, best_vmaf, best_crf, "空间节省不足(跳过 <20%)", pix_fmt

    preserve_metadata(src_path, out_path)

    # ADR-019 决策 2/3：色度/位深降级**默认放行，但一律记录**，而且记的必须是
    # 压完后 ffprobe 实测的产物格式，不是按编码器推断的。此前这里只写了源片
    # 那一半，于是「放行」照做、「记录」没做——2026-09-03 实测查出 46 个
    # yuvj422p 源片的产物变成 yuv420p（色度减半），账上一个字都看不出来。
    out_pix = get_video_metadata(out_path)[4] or ""
    pix_col = pix_column(pix_fmt, out_pix)     # 判据与理由见 pix_column 的 docstring
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
        # 配不上就抛，绝不返回一个「差不多」的名字：返回原名会让下游把产物
        # 连到它自己，或连到一个碰巧同名的无关文件（红线：宁可拒绝，不可错）。
        # 与 `naming.py` / vcat `compress_worker.py` 同一口径（ADR-020）。
        raise ValueError(f"不是压缩产物名（缺 {{COMPRESSED_MARKER}} 标记）：{{rel_path}}")
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
    parser.add_argument('--allow-bitdepth-downgrade', action='store_true',
                        help='允许 10bit 源被降到 8bit（默认拒绝：banding 不可逆且 VMAF 测不出）')
    parser.add_argument('--no-chroma-downgrade', action='store_true',
                        help='拒绝色度降级，如 4:2:2→4:2:0（默认放行但一律记入报表第 8 列）')
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
    parser.add_argument('--exclude-pattern', type=str, default='*._*,*_LRF.*,*.lrf,*.dng',
                        help='通配符排除文件模式列表（逗号分隔，默认排除 AppleDouble 残渣、相机低清代理 LRF 及 RAW DNG）')
    parser.add_argument('--json-out', action='store_true', help='单文件模式：处理完毕后向 stdout 输出一行 JSON 结果（供 vcat compress-worker 解析）')

    args = parser.parse_args()
    check_dependencies()

    if args.input.is_file():
        files = [args.input]
        base_dir = args.input.parent
    else:
        exclude_patterns = [p.strip().lower() for p in args.exclude_pattern.split(',') if p.strip()] if getattr(args, 'exclude_pattern', None) else []
        all_candidates = [p for p in args.input.rglob('*') if p.suffix.lower() in SUPPORTED_EXTENSIONS and not p.name.startswith('._')]
        files = []
        for p in all_candidates:
            name_lower = p.name.lower()
            str_lower = str(p).lower()
            if exclude_patterns and any(fnmatch.fnmatch(name_lower, pat) or fnmatch.fnmatch(str_lower, pat) for pat in exclude_patterns):
                continue
            files.append(p)
        base_dir = args.input

    out_dir = args.output if args.output.is_dir() or not args.output.suffix else args.output.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    report_csv = out_dir / "_report.csv"
    report_xlsx = out_dir / "_report.xlsx"
    report_html = out_dir / "_report.html"

    if not report_csv.exists():
        with open(report_csv, 'w', encoding='utf-8', newline='') as f:
            csv.writer(f).writerow(REPORT_HEADER)
    elif args.input.is_dir():
        # 开跑前把「本轮要重新处理」的旧行清掉，之后照旧逐条追加。
        # 判据与它挡住的两个事故（跳过类行每轮重复追加 / preview 抹掉血缘证据）
        # 都写在 report_prune_targets 与 prune_report_rows 的 docstring 里，
        # 由 tests/test_report_prune.py 锁着（含变异验证）。
        #
        # 🔴 **只在目录模式做**。单文件模式（工人那条路）`out_dir` 是产物的父目录，
        # 动的是**整棵树共用的那份报表**，而单文件模式的行键是裸文件名、树里的
        # 行键是相对路径，根本对不上——删不掉任何行，却把整个文件重写一遍。
        # 纯风险零收益。
        prune_report_csv(
            report_csv,
            report_prune_targets(files, base_dir, args.output, args.preview),
            args.preview,
        )

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
            # 四类结论必须各走各的，理由见 json_status_for 的 docstring；
            # 由 tests/test_json_status.py 锁着（含把它折回旧版的变异验证）。
            json_status = json_status_for(status)
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
