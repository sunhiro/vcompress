"""位深 / 色度守卫（红线 §11）：`pix_bit_depth` / `pix_chroma` /
`bitdepth_guard` / `chroma_downgrade_note` / `chroma_guard`。

为什么这层值得单独锁：**VMAF 只在亮度通道上算**，对 10bit→8bit 的渐变色带
(banding) 不敏感。也就是说位深降级这件事**闸门永远报不出来**，它唯一的守卫
就是这里判得对不对 —— 判错了没有任何东西会兜住。

还有一层：这套函数和 `vcompress.py:187-234` 是**两份手抄实现**（跨引擎，import
不过来）。抄错了不报错，只会让两条引擎对同一个源片给出不同的结论。所以本文件
除了锁行为，还直接拿两份实现逐格式对撞 —— 与 `test_naming.py` 锁跨仓命名契约同理。
"""

from __future__ import annotations

import pytest

import vcompress as backup_engine
import vcompress_abav1 as main_engine
from vcompress_abav1 import (bitdepth_guard, chroma_downgrade_note, chroma_guard,
                             pix_bit_depth, pix_chroma)

# 真机上见过的 + 每个名字里埋着坑的
FORMATS = [
    "yuv420p",        # 绝大多数 8bit
    "yuvj420p",       # 真机 56 个（全范围 JPEG 色域）
    "yuvj422p",       # 真机 50 个（老 MJPEG SD）—— 会被降成 4:2:0
    "yuv420p10le",    # 10bit 主战场
    "yuv422p10le",
    "yuv420p12le",
    "yuv420p16le",
    "yuv410p",        # 🔴 名字里有 '10'，子串匹配会把它当 10bit
    "nv12",           # 🔴 名字里有 '12'，子串匹配会把它当 12bit HDR
    "nv21",
    "p010le",         # 🔴 010 才是位深，且 le 得先剥掉
    "p012le",
    "p210le",
    "gray",
    "gray10le",
    "?",              # ffprobe 探不出来
    "",
]


@pytest.mark.parametrize("fmt", FORMATS)
def test_bit_depth_and_chroma_agree_with_the_backup_engine(fmt):
    """🔴 两份手抄实现必须逐格式给出**同一个答案**。

    不是「差不多就行」：同一棵树用 `-e vaapi` 在 Mac 侧（abav1 引擎）和 NAS 侧
    （vcompress.py）跑，位深判得不一样就会出现「同源片一边拦一边放」，
    而账面上两边都写着正常。抄漏一个 `le`、或把 `nv12` 的 12 当成位深，都在这条上炸。
    """
    assert pix_bit_depth(fmt) == backup_engine.pix_bit_depth(fmt), f"位深判定分歧: {fmt}"
    assert pix_chroma(fmt) == backup_engine.pix_chroma(fmt), f"色度判定分歧: {fmt}"


@pytest.mark.parametrize("fmt,depth", [
    ("yuv420p", 8), ("yuvj420p", 8), ("yuvj422p", 8),
    ("yuv420p10le", 10), ("yuv422p10le", 10), ("yuv420p12le", 12), ("yuv420p16le", 16),
    ("nv12", 8),          # 12 是像素数不是位深
    ("p010le", 10), ("p012le", 12), ("p210le", 10),
    ("yuv410p", 8),       # 410 是色度不是位深
    ("gray", 8),
    ("gray10le", 10), ("gray12be", 12),   # 🔴 gray 族位深要真解析，漏了会绕过降位深守卫
    ("?", 8), ("", 8),    # 探不出来按 8 算（不据此拦人）
])
def test_bit_depth(fmt, depth):
    assert pix_bit_depth(fmt) == depth


@pytest.mark.parametrize("fmt,chroma", [
    ("yuv420p", "420"), ("yuvj422p", "422"), ("yuv420p10le", "420"), ("yuv410p", "410"),
    ("nv12", "420"), ("nv16", "422"), ("p010le", "420"), ("p210le", "422"),
    ("gray", "gray"), ("gray10le", "gray"),
    ("?", "?"), ("", "?"),
])
def test_chroma(fmt, chroma):
    """`yuv420p10le` 必须读成 420 —— 位深数字不许被误读成色度。"""
    assert pix_chroma(fmt) == chroma


