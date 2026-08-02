#!/usr/bin/env python3
"""vcompress —— H.265 压缩 + VMAF 质量闸门引擎。

**来源与许可（务必先读）**
本文件迁移自 `vcat` 项目的 `contrib/compress_videos_nas.py`（2026-08-02 迁移），
而那份又源自用户本机的 `compress_videos.py`。**vcat 采用 AGPL-3.0，本仓采用 MIT**，
本次迁移即一次**降级重新许可**；两边版权同属一人（sunhiro），故可行。
迁移原则：**能力、红线注释、真机证据一字不删** —— 那些 🔴 段落不是装饰，
每一条背后都是数小时到数天的真机代价，删掉它们等于把防回归的机制删了只留代码。

**支持三条编码路径**
1. `vaapi` —— Intel 核显。群晖 J3455 实测：x265 软编 0.055x 实时（29.8GB 要 91 小时），
   核显 hevc_vaapi 1.28x 实时（同一批 3.9 小时），**差 23 倍**。
   （容器里 libva2 本就有，但**不装 VA 驱动不会自动生效**；装 intel-media-va-driver 后 iHD 才认得核显。）
2. `vt` —— Apple VideoToolbox（Mac 硬件）。
3. `x265` —— 软件编码，哪都能跑，慢。

**扩展名补齐过**：原版 `SUPPORTED_EXTENSIONS` 没有 `.vob/.mpg/.rmvb`，而真机上收益最高的
那 5.66GB 恰好全是 DVD 抓轨的 mpeg2video —— 不补就**静默漏掉**，不报错，就当它们不存在。

**`--file-list` 按清单派工**：上游（vcat 台账）已经算好该压哪些，按清单比让脚本自己走目录准确。

质量闸门（VMAF ≥ 阈值 且 省 ≥ 阈值）、**原片绝不改动**、`_report.csv` 格式，全部照旧。

**红线清单见 `docs/RED-LINES.md`；已知坏例与预期结论见 `docs/REGRESSION.md`。**

🔴 **闸门的适用边界（见 docs/RED-LINES.md §闸门边界；vcat 侧对应 ADR-019）**：
VMAF 是**只在亮度通道上计算**的指标 —— 它**不评估色度**，对 10bit→8bit 的渐变色带
（banding）也不敏感。所以「VMAF 94」只说明**亮度**没问题，对色度降级、位深降级
一个字都没说。这两类损失因此**不能靠闸门放行**，另设了守卫：
  - 位深降级：默认**拒绝**（`--allow-bitdepth-downgrade` 才放行）
  - 色度降级：默认放行，但**一律记入报表第 8 列**（`--no-chroma-downgrade` 可改为拒绝）
判据是「闸门测不出来的损失，不该由闸门顺手放行」。
"""

import os
import re
import sys
import subprocess
import shutil
import argparse
import json
import logging
import random
import tempfile
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("vcompress.log", mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger("video_compressor")

SUPPORTED_EXTENSIONS = {
    '.mp4', '.mov', '.m4v', '.avi', '.mkv', '.mts', '.m2ts', '.3gp', '.wmv', '.flv', '.webm',
    # 以下为 NAS 版补齐：真机 A 档（古董编码）大头全在这里，原版会静默漏掉
    '.vob', '.mpg', '.mpeg', '.m2p', '.vro',   # DVD 抓轨 / mpeg2video
    '.rmvb', '.rm',                             # RealMedia
    '.asf', '.divx', '.mod', '.tod',            # 老相机 / 老录像
}

def check_dependencies():
    """Verify that required binaries are installed."""
    for binary in ['ffmpeg', 'ffprobe', 'exiftool']:
        if shutil.which(binary) is None:
            logger.error(f"Required dependency '{binary}' is not installed or not in PATH.")
            if binary == 'exiftool':
                logger.error("Please install exiftool via: brew install exiftool")
            sys.exit(1)

def find_vmaf_models():
    """Find VMAF JSON models in the local file system (Homebrew default paths)."""
    search_paths = [
        Path('/opt/homebrew'),
        Path('/usr/local')
    ]
    model_dir = None
    for sp in search_paths:
        if not sp.exists():
            continue
        # Quick search for vmaf_v0.6.1.json
        for path in sp.glob('**/vmaf_v0.6.1.json'):
            model_dir = path.parent
            break
        if model_dir:
            break
            
    if not model_dir:
        # Fallback to standard homebrew model directory
        model_dir = Path('/opt/homebrew/share/model')
        
    model_hd = model_dir / 'vmaf_v0.6.1.json'
    model_4k = model_dir / 'vmaf_4k_v0.6.1.json'
    
    return model_hd, model_4k

def get_video_metadata(file_path):
    """Probe video for height, codec, duration, and bitrate in a single run."""
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
        logger.error(f"Failed to probe file {file_path}: {e}")
        return None, 0, 0.0, 0

VAAPI_DEVICE = os.environ.get('VCAT_VAAPI_DEVICE', '/dev/dri/renderD128')

# 混合 seek 的预卷秒数：输入端 -ss 只到关键帧粒度，留这么多秒让输出端 -ss 精确走完。
# 太小可能落在关键帧之后导致取不满；10 秒对常见 GOP（≤10s）足够。
SEEK_PREROLL = 10.0

# 近无损对照的量化器：这一档下画面几乎不可能有可见损失。
# 一个窗口在这一档还测出低分，只可能是**测量路径本身**坏了，不是素材压不动。
# （vt 的 -q:v 是 1~100 越大越好，与 QP/CRF 方向相反。）
CONTROL_Q = {'vaapi': 1, 'x265': 0, 'vt': 100}


def probe_pix_fmt(file_path):
    """单独探 pix_fmt。刻意不塞进 get_video_metadata —— 那个函数的返回值签名
    被 process_file 和 contrib/diag_vmaf_window.py 同时依赖，改签名会连坐。"""
    try:
        out = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=pix_fmt', '-of', 'csv=p=0', str(file_path)],
            capture_output=True, text=True, timeout=60)
        return out.stdout.strip() or "?"
    except Exception:
        return "?"


# semi-planar 那一族的名字里**不带**色度数字，也不带位深后缀，只能查表。
# 🔴 别想用正则糊过去：`nv12` 的 12 不是位深也不是色度，`p010le` 的 010 才是位深。
#    VAAPI 用的正是这族名字（nv12 / p010），漏了它就会把 10bit 产物读成 8bit。
_SEMI_PLANAR = {
    'nv12': (8, '420'), 'nv21': (8, '420'), 'nv16': (8, '422'), 'nv24': (8, '444'),
    'p010': (10, '420'), 'p012': (12, '420'), 'p016': (16, '420'),
    'p210': (10, '422'), 'p216': (16, '422'),
    'p410': (10, '444'), 'p416': (16, '444'),
}


def _strip_endian(pix_fmt):
    return re.sub(r'(le|be)$', '', (pix_fmt or '').strip())


def pix_bit_depth(pix_fmt):
    """读位深：yuv420p10le -> 10、p010le -> 10、yuv420p -> 8。认不出按 8 算（保守）。"""
    base = _strip_endian(pix_fmt)
    if base in _SEMI_PLANAR:
        return _SEMI_PLANAR[base][0]
    m = re.search(r'p(\d+)$', base)          # 平面格式：位深跟在结尾的 p 后面
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
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


