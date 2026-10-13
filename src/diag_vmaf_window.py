#!/usr/bin/env python3
"""诊断「窗口式 VMAF 测量」是不是在说谎（P2-41）。

背景：批量压缩里有一类文件在**最保守的 QP（20）**下拿到 VMAF 5.93~49.51。
这个分数物理上不可能是画质结论——QP20 近乎无损，画面不可能崩成这样。
真相要么是「测量路径坏了」，要么是「素材确实压不动」，而现在的脚本
把两者都写成「画质不达标」。这个工具的唯一任务是**把两者分开**。

判据是**近无损对照**：同一个窗口再压一次 QP=1（近乎无损），再测一次 VMAF。
- 对照分 ≈99 → 测量路径是好的，低分是素材的真实结论
- 对照分仍然低 → **测量路径坏了**，此前那个低分不是画质结论

🔴 本工具**必须**复用 vcompress.py 里的 encode_video / compute_vmaf_sample，
不许另写一份「差不多的」seek 逻辑——那样测的就不是主脚本的行为了，结论无效。

用法（在 vcat-media 容器里跑）：
    python3 -u /opt/diag_vmaf_window.py /volume3/video/.../IMG_2151.MOV \\
        --window middle --qp 20 --control-qp 1 --shift 1 2 3
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vcompress as C  # noqa: E402


def window_positions(duration):
    """与 measure_quality_windowed 完全同一套窗口计算，不许另算。"""
    seg = 15.0
    begin_at = C.SEEK_PREROLL + 5.0
    if duration >= (begin_at + seg * 3 + 5):
        return [("Beginning", begin_at, seg),
                ("Middle", duration / 2.0 - seg / 2.0, seg),
                ("End", duration - seg - 5.0, seg)]
    if duration >= 8:
        mid = duration / 2.0 - seg / 2.0
        ss = max(min(begin_at, max(0.0, duration - seg)), mid)
        return [("Middle", ss, min(seg, duration))]
    return [("Whole", 0.0, duration)]


def probe_stream(path):
    """取帧率相关字段：r_frame_rate 与 avg_frame_rate 差得多就是 VFR。"""
    cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
           'stream=r_frame_rate,avg_frame_rate,nb_frames,codec_name,width,height',
           '-of', 'json', str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(out.stdout)['streams'][0]
    except Exception:
        return {}


def count_frames(path):
    cmd = ['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0',
           '-show_entries', 'stream=nb_read_frames', '-of', 'csv=p=0', str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return int(out.stdout.strip())
    except Exception:
        return -1


def count_reference_frames(reference, clip_ss, clip_dur):
    """数参考侧那条路（混合 seek + trim）实际吐出多少帧。

    走的是与 compute_vmaf_sample 一模一样的 seek/trim，只是把 libvmaf 换成丢弃。
    """
    pre = min(clip_ss, C.SEEK_PREROLL)
    end = pre + clip_dur
    cmd = ['ffmpeg', '-hide_banner', '-nostdin',
           '-ss', f"{clip_ss - pre:.2f}", '-i', str(reference),
           '-vf', f"trim=start={pre:.2f}:end={end:.2f},setpts=PTS-STARTPTS",
           '-f', 'null', '-']
    out = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    m = re.findall(r'frame=\s*(\d+)', out.stderr)
    return int(m[-1]) if m else -1


def vmaf_detail(distorted_clip, reference, height, clip_ss, clip_dur,
                model_hd, model_4k, shift=0, norm_fps=None):
    """与 compute_vmaf_sample 同一条滤镜链；可选两种对照变体。

    shift>0：把失真侧前移 shift 帧——验证「是不是差几帧的固定偏移」。
    norm_fps：**两侧施加同一个 fps 归一化**——验证「是不是帧率漂移累积」。

    🔴 norm_fps 必须对称施加。对称归一化是「让两侧按同一时间轴配对」，
    与 2026-07-29 bug #1 那次「把参照物换成中间产物」是两回事——那次是
    单边替换且色彩范围变了，这次两边走完全相同的一条滤镜。
    返回 (mean, frames)。
    """
    version = "vmaf_4k_v0.6.1" if height >= 2000 else "vmaf_v0.6.1"
    nthreads = os.cpu_count() or 4
    pre = min(clip_ss, C.SEEK_PREROLL)
    end = pre + clip_dur

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        vlog_path = tf.name
    # fps 重采样 + 像素格式统一，两侧一字不差地施加同一条链。
    # format=yuv420p 这一节来自 vcompress 的实现，vcat 原版没有——
    # 它挡的是「两侧像素格式不同导致的隐性偏差」，白拿的健壮性。
    norm = f",fps={norm_fps},format=yuv420p" if norm_fps else ""
    if shift:
        dist = (f"[0:v]select='gte(n\\,{shift})',setpts=PTS-STARTPTS{norm}[d];")
        dlabel = "[d]"
    elif norm:
        dist = f"[0:v]setpts=PTS-STARTPTS{norm}[d];"
        dlabel = "[d]"
    else:
        dist = ""
        dlabel = "[0:v]"
    filtergraph = (
        f"{dist}"
        f"[1:v]trim=start={pre:.2f}:end={end:.2f},setpts=PTS-STARTPTS{norm}[r];"
        f"{dlabel}[r]libvmaf=model=version={version}:n_threads={nthreads}"
        f":log_path={vlog_path}:log_fmt=json"
    )
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
           '-i', str(distorted_clip),
           '-ss', f"{clip_ss - pre:.2f}", '-i', str(reference),
           '-filter_complex', filtergraph, '-f', 'null', '-']
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True)
        if p.returncode != 0:
            print(f"      VMAF 失败: {p.stderr.decode('utf-8', 'ignore')[:300]}")
            return None, []
        with open(vlog_path, encoding='utf-8') as f:
            data = json.load(f)
        mean = data['pooled_metrics']['vmaf']['mean']
        frames = [fr['metrics']['vmaf'] for fr in data.get('frames', [])]
        return round(mean, 2), frames
    except Exception as e:
        print(f"      VMAF 异常: {e}")
        return None, []
    finally:
        if os.path.exists(vlog_path):
            os.unlink(vlog_path)


def describe(frames):
    if not frames:
        return "无逐帧数据"
    n = len(frames)
    head = sum(frames[:10]) / min(10, n)
    tail = sum(frames[-10:]) / min(10, n)
    low = sum(1 for v in frames if v < 50)
    return (f"{n} 帧 · min {min(frames):.1f} / max {max(frames):.1f} · "
            f"前10帧均 {head:.1f} · 后10帧均 {tail:.1f} · <50 分的 {low} 帧({low*100//n}%)")


def run_one(src, args, model_hd, model_4k, temp_dir):
    codec, height, duration, bit_rate = C.get_video_metadata(src)
    st = probe_stream(src)
    r_fps, avg_fps = st.get('r_frame_rate', '?'), st.get('avg_frame_rate', '?')

    def _f(x):
        try:
            a, b = x.split('/')
            return float(a) / float(b) if float(b) else 0.0
        except Exception:
            return 0.0
    # 🔴 阈值别放宽。真机 IMG_2151 是 r=24.000 / avg=24.135，只差 0.135 fps,
    # 用 0.5 的阈值会把它判成 CFR —— 而正是这 0.56% 的漂移在 15 秒窗口里
    # 攒出 10 帧错位，把 VMAF 从 99 拉到 49.51。「几乎是 CFR」不等于 CFR。
    vfr = abs(_f(r_fps) - _f(avg_fps)) > 0.02

    print(f"\n{'='*78}\n{src}")
    print(f"  codec={codec} {st.get('width')}x{height} 时长{duration:.1f}s "
          f"码率{bit_rate/1e6:.1f}Mbps")
    print(f"  r_frame_rate={r_fps} avg_frame_rate={avg_fps} "
          f"→ {'VFR（变帧率）' if vfr else 'CFR（定帧率）'}")

    wins = window_positions(duration)
    targets = [w for w in wins if args.window == 'all' or w[0].lower() == args.window]
    if not targets:
        print(f"  ⚠ 这个文件没有 {args.window} 窗口（只有 {[w[0] for w in wins]}）")
        targets = wins

    for name, ss, wlen in targets:
        wd = min(wlen, max(0.0, duration - ss))
        print(f"\n  ── 窗口 {name}@{ss:.0f}s 时长{wd:.1f}s ──")
        ref_frames = count_reference_frames(src, ss, wd)

        configs = [("复现(批量QP)", args.qp)]
        if not args.skip_control:
            configs.append(("近无损对照", args.control_qp))
        for label, q in configs:
            with tempfile.NamedTemporaryFile(dir=temp_dir, suffix=".mp4", delete=False) as tf:
                wtmp = Path(tf.name)
            try:
                ok = C.encode_video(src, wtmp, args.encoder, q, args.preset, args.audio,
                                    vt_bitrate=None, clip=(ss, wd), video_only=True)
                if not ok or not wtmp.exists() or wtmp.stat().st_size == 0:
                    print(f"    {label} Q={q}: 编码失败")
                    continue
                dist_frames = count_frames(wtmp)
                mb = wtmp.stat().st_size / (1024 * 1024)
                mean, frames = vmaf_detail(wtmp, src, height, ss, wd,
                                           model_hd, model_4k, shift=0)
                flag = ""
                if label == "近无损对照" and mean is not None:
                    flag = ("  ← 🔴 测量路径坏了（近无损也测不出高分）"
                            if mean < args.control_floor
                            else "  ← ✅ 测量可信（低分是素材的真实结论）")
                print(f"    {label} Q={q}: VMAF={mean}{flag}")
                print(f"      产物 {mb:.1f}MB · 失真侧 {dist_frames} 帧 / 参考侧 {ref_frames} 帧"
                      f"{'  ← ⚠ 帧数不等' if dist_frames != ref_frames else ''}")
                print(f"      逐帧: {describe(frames)}")

                # 帧率归一化对照：两侧同一条 fps 滤镜，验「累积漂移」假说。
                # 复用刚压好的窗口产物，不重新编码——只多花一次 VMAF。
                if args.normalize_fps and mean is not None:
                    nf = args.normalize_fps if args.normalize_fps != 'auto' else r_fps
                    m2, f2 = vmaf_detail(wtmp, src, height, ss, wd,
                                         model_hd, model_4k, norm_fps=nf)
                    delta = (m2 - mean) if m2 is not None else 0
                    print(f"    {label} Q={q} + 两侧 fps={nf} 归一化: VMAF={m2} "
                          f"(Δ{delta:+.2f})"
                          f"{'  ← ✅ 漂移假说成立，测量可救' if delta > 20 else ''}")
                    print(f"      逐帧: {describe(f2)}")

                if args.shift and label == "近无损对照" and mean is not None \
                        and mean < args.control_floor:
                    for k in args.shift:
                        m2, f2 = vmaf_detail(wtmp, src, height, ss, wd,
                                             model_hd, model_4k, shift=k)
                        print(f"      失真侧前移 {k} 帧 → VMAF={m2}"
                              f"{'  ← 🔴 对齐问题坐实' if m2 and m2 - mean > 20 else ''}")
            finally:
                if wtmp.exists():
                    wtmp.unlink()


def main():
    ap = argparse.ArgumentParser(description="窗口式 VMAF 测量的诊断工具（P2-41）")
    ap.add_argument('files', nargs='+', help='要诊断的视频（绝对路径）')
    ap.add_argument('--window', default='middle',
                    choices=['beginning', 'middle', 'end', 'whole', 'all'])
    ap.add_argument('--qp', type=int, default=20, help='复现批量结论用的 QP（阶梯最后一档）')
    ap.add_argument('--control-qp', type=int, default=1, help='近无损对照的 QP')
    ap.add_argument('--skip-control', action='store_true',
                    help='跳过近无损对照（只验帧率归一化这类改动时用，省一次慢编码）')
    ap.add_argument('--control-floor', type=float, default=95.0,
                    help='对照分低于它就判定测量路径不可信（阈值需在健康样本上标定）')
    ap.add_argument('--shift', type=int, nargs='*', default=[],
                    help='对照分低时，试着把失真侧前移这些帧数，看分数是否暴涨')
    ap.add_argument('--normalize-fps', default=None,
                    help="两侧施加同一个 fps 归一化再测一遍（'auto' = 用源的 r_frame_rate）")
    ap.add_argument('-e', '--encoder', default='vaapi', choices=['x265', 'vt', 'vaapi'])
    ap.add_argument('--preset', default='medium')
    ap.add_argument('--audio', default='copy')
    ap.add_argument('--temp-dir', default='/volume3/vcat-compressed/_diag')
    args = ap.parse_args()

    C.check_dependencies()
    model_hd, model_4k = C.find_vmaf_models()
    temp_dir = Path(args.temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    print(f"编码器={args.encoder} 复现QP={args.qp} 对照QP={args.control_qp} "
          f"对照下限={args.control_floor}")
    for f in args.files:
        p = Path(f)
        if not p.is_file():
            print(f"⚠ 不存在：{f}")
            continue
        run_one(p, args, model_hd, model_4k, temp_dir)


if __name__ == '__main__':
    main()
