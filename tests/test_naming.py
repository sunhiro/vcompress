"""命名契约：`marked_name` / `is_marked` / `unmarked_name`（对应 vcat ADR-020）。

🔴 **这是跨仓契约**，vcat 的 `vcat/compress_worker.py` 是单一出处，两边必须逐字一致。
走偏的后果是**静默的**：血缘配对把产物连到一个碰巧同名的无关原片上，账面看不出
任何异常。vcat 侧有 `tests/test_cross_repo_naming.py` 锁两仓一致——但那个测试
**只验得到它跑的那台机器上的这份文件**（真机上两台机器的引擎曾长期对不上哈希）。
所以本文件锁的是「这份文件自己的行为」，两个测试合起来才完整。
"""

from __future__ import annotations

import pytest
from naming import COMPRESSED_MARKER, is_marked, marked_name, unmarked_name

# (源片名, 产物名)
PAIRS = [
    ("IMG_7161.MOV", "IMG_7161.hevc.MOV"),
    ("a/b/IMG_7161.MOV", "a/b/IMG_7161.hevc.MOV"),
    ("MVI_0046.AVI", "MVI_0046.hevc.AVI"),
    ("VTS_01_2.VOB", "VTS_01_2.hevc.VOB"),
    ("带 空格 和中文.mp4", "带 空格 和中文.hevc.mp4"),
    ("点.在.名字里.mkv", "点.在.名字里.hevc.mkv"),
    ("无扩展名", "无扩展名.hevc"),
]


@pytest.mark.parametrize("src,dst", PAIRS)
def test_marked_name(src, dst):
    assert marked_name(src) == dst


@pytest.mark.parametrize("src,dst", PAIRS)
def test_unmarked_name_is_strict_inverse(src, dst):
    """严格逆运算——这是血缘配对的地基，差一个字符就连错原片。"""
    assert unmarked_name(dst) == src


@pytest.mark.parametrize("src,dst", PAIRS)
def test_is_marked_separates_the_two(src, dst):
    assert is_marked(dst) is True
    assert is_marked(src) is False


def test_container_suffix_is_kept_not_forced_to_mp4():
    """🔴 老版 `vcompress.py` 用 `.with_suffix('.mp4')` 强制转名：源 `A.MOV`
    的产物写成 `A.mp4`，全集布局下同目录若有个未压缩的 `A.mp4` 搬进来，
    两者正好同名，后到的把先到的**静默覆盖**。容器后缀必须沿用源片。"""
    assert marked_name("A.MOV").endswith(".MOV")
    assert marked_name("A.avi").endswith(".avi")


def test_marked_name_is_not_idempotent_and_that_is_on_purpose():
    """再标记一次会变成 `X.hevc.hevc.MOV`。工人那条路靠这个非幂等性
    保证「输出路径不会等于源路径」（见 process_file_abav1 的 samefile 守卫注释）。"""
    once = marked_name("X.MOV")
    twice = marked_name(once)
    assert twice == "X.hevc.hevc.MOV"
    assert twice != once


@pytest.mark.parametrize("bad", [
    "IMG_7161.MOV",          # 根本没标记
    "a/b/普通视频.mp4",
    "hevc.MOV",              # 「hevc」在 stem 但不带点，不是标记
    "X.hevcx.MOV",
])
def test_unmarked_name_refuses_instead_of_guessing(bad):
    """🔴 红线：宁可拒绝，不可错。配不上就抛，**绝不返回一个「差不多」的名字**——
    悄悄返回原名会让血缘登记把产物连到它自己，或连到一个碰巧同名的无关文件。"""
    with pytest.raises(ValueError):
        unmarked_name(bad)


def test_marker_value_is_the_contract():
    """标记串本身就是契约的一部分，改它等于改两仓协议。"""
    assert COMPRESSED_MARKER == ".hevc"