# 这些编码路径的产物位深/色度是**写死**的，不看源片。
# vaapi 分支的 `-vf format=nv12` 就是 8bit 4:2:0 —— 10bit 源进去会被静默降位深，
# 而 J3455(Apollo Lake, Gen9) 的核显本来也只能编 8bit HEVC，改 p010 也救不回来。
FORCED_OUTPUT_PIX = {'vaapi': ('nv12', 8, '420')}


def encode_video(input_path, output_path, encoder, q_val, preset, audio_mode="copy",
                 vt_bitrate=None, clip=None, video_only=False):
    """Compress video using x265 (software), videotoolbox (mac) or vaapi (Intel 核显).

    clip=(ss, dur): 只编码从 ss 秒起、时长 dur 秒的片段(试压窗口用)。
    video_only=True: 不带音轨（试压窗口一律如此，理由见下方 clip 分支注释）。
    """
    # 音轨参数在这里一次决定，别在后面对 cmd 列表做手术——那种写法一改参数顺序就悄悄失效
    amap = [] if video_only else ['-map', '0:a?']
    acodec = ['-an'] if video_only else ['-c:a', audio_mode]
    if encoder == 'vaapi':
        # 🔴 **只做硬件编码，不做硬件解码**（没有 -hwaccel vaapi，只有 -vaapi_device）。
        #
        # 真机教训：带上 -hwaccel vaapi 后，15 秒的试压窗口都过得去，但 26 分钟的
        # DVD 抓轨全片编码到中途必崩——`Failed to sync surface: internal decoding error`。
        # 核显解码器对付老旧/有瑕疵的 MPEG-2 不够健壮，而窗口只解码一小段碰不上坏点，
        # 于是「测量通过 → 全片失败」，最难查的那种。
        #
        # 去掉硬件解码零代价：实测同一素材两种写法产物大小与 VMAF 完全一致
        # （软件解码 SD/1080p 对 CPU 压力远小于编码）。健壮性是白拿的。
        cmd = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-vaapi_device', VAAPI_DEVICE,
            '-i', str(input_path),
            '-vf', 'format=nv12,hwupload',
            '-map', '0:v:0', *amap,
            '-c:v', 'hevc_vaapi',
            '-qp', str(q_val),
            '-tag:v', 'hvc1',
            *acodec,
            '-map_metadata', '0',
            '-movflags', '+faststart+use_metadata_tags',
            str(output_path)
        ]
    elif encoder == 'vt':
        if vt_bitrate:
            bitrate_str = f"{int(vt_bitrate * 1000000)}"
            maxrate_str = f"{int(vt_bitrate * 1200000)}"
            bufsize_str = f"{int(vt_bitrate * 2000000)}"
            cmd = [
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                '-i', str(input_path),
                '-map', '0:v:0', *amap,
                '-c:v', 'hevc_videotoolbox',
                '-b:v', bitrate_str,
                '-maxrate', maxrate_str,
                '-bufsize', bufsize_str,
                '-tag:v', 'hvc1',
                *acodec,
                '-map_metadata', '0',
                '-movflags', '+faststart+use_metadata_tags',
                str(output_path)
            ]
        else:
            cmd = [
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                '-i', str(input_path),
                '-map', '0:v:0', *amap,
                '-c:v', 'hevc_videotoolbox',
                '-q:v', str(q_val),
                '-tag:v', 'hvc1',
                *acodec,
                '-map_metadata', '0',
                '-movflags', '+faststart+use_metadata_tags',
                str(output_path)
            ]
    else:
        cmd = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-i', str(input_path),
            '-map', '0:v:0', *amap,
            '-c:v', 'libx265',
            '-preset', preset,
            '-crf', str(q_val),
            '-tag:v', 'hvc1',
            *acodec,
            '-map_metadata', '0',
            '-movflags', '+faststart+use_metadata_tags',
            str(output_path)
        ]
        
    # 片段编码: 混合 seek —— 输入端快速 seek 到窗口前 PREROLL 秒(关键帧粒度, 几乎不花时间),
    # 再输出端精确 seek 走完那几秒(帧精确)。VMAF 参考侧用完全相同的 seek 参数, 两边逐帧对齐。
    #
    # 🔴 别改回「只用输出端 -ss」：那样每个窗口都要从文件头解码到窗口位置，
    #    26 分钟的 VOB 单个窗口就要 11 分钟(真机实测)。
    #
    # 🔴 参照物**不许是重编码产物**（2026-07-29 bug #1）：试过用 x264 -qp 0 抠无损中间片，
    #    出的是 High 4:4:4 Predictive，色彩范围与下游编码器不一致，硬件和软件编码器对同一
    #    抠片都只得 7.4/7.3 分——画面结构一样但亮度整体偏移，45 个文件跑 21 小时零通过。
    #
    #    ⚠ 这条曾被写成「参照物必须是原片本身，别用中间文件」——**推广过头了**（见 docs/RED-LINES.md §参照物）。
    #    失败的是「重编码」，不是「中间文件」。**无损流拷贝**出来的窗口不转码，
    #    就是原片的那几帧字节，没有色彩范围问题。ab-av1 正是这么做的：
    #        ffmpeg -ss X -i in -frames:v N -c:v copy -an -sn sample.mkv
    #    然后编码这个 sample、并与这个 sample 比 VMAF —— **窗口只被确定一次，
    #    两侧共用同一个文件，结构上不可能错位**。这正是 P2-41 的解法方向：
    #    我们现在的错位，根子就在窗口被两把尺子各量了一遍（编码侧按解码帧、参照侧按 PTS）。
    if clip:
        ss, dur = clip
        pre = min(ss, SEEK_PREROLL)
        i = cmd.index('-i')
        cmd[i:i] = ['-ss', f"{ss - pre:.2f}"]          # 输入端(快)
        i = cmd.index('-i')
        cmd[i + 2:i + 2] = ['-ss', f"{pre:.2f}", '-t', f"{dur:.2f}"]  # 输出端(准)

        # 🔴 试压窗口**只编码视频**（调用方传 video_only=True）。VMAF 只看画面，
        #    带上音频没有任何好处，却把 seek 的脆弱性引了进来：输入端 -ss 落在音频帧
        #    中间时，MPEG-2 节目流的 MP2 轨会直接 `Header missing` 让整条命令失败，
        #    留下残缺产物 —— 残片拿去比 VMAF 会得到「不随 QP 变化的平坦低分」
        #    （真机 30.58/30.78/30.94），看着像画质差，其实是文件坏了。
        # 注：窗口产物不含音频，saving 估算因此略偏乐观（音频通常占 1~4%）；
        #    最终写进报告的 saving 是全片编码后按实际字节重算的，不受影响。

    process = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if process.returncode != 0:
        logger.error(f"FFmpeg encoding failed with exit code {process.returncode}: {process.stderr.decode('utf-8', errors='ignore')}")
    return process.returncode == 0