def test_vaapi_refuses_10bit_by_default():
    """🔴 红线 §11 的核心断言：vaapi 支路写死 nv12(8bit)，10bit 源默认**拒绝**。"""
    assert bitdepth_guard("yuv420p10le", "vaapi") is not None
    assert bitdepth_guard("p010le", "vaapi") is not None


def test_vaapi_allows_8bit_and_the_opt_out():
    assert bitdepth_guard("yuv420p", "vaapi") is None
    assert bitdepth_guard("nv12", "vaapi") is None
    # 显式放行才过
    assert bitdepth_guard("yuv420p10le", "vaapi", allow_downgrade=True) is None


@pytest.mark.parametrize("encoder", ["vt", "x265", "svt-av1", "x264", "nvenc"])
def test_other_encoders_are_not_forced_8bit(encoder):
    """产物位深跟着源片走的编码器不该被拦。这张表只该有写死像素格式的路径。"""
    assert bitdepth_guard("yuv420p10le", encoder) is None
    assert chroma_downgrade_note("yuvj422p", encoder) is None


def test_probe_failure_is_not_a_reason_to_refuse():
    """探不出像素格式时不许拦 —— 那会把「机器读不出来」记成「素材有问题」。"""
    assert bitdepth_guard("?", "vaapi") is None
    assert bitdepth_guard("", "vaapi") is None


def test_chroma_downgrade_is_recorded_not_refused():
    """色度降级默认放行（红线 §11），但必须留痕：返回值就是要写进日志的那句话。

    真机那 50 个 `yuvj422p` 走的正是这条路 —— 4:2:2 减半到 4:2:0，闸门全程无感，
    账上靠台账第 8 列兜着。
    """
    note = chroma_downgrade_note("yuvj422p", "vaapi")
    assert note is not None
    assert "422" in note and "420" in note
    # 同族不报，缺信息不报
    assert chroma_downgrade_note("nv12", "vaapi") is None
    assert chroma_downgrade_note("?", "vaapi") is None


def test_forced_output_table_matches_the_backup_engine():
    """那张「写死产物像素格式」的表也必须同源，否则一边知一边不知。"""
    assert main_engine.FORCED_OUTPUT_PIX == backup_engine.FORCED_OUTPUT_PIX


@pytest.mark.parametrize("status", ["skipped_bitdepth_guard", "skipped_chroma_guard"])
def test_guard_status_is_reported_as_skipped(status):
    """新状态必须落进既有的四类分派：`skipped_*` → `skipped`（终态）。

    否则 vcat 的工人会把它当 `failed`（机器故障、要删残片重做）——
    「素材不该压」被记成「机器坏了」，正是 §6.2 那条要避免的记账错误。
    """
    assert main_engine.json_status_for(status) == "skipped"


def test_chroma_is_allowed_by_default_and_refusable_on_request():
    """色度降级**默认放行**——这是红线 §11 明确的两类损失的区别对待。

    备份引擎多给了一个 `--no-chroma-downgrade`；主引擎必须同名同语义，
    否则同一棵树在 Mac 侧和 NAS 侧跑，同一个开关一边认一边报
    `unrecognized arguments`。
    """
    assert chroma_guard("yuvj422p", "vaapi") is None                        # 默认放行
    assert chroma_guard("yuvj422p", "vaapi", refuse_downgrade=True) is not None
    # 同族 / 缺信息 / 不受强制的编码器：显式要求也不拦
    assert chroma_guard("nv12", "vaapi", refuse_downgrade=True) is None
    assert chroma_guard("?", "vaapi", refuse_downgrade=True) is None
    assert chroma_guard("gray", "vaapi", refuse_downgrade=True) is None
    assert chroma_guard("yuvj422p", "vt", refuse_downgrade=True) is None
