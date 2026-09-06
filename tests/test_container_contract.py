"""产物沿用源容器 —— 红线 10 在生产引擎上「触发不了」的**唯一原因**。

红线 10 的原话：「MP4 装不下 MP2/PCM 这类老音轨（DVD 抓轨、老相机 AVI 全是），
`-c:a copy` 会被 muxer 拒。此时**退成 aac 重来一次**，而不是丢掉音轨。」

`vcompress.py` 实现了那条退避（`:847`）。**生产引擎 `vcompress_abav1.py` 没有**——
它只有 `-c:a copy`，被拒就记「编码失败」。那为什么真机上没出事？

因为 `marked_name` **沿用源扩展名**（ADR-020），产物容器 == 源容器：
`cam.avi → cam.hevc.avi` 走 AVI muxer，`VTS_01_2.VOB → VTS_01_2.hevc.VOB`
走 MPEG-PS muxer。**源容器既然装得下那条音轨，同名容器就还装得下。**
红线 10 说的「MP4 muxer 拒收」这个前提，在新命名约定下根本不成立。

2026-09-06 实测坐实（照抄 `run_ab_av1_encode` 的 vt 分支命令）：

    cam.avi  (mpeg4 + mp2)  → cam.hevc.avi   ✅ 音轨 mp2 原样 copy
    dvd.VOB  (mpeg2 + mp2)  → dvd.hevc.VOB   ✅ 音轨 mp2 原样 copy

§5.6 的 R5 早就撞见过这件事（「`-c:a copy` 直接成功，退 aac 那条路根本没被触发」），
只是当时只看到 VOB 一例，没把它推广成「红线 10 触发不了」这个结论。

🔴 **所以这条命名约定现在是承重的**：谁把它改回 `.with_suffix('.mp4')`
（老 `vcompress.py` 就是那么干的），红线 10 的洞**当场就开**，而生产引擎
没有退避、只会把老 AVI/DVD 那批记成「编码失败」——一个看起来像机器故障的
静默画质事故。本文件就是钉这颗钉子的。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from naming import marked_name
from vcompress_abav1 import SUPPORTED_EXTENSIONS

#: 这些容器**自己装得下**老音轨（MP2 / PCM / AC3），源片就是这么来的。
#: 红线 10 点名的两类素材（DVD 抓轨、老相机 AVI）落在这里。
LEGACY_AUDIO_CONTAINERS = {".avi", ".vob", ".mpg", ".mts", ".m2ts", ".mkv"}


@pytest.mark.parametrize("ext", sorted(SUPPORTED_EXTENSIONS))
def test_every_supported_container_is_preserved(ext):
    """🔴 **每一个**受支持的扩展名都必须原样留在产物上，不是只有 MOV/avi。

    `test_naming.py` 那条只抽查了两个；这里覆盖全集，因为红线 10 的暴露面
    正是「哪些容器会被换掉」。
    """
    src = f"素材{ext}"
    out = marked_name(src)
    assert Path(out).suffix == ext, f"{src} 的产物容器被换成了 {Path(out).suffix}"


@pytest.mark.parametrize("ext", sorted(LEGACY_AUDIO_CONTAINERS))
def test_legacy_audio_containers_never_become_mp4(ext):
    """🔴 红线 10 的暴露面：这几类容器一旦被转成 .mp4，`-c:a copy` 就会被拒，
    而生产引擎没有 aac 退避——老 AVI / DVD 抓轨会整批记成「编码失败」。"""
    out = marked_name(f"VTS_01_2{ext}")
    assert not out.endswith(".mp4")
    assert Path(out).suffix == ext


def test_the_marker_goes_before_the_extension_not_after():
    """标记插在扩展名**之前**。插在后面（`X.MOV.hevc`）等于换了容器，
    ffmpeg 会按 `.hevc` 挑裸流 muxer，音轨直接没地方放。"""
    assert marked_name("X.MOV") == "X.hevc.MOV"
    assert not marked_name("X.MOV").endswith(".hevc")


def test_mutation_forcing_mp4_reopens_the_red_line_10_hole():
    """变异验证：还原老 `vcompress.py` 的 `.with_suffix('.mp4')`。

    变异体自己一声不吭——它产出的名字看着完全正常，**坏在别处**：
    AVI/VOB 源片的音轨会在 muxer 那里被拒，而生产引擎不会退 aac。
    """
    def mutated(rel):
        return str(Path(rel).with_suffix(".mp4"))

    for ext in sorted(LEGACY_AUDIO_CONTAINERS):
        assert mutated(f"VTS_01_2{ext}").endswith(".mp4")      # 变异体：容器被换掉
        assert marked_name(f"VTS_01_2{ext}").endswith(ext)     # 真实现：容器保住


def test_supported_extensions_has_not_silently_grown_a_container_we_never_checked():
    """新增受支持容器时，请回来想一遍红线 10：那个容器装得下它自己的音轨吗？

    这条不是形式主义——`SUPPORTED_EXTENSIONS` 加一行是很轻的动作，
    而红线 10 的暴露面正是由这个集合定义的。
    """
    known = {'.mp4', '.mov', '.m4v', '.avi', '.mkv', '.mts', '.m2ts',
             '.3gp', '.wmv', '.flv', '.webm', '.vob', '.mpg', '.rmvb'}
    assert SUPPORTED_EXTENSIONS == known, (
        "受支持容器变了。请确认新容器装得下自己的音轨（红线 10），"
        "确认后把它加进本测试的 known 集合。")
