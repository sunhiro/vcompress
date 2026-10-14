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


def _posix(p: str) -> str:
    """把路径分隔符归一到 `/` 再比。

    Windows 上 `pathlib` 拼出来的是 `a\\b\\X.hevc.MOV`，POSIX 上是 `a/b/...`。
    本测试锁的是**命名语义**——前缀目录原样保留、标记插在主干末尾、容器后缀沿用
    ——不是那台机器的分隔符。断言里写死 `/` 只会让 Windows 上跑 pytest 必红，
    真红灯和假红灯混在一起，就没人再看了（2026-10-13 前本项目正是如此）。

    🔴 `naming.py` 一个字都不改：它和 vcat `compress_worker.py` 必须逐字一致，
    分隔符是 `pathlib` 按平台给的，不是我们能选的口径。要让的只能是测试。
    """
    return p.replace("\\", "/")


@pytest.mark.parametrize("src,dst", PAIRS)
def test_marked_name(src, dst):
    assert _posix(marked_name(src)) == _posix(dst)


@pytest.mark.parametrize("src,dst", PAIRS)
def test_unmarked_name_is_strict_inverse(src, dst):
    """严格逆运算——这是血缘配对的地基，差一个字符就连错原片。"""
    assert _posix(unmarked_name(dst)) == _posix(src)


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


# ---------------------------------------------------------------- 生成脚本里的第三份抄件
#
# 🔴 这套命名规则在本仓有**三份**手工抄件：`naming.py`（上面锁的这份）、
#    vcat 的 `compress_worker.py`、以及 `write_replace_scripts` 塞进
#    `_apply_replace.py` 模板里的那份。
#
#    第三份最危险，因为它**没有任何模块会 import 它**——它是 f-string 拼出来的文本，
#    语法错、逻辑漂了，测试都照绿，直到用户某天真去跑那个脚本。而它干的事是
#    「删掉源目录里的原片、把产物搬过去」，`unmarked_name` 一旦返回个「差不多」的名字，
#    被删的就是**别的文件**。
#
#    所以这里不验函数，验**产物**：生成 → 落盘 → 编译 → 和 `naming.py` 逐例对撞。


@pytest.fixture(scope="module")
def generated_replace_script(tmp_path_factory):
    """真跑一遍 `write_replace_scripts`，把生成出来的脚本当模块加载回来。"""
    import importlib.util

    import vcompress_abav1 as engine

    base = tmp_path_factory.mktemp("apply_replace")
    src_dir, out_dir = base / "src", base / "out"
    src_dir.mkdir()
    out_dir.mkdir()

    engine.write_replace_scripts(src_dir, out_dir)

    script = out_dir / "_apply_replace.py"
    assert script.exists(), "write_replace_scripts 没有落盘 _apply_replace.py"

    spec = importlib.util.spec_from_file_location("_apply_replace_generated", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # 编译不过 / 顶层出错就在这里炸
    return mod


@pytest.mark.parametrize("src,dst", PAIRS)
def test_generated_script_unmarks_exactly_like_naming_py(generated_replace_script, src, dst):
    """生成脚本里的 `unmarked_name` 必须与 `naming.py` **逐例同解**。"""
    assert _posix(generated_replace_script.unmarked_name(dst)) == _posix(unmarked_name(dst))


@pytest.mark.parametrize("src,dst", PAIRS)
def test_generated_script_marks_exactly_like_naming_py(generated_replace_script, src, dst):
    assert (generated_replace_script.is_marked(dst) is True) == (is_marked(dst) is True)
    assert (generated_replace_script.is_marked(src) is False) == (is_marked(src) is False)


@pytest.mark.parametrize("bad", ["IMG_7161.MOV", "hevc.MOV", "X.hevcx.MOV"])
def test_generated_script_refuses_instead_of_guessing(generated_replace_script, bad):
    """🔴 生成脚本也必须「宁可拒绝，不可错」。

    老模板配不上时是 `return rel_path`（原样返回）——那正好是**最坏**的一种：
    脚本会把产物当成它自己的源片，算出的目标路径就是它自己，用户以为在还原、
    实际什么都没做或删错文件。
    """
    with pytest.raises(ValueError):
        generated_replace_script.unmarked_name(bad)