def compute_vmaf(distorted, reference, height, duration, model_hd, model_4k, test_duration=30):
    """Run VMAF comparison between compressed (distorted) and original (reference) videos."""
    version = "vmaf_4k_v0.6.1" if height >= 2000 else "vmaf_v0.6.1"
    nthreads = os.cpu_count() or 4
    orig_duration = duration  # 由调用方传入, 避免重复 ffprobe

    # Helper to calculate VMAF for a single segment.
    # 关键: 用 trim 滤镜按 PTS 帧精确裁段(两路都归零 PTS), 而不是输入端 -ss。
    # 输入端 -ss 会分别 seek 到"压缩版"和"原片"各自的关键帧(GOP 不同)导致错位, 算出假的低分。
    def run_segment_vmaf(ss_time, duration):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            vlog_path = tf.name

        end = ss_time + duration
        filtergraph = (
            f"[0:v]trim=start={ss_time:.2f}:end={end:.2f},setpts=PTS-STARTPTS[d];"
            f"[1:v]trim=start={ss_time:.2f}:end={end:.2f},setpts=PTS-STARTPTS[r];"
            f"[d][r]libvmaf=model=version={version}:n_threads={nthreads}:log_path={vlog_path}:log_fmt=json"
        )
        vmaf_cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-i', str(distorted),
            '-i', str(reference),
            '-filter_complex', filtergraph,
            '-f', 'null', '-'
        ]
        try:
            process = subprocess.run(vmaf_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if process.returncode != 0:
                logger.error(f"VMAF FFmpeg failed for segment {ss_time:.1f}s: {process.stderr.decode('utf-8', errors='ignore')}")
                return None
                
            with open(vlog_path, 'r', encoding='utf-8') as f:
                vmaf_data = json.load(f)
                
            if "pooled_metrics" in vmaf_data and "vmaf" in vmaf_data["pooled_metrics"]:
                return vmaf_data["pooled_metrics"]["vmaf"]["mean"]
        except Exception as e:
            logger.error(f"Error in VMAF segment {ss_time:.1f}s: {e}")
        finally:
            if os.path.exists(vlog_path):
                os.unlink(vlog_path)
        return None

    # Check if video is long enough to run three-segment sampling
    # We want 3 segments of 15 seconds = 45 seconds total.
    # To run this safely, the video should be at least 50 seconds.
    segment_dur = 15.0
    required_dur = (segment_dur * 3) + 5.0 # 50 seconds
    
    if orig_duration >= required_dur:
        logger.info("   Computing three-segment VMAF (Beginning, Middle, End) for precision...")
        
        # Segment 1: Beginning (starts at 5s to avoid initial black/fade-in)
        s1_start = 5.0
        # Segment 2: Middle
        s2_start = (orig_duration / 2.0) - (segment_dur / 2.0)
        # Segment 3: End (ends 5s before the actual end to avoid final fade-out/credits)
        s3_start = orig_duration - segment_dur - 5.0
        
        scores = []
        for name, start in [("Beginning", s1_start), ("Middle", s2_start), ("End", s3_start)]:
            score = run_segment_vmaf(start, segment_dur)
            if score is not None:
                scores.append(score)
                logger.debug(f"      {name} segment VMAF: {score:.2f}")
            else:
                logger.warning(f"      {name} segment VMAF calculation returned NA.")

        if scores:
            # 取三段里最低分做闸门：压缩瑕疵最重的段(常在高速运动处)不会被平均稀释掉
            worst = min(scores)
            mean = sum(scores) / len(scores)
            logger.info(f"   Segment VMAF: min={worst:.2f} mean={mean:.2f} ({', '.join(f'{s:.1f}' for s in scores)})")
            return round(worst, 2)
        return "NA"
    else:
        # For short videos, test the entire video in a single run
        logger.info(f"   Short video ({orig_duration:.1f}s). Computing VMAF on full video...")
        score = run_segment_vmaf(0.0, orig_duration)
        return round(score, 2) if score is not None else "NA"

def compute_vmaf_sample(distorted_clip, reference, height, clip_ss, clip_dur, model_hd, model_4k):
    """distorted_clip 是用混合 seek 压出的片段(自身从 0 帧起, 帧精确)。

    参考侧用**与编码时完全相同的混合 seek**：输入端 -ss 快进到窗口前 PREROLL 秒,
    再用 trim 滤镜从 PREROLL 处帧精确裁出同样长度并把 PTS 归零 —— 两边逐帧对齐,
    且不必从文件头解码。

    🔴 参照物必须是**原片本身**，不许换成任何中间产物：试过先抠无损小片再比,
    x264 -qp 0 出的是 High 4:4:4 Predictive, 色彩范围与下游编码器不一致,
    硬件与软件编码器对同一抠片都只得 7.4/7.3 分(真机 45 个文件全被误判)。
    """
    version = "vmaf_4k_v0.6.1" if height >= 2000 else "vmaf_v0.6.1"
    nthreads = os.cpu_count() or 4
    pre = min(clip_ss, SEEK_PREROLL)
    end = pre + clip_dur

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        vlog_path = tf.name
    filtergraph = (
        f"[1:v]trim=start={pre:.2f}:end={end:.2f},setpts=PTS-STARTPTS[r];"
        f"[0:v][r]libvmaf=model=version={version}:n_threads={nthreads}:log_path={vlog_path}:log_fmt=json"
    )
    vmaf_cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-i', str(distorted_clip),
        '-ss', f"{clip_ss - pre:.2f}", '-i', str(reference),
        '-filter_complex', filtergraph,
        '-f', 'null', '-'
    ]
    try:
        process = subprocess.run(vmaf_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if process.returncode != 0:
            logger.error(f"Preview VMAF failed: {process.stderr.decode('utf-8', errors='ignore')}")
            return "NA"
        with open(vlog_path, 'r', encoding='utf-8') as f:
            vmaf_data = json.load(f)
        if "pooled_metrics" in vmaf_data and "vmaf" in vmaf_data["pooled_metrics"]:
            return round(vmaf_data["pooled_metrics"]["vmaf"]["mean"], 2)
        return "NA"
    except Exception as e:
        logger.error(f"Error computing preview VMAF: {e}")
        return "NA"
    finally:
        if os.path.exists(vlog_path):
            os.unlink(vlog_path)

def preserve_metadata(source, target):
    """Copy all Exif tags and creation/modification timestamps from source to target."""
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
        logger.error(f"Error preserving metadata: {e}")
        return False

def aggregate_windows(wins, orig_bytes, duration):
    """把若干**可信**窗口的测量汇总成一条结论：取最低分 + 按窗口字节外推省了多少。

    🔴 已判定「测量不可信」的窗口不许进来。取最低分是保守的，前提是每个分数都是
    真的画质分；混进一个坏掉的测量，最低分就成了坏窗口的一票否决。
    返回 (min_vmaf 或 'NA', saving_pct, est_new_mb, n_windows)。"""
    if not wins:
        return "NA", 0.0, 0.0, 0
    comp_bytes = sum(w["bytes"] for w in wins)
    win_secs = sum(w["wd"] for w in wins)
    min_v = round(min(w["score"] for w in wins), 2)
    orig_win_bytes = orig_bytes * (win_secs / duration) if duration > 0 else orig_bytes
    saving = ((orig_win_bytes - comp_bytes) / orig_win_bytes) * 100 if orig_win_bytes > 0 else 0.0
    est_new_mb = (orig_bytes / (1024 * 1024)) * (1 - saving / 100.0)
    return min_v, saving, est_new_mb, len(wins)


def _window_frame_counts(clip_path, reference, clip_ss, clip_dur):
    """数一数「失真侧」与「参考侧」各自认为这个窗口有多少帧。

    🔴 这是测量错位的**诊断签名**，只记日志、不参与任何判定。
    真机 2026-08-02：`IMG_2151.MOV` Middle 窗口两侧是 359 / 369 帧，
    逐帧分数「开头 99.5、结尾 7.2」—— 起点对齐、随后累积漂移。
    根因是两把尺子不同：编码侧输出端 `-ss`/`-t` 按解码帧计, 参考侧 `trim` 按 PTS 计,
    在轻微 VFR 素材上量出的**时间跨度**就不一样（两侧 fps 归一化救不了, 已实测证伪）。
    把这两个数留在日志里, 下次修根因的人不必再跑一轮诊断才能看见它。

    任何异常都吞掉返回 (-1, -1) —— 诊断信息绝不能拖垮批量。
    """
    try:
        pre = min(clip_ss, SEEK_PREROLL)
        end = pre + clip_dur
        p = subprocess.run(
            ['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0',
             '-show_entries', 'stream=nb_read_frames', '-of', 'csv=p=0', str(clip_path)],
            capture_output=True, text=True, timeout=600)
        dist = int(p.stdout.strip())
        r = subprocess.run(
            ['ffmpeg', '-hide_banner', '-nostdin',
             '-ss', f"{clip_ss - pre:.2f}", '-i', str(reference),
             '-vf', f"trim=start={pre:.2f}:end={end:.2f},setpts=PTS-STARTPTS",
             '-f', 'null', '-'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1800)
        m = re.findall(r'frame=\s*(\d+)', r.stderr)
        return dist, (int(m[-1]) if m else -1)
    except Exception as e:
        logger.debug(f"      帧数诊断跳过：{e}")
        return -1, -1


def measure_control_window(src_path, ss, wd, encoder, preset, audio, vt_bitrate,
                           height, model_hd, model_4k, temp_dir, control_q):
    """近无损对照：同一窗口用近乎无损的量化器再压一次, 再测一次 VMAF。

    🔴 这是「机器坏了」与「素材压不动」的分界线, 是 §4.1 那条红线的延伸。
    近无损产物在画面上与原片几乎无差别, 它的 VMAF **只可能**被测量路径本身拉低。
    所以: 对照分高 -> 此前的低分是素材的真实结论, 判画质不达标是对的;
          对照分也低 -> 测量路径坏了, 此前那个低分**不许**写成「画质不达标」。

    返回 float, 或 None（对照自己都没跑成 —— 同样按「测量不可信」处理,
    宁可报「需人工检查」也不拿一个跑不通的测量去下画质结论）。"""
    with tempfile.NamedTemporaryFile(dir=temp_dir, suffix=".mp4", delete=False) as tf:
        ctmp = Path(tf.name)
    try:
        ok = encode_video(src_path, ctmp, encoder, control_q, preset, audio,
                          vt_bitrate=vt_bitrate, clip=(ss, wd), video_only=True)
        if not ok or not ctmp.exists() or ctmp.stat().st_size == 0:
            logger.warning(f"      近无损对照 @{ss:.0f}s 编码失败")
            return None
        v = compute_vmaf_sample(ctmp, src_path, height, ss, wd, model_hd, model_4k)
        d, r = _window_frame_counts(ctmp, src_path, ss, wd)
        if d > 0 and r > 0:
            logger.info(f"      [诊断] 窗口 @{ss:.0f}s 两侧帧数 失真 {d} / 参考 {r}"
                        f"{'  ← 跨度不一致（测量错位签名）' if d != r else '  ← 一致'}")
        return None if v == "NA" else v
    finally:
        if ctmp.exists():
            ctmp.unlink()


def measure_quality_windowed(src_path, encoder, q_val, preset, audio, vt_bitrate,
                             duration, height, model_hd, model_4k, temp_dir, skip=()):
    """质量测量: 取若干 15s 窗口各自单独编码, 与**原片同窗口**逐帧比对, 取最低 VMAF。

    编码与参考两侧都走混合 seek(输入端快进 + 输出端精确), 参数完全一致 ->
    逐帧对齐, 且不必从文件头解码。参照物始终是原片本身, 不引入任何中间产物。
    skip: 本文件中已被近无损对照判定为「测量不可信」的窗口名, 直接不测。
    返回 (min_vmaf 或 'NA', saving_pct, est_new_mb, n_windows, wins)。"""
    seg = 15.0
    # 🔴 窗口起点必须让**输入端 seek 严格大于 0**（即 ss > SEEK_PREROLL），绝不从文件第 0 字节解起。
    #
    # 真机教训：起始窗口原本取 5.0s，而 pre=min(5,10)=5 → 输入端 seek 落在 0。
    # 对于**没有序列头的流片段**（DVD 的 VTS_01_2/_3.VOB 这类续集文件，start_time
    # 分别是 1609s/3219s，ffprobe 会报 `Invalid frame dimensions 0x0`），从字节 0 解码
    # 得到的前导坏帧数量**两次解码并不一致** —— 编码那一路和 VMAF 参考那一路各丢各的，
    # 时间轴就错开几帧。帧数明明相等（450=450）分数却只有 16.45，看着像画质崩了。
    #
    # 同一文件把起始窗口挪到 15s（seek=5>0）立刻回到 92.30，30s→91.97，60s→90.22。
    # 只要不从 0 起解就正常。这与「参照物必须是原片」「窗口不带音轨」是同一类教训：
    # **测量路径上任何不可复现的环节，都会伪装成画质结论。**
    begin_at = SEEK_PREROLL + 5.0
    if duration >= (begin_at + seg * 3 + 5):
        windows = [("Beginning", begin_at),
                   ("Middle", duration / 2.0 - seg / 2.0),
                   ("End", duration - seg - 5.0)]
        wlen = seg
    elif duration >= 8:
        # 单窗口路径同理：宁可偏离正中，也不让 seek 落在 0。
        # 残留：≤20 秒的短片仍会从 0 起解——但那种长度不可能是流片段
        # （12 秒的 DVD 续集文件不存在），且窗口本就覆盖全片，从头解正是对的。
        mid = duration / 2.0 - seg / 2.0
        windows = [("Middle", max(min(begin_at, max(0.0, duration - seg)), mid))]
        wlen = min(seg, duration)
    else:
        windows = [("Whole", 0.0)]
        wlen = duration

    orig_bytes = src_path.stat().st_size
    wins = []
    for name, ss in windows:
        if name in skip:
            logger.info(f"      窗口 {name}@{ss:.0f}s: 已判定测量不可信, 跳过")
            continue
        wd = min(wlen, max(0.0, duration - ss))
        if wd < 1.0:
            continue
        with tempfile.NamedTemporaryFile(dir=temp_dir, suffix=".mp4", delete=False) as tf:
            wtmp = Path(tf.name)
        try:
            ok = encode_video(src_path, wtmp, encoder, q_val, preset, audio,
                              vt_bitrate=vt_bitrate, clip=(ss, wd), video_only=True)
            if not ok or not wtmp.exists() or wtmp.stat().st_size == 0:
                logger.warning(f"      窗口 {name}@{ss:.0f}s 编码失败")
                continue
            v = compute_vmaf_sample(wtmp, src_path, height, ss, wd, model_hd, model_4k)
            if v != "NA":
                wins.append({"name": name, "ss": ss, "wd": wd,
                             "score": v, "bytes": wtmp.stat().st_size})
                logger.info(f"      窗口 {name}@{ss:.0f}s: VMAF={v}")
            else:
                logger.warning(f"      窗口 {name}@{ss:.0f}s: VMAF 算不出")
        finally:
            if wtmp.exists():
                wtmp.unlink()

    return (*aggregate_windows(wins, orig_bytes, duration), wins)


def process_file(src_path, out_path, args, model_hd, model_4k, info=None):
    """先用'窗口测量'廉价可靠地决定留/弃; 通过后(非 preview)再全片编码输出。原文件始终不动。

    info: 可选的外带字典, 用来回填 src_pix / out_pix 等诊断信息。
    刻意不加进返回值元组 —— 那个 6 元组有十来处 return, 逐个改极易漏掉一处,
    而漏掉的那处会静默返回错位的字段。
    """
    if info is None:
        info = {}
    orig_bytes = src_path.stat().st_size
    orig_mb = orig_bytes / (1024 * 1024)

    # 0. 小文件跳过: 体积过小(默认 < 100MB), 即使省50%也仅节省几十MB, 重新编码耗时算力不划算
    if orig_mb < args.min_file_mb:
        logger.info(f"   Skipping: 文件大小 {orig_mb:.1f} MB < 下限阈值 {args.min_file_mb:.1f} MB (小文件跳过重压, 直接原样复制)。")
        return "skipped_small_file", orig_mb, 0.0, 0.0, "NA", "NA"

    codec, height, duration, bit_rate = get_video_metadata(src_path)
    if not codec:
        return "skipped_unsupported", orig_mb, 0.0, 0.0, "NA", "NA"
    bitrate_mbps = bit_rate / 1000000.0 if bit_rate else 0.0

    # 1. 码率过低: 已被压得很狠, 再压收益低且伤画质, 跳过
    if bitrate_mbps > 0.0 and bitrate_mbps < args.min_bitrate:
        logger.info(f"   Skipping: 码率 {bitrate_mbps:.2f} Mbps < 下限 {args.min_bitrate} Mbps。")
        return "skipped_low_bitrate", orig_mb, 0.0, 0.0, "NA", "NA"

    # 2. HEVC 默认跳过: 已是高效编码, 重压又慢又低收益(4K60 尤甚), 且难以可靠测量质量。
    #    确需重压高码率 HEVC 时用 --recompress-hevc(会强制软件 x265)。
    encoder = args.encoder
    if codec in ['hevc', 'h265']:
        if not args.recompress_hevc:
            logger.info(f"   Skipping: 已是 HEVC({bitrate_mbps:.1f} Mbps), 默认不重压(高效编码, 收益低)。")
            return "skipped_already_h265", orig_mb, 0.0, 0.0, "NA", "NA"
        logger.info(f"   [--recompress-hevc] 重压 HEVC {bitrate_mbps:.1f} Mbps")
        if encoder == 'vt' and not args.allow_vt_hevc:
            logger.info("   HEVC 源 -> 强制软件 x265 (硬件无法高效重压 HEVC)")
            encoder = 'x265'

    # 2.5 位深 / 色度守卫 —— 🔴 因为**这类损失 VMAF 测不出来**。
    #
    # VMAF 是**只看亮度**的指标, 完全不评估色度; 对 10bit→8bit 造成的渐变色带(banding)
    # 也不敏感。于是「VMAF 94 分」只说明亮度没问题, 对色度降级与位深降级**一个字都没说**。
    # 闸门测不出来的损失, 就不该由闸门顺手放行 —— 这与「宁可拒绝执行, 不可冒险删除」同源。
    #
    # 两者代价不同, 所以规则也不同:
    # - **位深降级默认拒绝**: banding 不可逆, 且 10bit 素材通常是较新的珍贵原片。
    # - **色度降级默认放行但必须记录**: 4:2:2→4:2:0 是 H.265 交付常规, 老相机 SD 素材上
    #   影响极小; 真机实测本库 50 个 yuvj422p 全是老 MJPEG。**原片始终不动, 事后可重压**,
    #   所以这里可以不那么严 —— 但必须写进报表, 让人**知道**发生了什么。
    src_pix = probe_pix_fmt(src_path)
    info['src_pix'] = src_pix
    forced = FORCED_OUTPUT_PIX.get(encoder)
    if forced:
        _fname, f_depth, f_chroma = forced
        s_depth, s_chroma = pix_bit_depth(src_pix), pix_chroma(src_pix)
        if s_depth > f_depth and not args.allow_bitdepth_downgrade:
            logger.warning(
                f"   Skipping: 源片 {src_pix} 是 {s_depth}bit, 而 {encoder} 路径固定输出 "
                f"{f_depth}bit —— 会静默降位深, 且 VMAF 测不出 banding。"
                f"确需如此用 --allow-bitdepth-downgrade。")
            return "skipped_bitdepth_guard", orig_mb, 0.0, 0.0, "NA", "NA"
        if s_chroma not in ('?', 'gray') and s_chroma != f_chroma:
            if args.no_chroma_downgrade:
                logger.warning(
                    f"   Skipping: 源片 {src_pix} 是 {s_chroma}, {encoder} 路径固定输出 "
                    f"{f_chroma}（--no-chroma-downgrade 生效）。")
                return "skipped_chroma_guard", orig_mb, 0.0, 0.0, "NA", "NA"
            logger.info(f"   [注意] 色度将由 {s_chroma} 降到 {f_chroma}"
                        f"（{src_pix} -> {_fname}）—— VMAF 不评估色度, 已记入报表。")

    # 质量阶梯(基于解析后的 encoder)
    if encoder == 'vaapi':
        q_ladder = [int(x) for x in args.vaapi_qp_ladder.split()]
        q_label = "QP"
    elif encoder == 'vt':
        if args.vt_bitrate:
            q_ladder = [args.vt_bitrate]
            q_label = "Bitrate(Mbps)"
        else:
            q_ladder = [int(x) for x in args.vt_quality.split()]
            q_label = "Q"
    else:
        q_ladder = [int(x) for x in args.crf_ladder.split()]
        q_label = "CRF"

    temp_dir = out_path.parent
    temp_dir.mkdir(parents=True, exist_ok=True)

    best_ok = False
    best_vmaf = "NA"
    best_saving = 0.0
    best_q = None
    new_mb = 0.0

    # 如果开启了 --no-vmaf, 跳过复杂的窗口质量测量, 直接全速编码
    if args.no_vmaf:
        best_q = q_ladder[0]
        best_vmaf = "SKIPPED"
        logger.info(f"   [--no-vmaf] 跳过 VMAF 质量测量, 直接全速编码输出 ({q_label}={best_q})...")
        with tempfile.NamedTemporaryFile(dir=temp_dir, suffix=".mp4", delete=False) as tf:
            full_tmp = Path(tf.name)
        if not encode_video(src_path, full_tmp, encoder, best_q, args.preset, args.audio, vt_bitrate=args.vt_bitrate):
            if full_tmp.exists():
                full_tmp.unlink()
            return "failed", orig_mb, 0.0, 0.0, best_vmaf, best_q
        actual_new_bytes = full_tmp.stat().st_size
        new_mb = actual_new_bytes / (1024 * 1024)
        best_saving = ((orig_bytes - actual_new_bytes) / orig_bytes) * 100 if orig_bytes > 0 else 0.0
        
        # 安全防御: 如果无 VMAF 模式下压缩完体积反而变大或节省空间 < 15%, 放弃压缩版, 保护磁盘空间
        if actual_new_bytes >= orig_bytes or best_saving < args.min_saving_pct:
            logger.info(f"   [--no-vmaf] 压缩后体积未显著减少 (省{best_saving:.1f}% < {args.min_saving_pct}%), 丢弃压缩版, 留待后续原样拷贝。")
            if full_tmp.exists():
                full_tmp.unlink()
            return "failed", orig_mb, 0.0, best_saving, best_vmaf, best_q

        preserve_metadata(src_path, full_tmp)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists():
            out_path.unlink()
        shutil.move(str(full_tmp), str(out_path))
        subprocess.run(['touch', '-r', str(src_path), str(out_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "success", orig_mb, new_mb, best_saving, best_vmaf, best_q

    # 逐档做"窗口测量"(廉价、可靠), 通过即定档
    #
    # 🔴 低分在写成结论之前必须先过近无损对照（P2-41）。
    # 真机血泪：19 个 .MOV 在**最保守的 QP20** 下拿到 VMAF 5.93~49.51 —— 这个分数
    # 物理上不可能是画质结论（QP20 近乎无损），却被原样写成「画质不达标」，
    # 把「机器坏了」报成「素材不值得压」。S18 为此拆过 encode_failed，
    # 但那只覆盖了编码失败，**测量失败仍在伪装成质量结论**。这里补上。
    #
    # 「不可信」是**窗口位置**的性质（寻址落点离关键帧多远），与 QP 无关,
    # 所以每个窗口一生只对照一次, 判坏之后整条阶梯都不再测它。
    distrusted = set()   # 已证实测量不可信的窗口名
    controlled = set()   # 已做过对照的窗口名（无论结论）
    control_q = args.control_q if args.control_q is not None else CONTROL_Q.get(encoder, 1)

    # 外层最多重来 3 轮：只有「在最后一档才发现坏窗口」才值得重来（见 late_distrust）,
    # 而每轮至少要新剔除一个窗口, 窗口总共 3 个 -> 不可能打转。
    for _ in range(4):
        best_ok = False
        best_vmaf, best_saving, best_q, new_mb = "NA", 0.0, q_ladder[0], 0.0
        late_distrust = False

        for q_val in q_ladder:
            logger.info(f"   窗口试压测量: {q_label}={q_val} (encoder={encoder})...")
            vmaf, saving, est_new_mb, nwin, wins = measure_quality_windowed(
                src_path, encoder, q_val, args.preset, args.audio, args.vt_bitrate,
                duration, height, model_hd, model_4k, temp_dir, skip=distrusted)

            # 有窗口低到可疑就先验一验测量本身, 再谈画质。
            #
            # 两道门槛, 为的是既不漏也不慢:
            # - 中间各档只验低到**物理上不可能**的窗口（< suspect_below）。这类一验一个准,
            #   而且验出来就能立刻剔除, 省下整条阶梯——坏窗口不会随 QP 变好, 陪它走完
            #   5 档是纯粹的浪费（真机上每个坏文件白烧约 50 分钟）。
            # - 最后一档（最保守的 QP）验**所有达不到闸门**的窗口。因为「画质不达标」这个
            #   结论就是在这一档定下来的, 红线要求: 任何画质结论所依据的测量必须先被验过。
            is_last_rung = (q_val == q_ladder[-1])
            control_threshold = args.vmaf_min if is_last_rung else args.suspect_below
            if not args.no_control:
                for w in [x for x in wins if x["score"] < control_threshold
                          and x["name"] not in controlled]:
                    controlled.add(w["name"])
                    ctrl = measure_control_window(
                        src_path, w["ss"], w["wd"], encoder, args.preset, args.audio,
                        args.vt_bitrate, height, model_hd, model_4k, temp_dir, control_q)
                    if ctrl is None or ctrl < args.control_floor:
                        distrusted.add(w["name"])
                        late_distrust = late_distrust or is_last_rung
                        logger.warning(
                            f"      🔴 窗口 {w['name']}@{w['ss']:.0f}s 近无损对照 "
                            f"({q_label}={control_q}) 只有 {ctrl}（应 ≥{args.control_floor}）"
                            f" -> 测量路径不可信, 它那 {w['score']} 分不作为画质结论")
                    else:
                        logger.info(
                            f"      窗口 {w['name']}@{w['ss']:.0f}s 近无损对照 {ctrl} "
                            f"-> 测量可信, {w['score']} 分是素材的真实结论")
                if distrusted:
                    kept = [x for x in wins if x["name"] not in distrusted]
                    if not kept:
                        logger.error("   [测量不可信] 所有窗口的近无损对照都不过关, "
                                     "拒绝对本片下任何画质结论。")
                        return "measurement_unreliable", orig_mb, 0.0, 0.0, "NA", q_val
                    vmaf, saving, est_new_mb, nwin = aggregate_windows(kept, orig_bytes, duration)

            logger.info(f"   {q_label}={q_val}: VMAF(min/{nwin}段)={vmaf} | 预计省{saving:.1f}% | 预计{est_new_mb:.1f}MB")
            best_vmaf, best_saving, best_q, new_mb = vmaf, saving, q_val, est_new_mb
            if vmaf != "NA" and vmaf >= args.vmaf_min and saving >= args.min_saving_pct:
                best_ok = True
                break

        if best_ok or not late_distrust:
            break
        # 坏窗口是在最后一档才暴露的, 此前整条阶梯都被它带偏了。用剩下的可信窗口重走一遍——
        # 否则这片子即便被救回来, 也只能停在最保守的那一档, 白白少省一半空间。
        logger.info(f"   剔除不可信窗口 {sorted(distrusted)} 后重走阶梯（此前各档的结论都被它压低过）")

    if not best_ok:
        return "failed", orig_mb, 0.0, best_saving, best_vmaf, best_q

    # Preview: 到此为止, 不生成文件(new_mb/saving 均为窗口外推的预估)
    if args.preview:
        return "success", orig_mb, new_mb, best_saving, best_vmaf, best_q

    # 正式: 全片编码输出
    logger.info(f"   通过闸门, 全片编码输出 ({q_label}={best_q})...")
    with tempfile.NamedTemporaryFile(dir=temp_dir, suffix=".mp4", delete=False) as tf:
        full_tmp = Path(tf.name)
    ok = encode_video(src_path, full_tmp, encoder, best_q, args.preset, args.audio,
                      vt_bitrate=args.vt_bitrate)
    if not ok and args.audio == 'copy':
        # MP4 装不下 MP2/PCM 这类老音轨（DVD 抓轨、老相机 AVI 全是），copy 会被muxer 拒。
        # 退成 aac 重来一次——**绝不静默丢音轨**，家庭影像没声音等于废了。
        logger.warning("   音轨 copy 失败（源音频格式 MP4 装不下？），改用 aac 重试一次…")
        if full_tmp.exists():
            full_tmp.unlink()
        with tempfile.NamedTemporaryFile(dir=temp_dir, suffix=".mp4", delete=False) as tf:
            full_tmp = Path(tf.name)
        ok = encode_video(src_path, full_tmp, encoder, best_q, args.preset, 'aac',
                          vt_bitrate=args.vt_bitrate)
    if not ok:
        if full_tmp.exists():
            full_tmp.unlink()
        # 独立状态：闸门是过了的，倒在全片编码上。混进 "failed" 会被上层套用
        # 「画质不达标 / 省空间太少」的文案，报出「省 53.7% < 15%」这种自相矛盾的话，
        # 把「机器坏了」说成「素材不值得压」——最不该混淆的两件事。
        return "encode_failed", orig_mb, 0.0, best_saving, best_vmaf, best_q
    actual_new_bytes = full_tmp.stat().st_size
    new_mb = actual_new_bytes / (1024 * 1024)
    best_saving = ((orig_bytes - actual_new_bytes) / orig_bytes) * 100 if orig_bytes > 0 else 0.0
    preserve_metadata(src_path, full_tmp)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    shutil.move(str(full_tmp), str(out_path))
    subprocess.run(['touch', '-r', str(src_path), str(out_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 实测产物的 pix_fmt, 不是推断 —— 编码器实际吐了什么, 报表就记什么。
    # 换了编码器/驱动/ffmpeg 版本也不会让这条记录变成谎话。
    out_pix = probe_pix_fmt(out_path)
    info['out_pix'] = out_pix
    src_pix = info.get('src_pix', '?')
    if pix_bit_depth(out_pix) < pix_bit_depth(src_pix):
        # 走到这里说明前面的守卫没拦住(比如显式开了 --allow-bitdepth-downgrade,
        # 或某个编码器没登记在 FORCED_OUTPUT_PIX 里)。至少要喊一声, 不许静默。
        logger.warning(f"   ⚠ 位深降级: {src_pix} -> {out_pix}"
                       f"（VMAF 测不出 banding, 此项未被闸门评估）")
    return "success", orig_mb, new_mb, best_saving, best_vmaf, best_q

def main():
    parser = argparse.ArgumentParser(description="H.265 Video Compressor with VMAF Quality Gates")
    parser.add_argument('-i', '--input-dir', required=True, type=str, help='Source directory')
    parser.add_argument('-o', '--output-dir', required=True, type=str, help='Output directory')
    parser.add_argument('-e', '--encoder', choices=['x265', 'vt', 'vaapi'], default='x265',
                        help='x265=软件, vt=mac 硬件, vaapi=Intel 核显硬件（NAS 用这个）')
    parser.add_argument('--crf-ladder', type=str, default='22 20', help='CRF ladder values (e.g. "22 20 18")')
    parser.add_argument('--vaapi-qp-ladder', type=str, default='28 26 24 22',
                        help='vaapi QP 阶梯，先激进后保守；第一个过闸门的即采用 (default: "28 26 24 22")')
    parser.add_argument('--file-list', type=str, default=None,
                        help='只处理清单里的文件（一行一个绝对路径，台账派工单格式）；给了它就不走目录遍历')
    parser.add_argument('--vt-quality', type=str, default='65 60', help='VideoToolbox quality values (e.g. "65 60 55")')
    parser.add_argument('--preset', type=str, default='medium', help='x265 preset (slow/medium/fast)')
    parser.add_argument('--vmaf-min', type=float, default=93.0, help='VMAF quality threshold (93=视觉几乎无差别, 4K高速素材更易通过)')
    parser.add_argument('--min-saving-pct', type=float, default=15.0, help='Min saving %% threshold')
    parser.add_argument('--audio', type=str, default='copy', help='Audio codec (copy/aac)')
    parser.add_argument('--vmaf-dur', type=int, default=30, help='VMAF check duration (0 for full video)')
    parser.add_argument('--preview', type=int, nargs='?', const=3, help='Preview mode: randomly process N files and exit without saving')
    parser.add_argument('--preview-sample-sec', type=int, default=30, help='Preview 模式下只压中间这么多秒做快速估算 (default: 30)')
    parser.add_argument('--allow-vt-hevc', action='store_true', help='允许对 HEVC 源使用硬件 vt 编码(默认自动改用软件 x265)')
    parser.add_argument('--recompress-hevc', action='store_true', help='Force re-compression of already HEVC/H.265 videos (useful for high-bitrate files)')
    parser.add_argument('--vt-bitrate', type=float, help='Target bitrate for hevc_videotoolbox in Mbps (disables -q:v quality control)')
    parser.add_argument('--max-hevc-bitrate', type=float, default=20.0, help='Max acceptable HEVC bitrate in Mbps. HEVC videos below this are skipped. (default: 20.0)')
    parser.add_argument('--min-bitrate', type=float, default=2.5, help='Min acceptable bitrate in Mbps for any video. Videos below this are skipped. (default: 2.5)')
    parser.add_argument('--min-file-mb', type=float, default=100.0, help='Min acceptable file size in MB for re-compression. Files smaller than this are skipped. (default: 100.0)')
    parser.add_argument('--no-vmaf', action='store_true', help='Skip VMAF quality evaluation and encode directly for maximum speed')
    # 近无损对照（P2-41）：把「测量坏了」与「素材压不动」分开。默认开启——
    # 只有分数低到可疑的窗口才付这份成本，通过闸门的文件一秒都不多花。
    parser.add_argument('--suspect-below', type=float, default=80.0,
                        help='窗口分低于它就先做近无损对照再下结论 (default: 80)')
    parser.add_argument('--control-floor', type=float, default=95.0,
                        help='近无损对照低于它即判定该窗口测量不可信 (default: 95)')
    parser.add_argument('--control-q', type=int, default=None,
                        help='近无损对照用的量化器，默认按编码器取 (vaapi QP1 / x265 CRF0 / vt q100)')
    parser.add_argument('--no-control', action='store_true',
                        help='关掉近无损对照（会让测量失败重新伪装成画质结论，别用）')
    # 位深 / 色度守卫：VMAF 测不出这两类损失，所以不能靠质量闸门顺手放行
    parser.add_argument('--allow-bitdepth-downgrade', action='store_true',
                        help='允许 10bit 源被降到 8bit（默认拒绝：banding 不可逆且 VMAF 测不出）')
    parser.add_argument('--no-chroma-downgrade', action='store_true',
                        help='拒绝 4:2:2/4:4:4 源降到 4:2:0（默认放行，但一律记入报表）')

    args = parser.parse_args()
    
    check_dependencies()
    
    src_dir = Path(args.input_dir).resolve()
    out_dir = Path(args.output_dir).resolve()
    
    if not src_dir.exists():
        logger.error(f"Source folder does not exist: {src_dir}")
        sys.exit(1)
        
    model_hd, model_4k = find_vmaf_models()
    logger.info(f"Using VMAF HD Model: {model_hd}")
    logger.info(f"Using VMAF 4K Model: {model_4k}")
    
    # Gather video files —— 有清单就按清单派工，没有才走目录遍历
    video_files = []
    if args.file_list:
        lst = Path(args.file_list)
        if not lst.exists():
            logger.error(f"文件清单不存在：{lst}")
            sys.exit(1)
        missing, outside, unsupported = [], [], []
        for line in lst.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            p = Path(line)
            if not p.is_file():
                missing.append(line)
                continue
            try:
                p.resolve().relative_to(src_dir)
            except ValueError:
                outside.append(line)
                continue
            if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
                unsupported.append(line)
                continue
            video_files.append(p)
        # 派工单不许静默缩水——少一个都要说出来（与 vcat report --worklist 同一态度）
        for label, items in (("盘上找不到", missing), ("不在输入目录下", outside),
                             ("扩展名不支持", unsupported)):
            if items:
                logger.warning(f"清单中 {len(items)} 个文件{label}，已跳过：")
                for it in items[:10]:
                    logger.warning(f"    {it}")
                if len(items) > 10:
                    logger.warning(f"    …另有 {len(items) - 10} 个")
        logger.info(f"按清单派工：{len(video_files)} 个文件待处理")
    else:
        for root, dirs, files in os.walk(src_dir):
            # Skip output directory if it happens to be nested inside input
            if Path(root).resolve() == out_dir:
                continue
            for file in files:
                file_path = Path(root) / file
                if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    video_files.append(file_path)


    total_files = len(video_files)
    logger.info(f"Found {total_files} videos to process.")
    
    if args.preview is not None:
        preview_count = args.preview
        logger.info(f"[PREVIEW MODE] Randomly selecting {preview_count} files for trial compression (no output files will be saved)...")
        # Random sample
        video_files = random.sample(video_files, min(preview_count, total_files))
        total_files = len(video_files)
        
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "_report.csv"
    
    # Initialize report
    if not report_path.exists():
        with open(report_path, 'w', encoding='utf-8') as f:
            # 第 8 列是后加的（P2-41）。存量报表仍是 7 列表头 + 混着 7/8 列的行，
            # csv 读得动、backfill 的 row[:7] 也不受影响，不去改写历史行。
            f.write("文件,原MB,新MB,省%,VMAF,CRF/Q,决定,像素格式\n")
            
    n_total = 0
    n_keep = 0
    n_skip = 0
    n_fail = 0
    total_saved_bytes = 0
    
    for idx, src_path in enumerate(video_files, 1):
        rel_path = src_path.relative_to(src_dir)
        # Avoid commas in CSV filename representation
        rel_path_csv = str(rel_path).replace(',', ' ')
        
        out_path = out_dir / rel_path.with_suffix('.mp4')
        
        # Skip if already exists (breakpoint resume)
        if out_path.exists() and not args.preview:
            logger.info(f"[{idx}/{total_files}] Skipping (already exists): {rel_path}")
            n_skip += 1
            continue
            
        logger.info(f"\n[{idx}/{total_files}] Processing: {rel_path}")
        
        info = {}
        status, orig_mb, new_mb, saving, vmaf, q_val = process_file(
            src_path, out_path, args, model_hd, model_4k, info=info)
        
        n_total += 1
        
        if status == "skipped_small_file":
            logger.info("   Skipped: File size below threshold.")
            n_skip += 1
            decision = "小文件(跳过)"
        elif status == "skipped_already_h265":
            logger.info("   Skipped: Already H.265/HEVC with reasonable bitrate.")
            n_skip += 1
            decision = "已是H.265(跳过)"
        elif status == "skipped_low_bitrate":
            logger.info("   Skipped: Bitrate too low.")
            n_skip += 1
            decision = "码率过低(跳过)"
        elif status == "skipped_unsupported":
            logger.info("   Skipped: Unsupported media codec/track.")
            n_skip += 1
            decision = "不支持格式(跳过)"
        elif status == "skipped_bitdepth_guard":
            n_skip += 1
            decision = "位深守卫(拒绝 10bit→8bit)"
            logger.info("   Skipped: 位深守卫拦下，VMAF 测不出 banding，不做无据的降级。")
        elif status == "skipped_chroma_guard":
            n_skip += 1
            decision = "色度守卫(拒绝降 4:2:0)"
            logger.info("   Skipped: 色度守卫拦下（--no-chroma-downgrade）。")
        elif status == "measurement_unreliable":
            # 测量路径坏了 —— 这是**我们**的问题，不是这段素材的问题。
            # 绝不写成「画质不达标」：那等于替一段没测准的家庭影像盖章说它不值得压。
            n_fail += 1
            decision = "需人工检查(测量不可信: 近无损对照未过关)"
            logger.error(f"   [测量不可信] 拒绝下画质结论，原片保留：{rel_path}")
        elif status == "encode_failed":
            # 闸门过了、倒在全片编码——这是机器/参数的问题，不是素材不值得压。
            # 说清楚才能去查日志，别让它混进「画质不达标」里被当成正常结论。
            n_fail += 1
            decision = f"⚠全片编码失败(闸门本已通过 VMAF {vmaf}/省{saving:.1f}%)，见日志"
            logger.error(f"   [ENCODE FAILED] 闸门通过但全片编码失败，原片保留：{rel_path}")
        elif status == "success":
            n_keep += 1
            saved_mb = orig_mb - new_mb
            total_saved_bytes += saved_mb * 1024 * 1024
            decision = "保留压缩版"
            logger.info(f"   [SUCCESS] Kept compressed file. Saved {saved_mb:.2f} MB ({saving:.1f}%)")
        else:
            n_fail += 1
            if vmaf == "NA":
                decision = "需人工检查(VMAF算不出)"
                logger.info("   [FAILED] VMAF calculation failed (possibly VFR). Retained original.")
            elif vmaf < args.vmaf_min:
                decision = f"画质不达标(VMAF {vmaf} < {args.vmaf_min})"
                logger.info(f"   [FAILED] VMAF score {vmaf} below threshold {args.vmaf_min}. Retained original.")
            else:
                decision = f"省空间太少({saving:.1f}% < {args.min_saving_pct}%)"
                logger.info(f"   [FAILED] Space saving {saving:.1f}% below minimum {args.min_saving_pct}%. Retained original.")
                
        # Append to CSV
        # 第 8 列「像素格式」记录 pix_fmt 变迁（源 -> 产物），是**实测**不是推断。
        # 🔴 为什么另起一列、而不是把它拼进「决定」：
        #    contrib/backfill_compress_report.py 用 `decision != "保留压缩版"` **精确匹配**
        #    来挑要登记血缘的行，往决定里加任何后缀都会让它一条都认不出来。
        #    它读的是 row[:7]，多一列不影响 —— 加列安全，改列致命。
        src_pix, out_pix = info.get('src_pix', ''), info.get('out_pix', '')
        pix_col = f"{src_pix}->{out_pix}" if src_pix and out_pix else (src_pix or '')
        with open(report_path, 'a', encoding='utf-8') as f:
            f.write(f'"{rel_path_csv}",{orig_mb:.2f},{new_mb:.2f},{saving:.1f},{vmaf},{q_val},{decision},{pix_col}\n')
            
    logger.info(f"\n{'='*60}\nCOMPRESSION RUN SUMMARY\n{'='*60}")
    logger.info(f"Total processed: {n_total}")
    logger.info(f"Successful compressions: {n_keep}")
    logger.info(f"Skipped/Ignored: {n_skip}")
    logger.info(f"Retained originals: {n_fail}")
    logger.info(f"Space saved: {total_saved_bytes / (1024*1024*1024):.2f} GB")
    logger.info(f"CSV Report: {report_path}")

if __name__ == '__main__':
    main()
