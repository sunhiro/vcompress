"""全量编码命令的拼装（`run_ab_av1_encode`）。

为什么要单独锁这一段：**这是本仓唯一一处「拼 ffmpeg 命令」的地方**，
而它出的错全都长得像别的东西——`Impossible to convert between the formats`
看着像素材问题，`Option preset not found` 看着像 ffmpeg 版本问题。
真机上一跑就是几十分钟，发现问题时已经太晚。

红线 §9 那一条（vaapi 只做硬件**编码**，绝不 `-hwaccel vaapi`）也在这里落地：
错误的一端会表现为「15 秒试压全过、全场编码到中途必崩 `Failed to sync surface`」，
**是最难查的那种**，所以这里直接对着拼出来的 argv 断言，而不是等真机。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import vcompress_abav1 as engine


@pytest.fixture
def captured(monkeypatch):
    """把 subprocess.run 换掉，只留拼出来的 argv。"""
    box = {}

    class _Done:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(cmd, *a, **k):
        box["cmd"] = list(cmd)
        return _Done()

    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    return box


def _encode(captured, encoder, crf=24, preset=None):
    engine.run_ab_av1_encode("in.MOV", "out.MOV", encoder, crf, preset)
    return captured["cmd"]


def test_vaapi_arms_the_hardware(captured):
    """🔴 `-e vaapi` 的全量转码必须自己接硬件。

    `ab-av1 crf-search` 会替采样段注入 hwaccel，但**全量这一段是我们自己拼的
    ffmpeg 命令**，ab-av1 的默认值在这里不生效。老代码只换了编码器名就发车，
    于是 README 里 NAS 那一条路根本走不通。
    """
    cmd = _encode(captured, "vaapi")
    joined = " ".join(cmd)
    assert "-vaapi_device" in cmd
    assert "format=nv12,hwupload" in joined   # 帧要上传到显存
    assert "-qp" in cmd                        # VAAPI 用 -qp，没有 -crf/-q:v
    assert cmd[cmd.index("-c:v") + 1] == "hevc_vaapi"


def test_vaapi_never_uses_hardware_decoding(captured):
    """红线 §9：只做硬件编码，**绝不** `-hwaccel vaapi`。

    带上硬解后，15 秒的试压窗口都过得去，但 26 分钟的 DVD 抓轨全片编码到中途
    必崩 `Failed to sync surface: internal decoding error` —— 「测量通过 → 全片失败」。
    """
    cmd = _encode(captured, "vaapi")
    assert "-hwaccel" not in cmd
    assert not any("hwaccel" in str(a) for a in cmd)


@pytest.mark.parametrize("encoder,flag", [
    ("vt", "hevc_videotoolbox"),
    ("x265", "libx265"),
    ("svt-av1", "libsvtav1"),
    ("x264", "libx264"),
])
def test_encoder_mapping_is_stable(captured, encoder, flag):
    cmd = _encode(captured, encoder)
    assert cmd[cmd.index("-c:v") + 1] == flag


@pytest.mark.parametrize("encoder", ["vaapi", "vt"])
def test_hardware_encoders_drop_preset_instead_of_poisoning_the_command(captured, encoder):
    """🔴 `-preset` 只在软编编码器上存在。

    `hevc_vaapi` / `hevc_videotoolbox` 没有这个 AVOption，硬塞进去 ffmpeg 会
    `Option preset not found` 直接失败。旧代码无条件追加，只是 vaapi 那条臂当时
    根本跑不起来所以没人撞上 —— **硬件接线修好之后它才变得够得着**。
    丢掉用户的显式参数必须留痕，不能静默忽略。
    """
    cmd = _encode(captured, encoder, preset="medium")
    assert "-preset" not in cmd
    assert cmd[-1] == "out.MOV"          # 产物路径仍收在最后


@pytest.mark.parametrize("encoder", ["x265", "svt-av1", "x264"])
def test_software_encoders_still_take_preset(captured, encoder):
    cmd = _encode(captured, encoder, preset="slow")
    assert cmd[cmd.index("-preset") + 1] == "slow"


def test_preset_dropped_is_logged(captured, caplog):
    """静默丢参数比报错更坏：用户以为 preset 生效了，其实没有。"""
    with caplog.at_level(logging.WARNING, logger="vcompress_abav1"):
        _encode(captured, "vaapi", preset="medium")
    assert any("preset" in r.message for r in caplog.records)


def test_no_preset_means_no_flag(captured):
    """没传 preset 时不许凭空多一个 `-preset`（默认值是 None）。"""
    assert "-preset" not in _encode(captured, "x265")


def test_audio_and_metadata_flags_survive(captured):
    """红线 §10：绝不静默丢音轨。音轨是 `-c:a copy` 搬过去的，不许被改动弄没。"""
    cmd = _encode(captured, "vaapi")
    assert cmd[cmd.index("-c:a") + 1] == "copy"
    assert "-map" in cmd and "0:a?" in cmd


def test_audio_can_be_switched_to_aac(captured):
    """退路用的那个开关真的接上了（默认 copy 不放行丢音轨）。"""
    engine.run_ab_av1_encode("in.MOV", "out.MOV", "vt", 24, audio="aac")
    cmd = captured["cmd"]
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert "0:a?" in cmd


# --------------------------------------------------------- 重试接线（红线 §10 后半段）
#
# 命令拼对 ≠ 接线接对。下面这几个测试真走一遍 `process_file_abav1`，只把会碰真机的
# 那几个函数（ffprobe / ab-av1 / ffmpeg / exiftool）换掉。
#
# 背景：DVD 抓轨、老相机 AVI 的音轨常是 MP2/PCM，MP4/MOV 装不下，`-c:a copy`
# 会被 muxer 拒。备份引擎 vcompress.py 早有「退成 aac 重来一次」的退路，
# 主引擎一直缺——**闸门好不容易过了，整片编码倒在这一步**。


def _args(**over):
    import types

    base = dict(
        min_file_mb=0.0, min_bitrate=0.0, max_hevc_bitrate=30.0,
        encoder="vt", vmaf_min=93.0, min_saving_pct=15.0,
        sample_sec=10, samples=3, vmaf_threads=6, vmaf_scale_1080p=True,
        preset=None, preview=False, skip_hdr=False,
        allow_bitdepth_downgrade=False, no_chroma_downgrade=False,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


@pytest.fixture
def driven(monkeypatch, tmp_path):
    """真素材以外的一切都换成桩，返回 (源片, 产物, audio 调用序列)。"""
    src = tmp_path / "IMG_0001.MOV"
    src.write_bytes(b"x" * 4096)
    out = tmp_path / "IMG_0001.hevc.MOV"
    calls: list[str] = []

    monkeypatch.setattr(engine, "get_video_metadata",
                        lambda p: ("h264", 720, 60.0, 5_000_000, "yuv420p"))
    monkeypatch.setattr(engine, "run_ab_av1_search", lambda *a, **k: {
        "crf": 26, "vmaf": 94.1,
        "predicted_encode_percent": 60.0, "predicted_encode_size": 1_000_000,
    })
    monkeypatch.setattr(engine, "preserve_metadata", lambda *a, **k: None)
    return src, out, calls


def _flaky(calls, fail_on=("copy",), size=2048, err="Could not find tag for codec mp2 in stream #1"):
    """按 audio 模式决定成败的假编码器：`fail_on` 里的模式返回失败且**不**落盘。

    返回值与真函数一致：`(成功?, 失败原因)`——重试条件（§10 后半段）靠它区分
    「muxer 拒收音轨」和「盘满 / 断连」这两类失败。
    """
    def _run(src_path, out_path, encoder_name, crf_val, preset=None, audio="copy"):
        calls.append(audio)
        if audio in fail_on:
            return False, err
        Path(out_path).write_bytes(b"y" * size)
        return True, ""
    return _run


def test_audio_copy_failure_falls_back_to_aac(driven, monkeypatch):
    """🔴 主引擎必须补上备份引擎那条 aac 退路（2026-10-14）。"""
    src, out, calls = driven
    monkeypatch.setattr(engine, "run_ab_av1_encode", _flaky(calls))

    status, *_rest, decision, _pix = engine.process_file_abav1(src, out, _args())

    assert calls == ["copy", "aac"], f"audio 调用序列不对: {calls}"
    assert status == "success", f"退路没救回来：{status} / {decision}"


def test_no_retry_when_the_first_attempt_works(driven, monkeypatch):
    """退路只在**失败后**才走：不能拿它给每次成功都加一遍空转。"""
    src, out, calls = driven
    monkeypatch.setattr(engine, "run_ab_av1_encode", _flaky(calls, fail_on=()))

    status, *_rest = engine.process_file_abav1(src, out, _args())

    assert calls == ["copy"]
    assert status == "success"


def test_no_retry_when_failure_is_not_an_audio_mux_error(driven, monkeypatch):
    """🔴 复检收紧（2026-10-14）：盘满 / SMB 断连 / VAAPI 故障不重试。

    重试救不回这类失败，只会白跑一遍整片编码；而且老日志把原因硬说成
    「音轨装不下」——那是在报假账。失败原因要如实落日志。
    """
    src, out, calls = driven
    monkeypatch.setattr(engine, "run_ab_av1_encode",
                        _flaky(calls, err="No space left on device"))

    status, *_rest = engine.process_file_abav1(src, out, _args())

    assert calls == ["copy"], "非音轨失败走了 aac 重试，白跑一场"
    assert status == "failed"
    assert not out.exists(), "残片必须清掉"


def test_fallback_gives_up_after_one_try_and_cleans_the_remnant(driven, monkeypatch):
    """aac 也失败时要**收手并清残片**，而不是无限重试或把半成品留在盘上。

    残片与真产物同名，下一轮的续压守卫（「存在且非空 ⇒ 跳过」）会把它当成压好的
    产物，外部扫描也认不出来——这正是老版记录过的那条坑。
    """
    src, out, calls = driven

    def _always_fail(*a, **k):
        calls.append(k.get("audio", "copy"))
        out.write_bytes(b"z" * 128)      # 模拟 ffmpeg 写了一半就崩
        return False, "Could not find tag for codec mp2 in stream #1, codec not currently supported in container"

    monkeypatch.setattr(engine, "run_ab_av1_encode", _always_fail)

    status, *_rest, decision, _pix = engine.process_file_abav1(src, out, _args())

    assert calls == ["copy", "aac"], "退路只许走一次"
    assert status == "failed"
    assert decision == "编码失败"
    assert not out.exists(), "没过闸门的残片留在了盘上，下轮会被当成产物"
