"""内容清单 `_manifest.csv` 与全集搬运（对应 vcat ADR-020）。

`_report.csv` 是**单次运行的日志**，`_manifest.csv` 是**这棵树的账**。
真机上混淆二者漏记过 66 个产物：08-03 跑完写了 148 行，08-04 又压了 66 个，
盘上 214 个产物、报表只认 148 个，谁都没发现。
"""

from __future__ import annotations

import csv

import fullset
import pytest
from fullset import (HASHING_AVAILABLE, MANIFEST_FIELDS, MANIFEST_NAME, full_hash,
                     manifest_row, plan_move_untouched, read_manifest, walk_rel,
                     write_manifest)


def _row(dst="a/x.hevc.MOV", src="a/x.MOV", dst_bytes=50, src_bytes=100, **kw):
    return manifest_row(dst_rel=dst, src_rel=src, dst_bytes=dst_bytes,
                        src_bytes=src_bytes, **kw)


# ---- 清单读写 ---------------------------------------------------------------

def test_roundtrip(tmp_path):
    rows = {"a/x.hevc.MOV": _row()}
    write_manifest(tmp_path, rows)
    back = read_manifest(tmp_path)
    assert list(back) == ["a/x.hevc.MOV"]
    assert back["a/x.hevc.MOV"]["源相对路径"] == "a/x.MOV"


def test_missing_manifest_reads_as_empty_not_error(tmp_path):
    assert read_manifest(tmp_path) == {}


def test_write_is_atomic_and_leaves_no_tmp(tmp_path):
    """跑一半断电留下半截清单比没有清单更坏——它看起来是完整的。"""
    write_manifest(tmp_path, {"a": _row()})
    assert not (tmp_path / "_manifest.csv.tmp").exists()
    assert (tmp_path / MANIFEST_NAME).is_file()


def test_upsert_semantics_not_rewrite(tmp_path):
    """续跑是 upsert：新增不抹掉上一轮的行（漏记 66 个正是这个形状）。"""
    write_manifest(tmp_path, {"a/x.hevc.MOV": _row()})
    rows = read_manifest(tmp_path)
    rows["a/y.hevc.MOV"] = _row(dst="a/y.hevc.MOV", src="a/y.MOV")
    write_manifest(tmp_path, rows)
    assert set(read_manifest(tmp_path)) == {"a/x.hevc.MOV", "a/y.hevc.MOV"}


def test_field_order_is_the_contract(tmp_path):
    write_manifest(tmp_path, {"a": _row()})
    with open(tmp_path / MANIFEST_NAME, encoding="utf-8-sig", newline="") as fh:
        assert next(csv.reader(fh)) == MANIFEST_FIELDS


# ---- 哈希缺席：留空，且必须说出来 -------------------------------------------

def test_vmaf_absent_is_blank_not_invented():
    """🔴 本轮没测就留空，绝不编一个数（红线：宁可拒绝，不可错）。"""
    assert _row(vmaf=None)["VMAF"] == ""
    assert _row(vmaf=93.02)["VMAF"] == 93.02


def test_full_hash_returns_empty_when_blake3_missing(tmp_path, monkeypatch):
    """🔴 缺 blake3 时返回空串，**绝不用 sha256 之类顶替**——那会产生一列
    「看着像、和 vcat 台账对不上」的哈希，比留空危险得多。"""
    f = tmp_path / "f.bin"
    f.write_bytes(b"hello")
    monkeypatch.setattr(fullset, "_blake3", None)
    assert full_hash(f) == ""


def test_hashing_available_flag_tells_the_truth():
    """调用方靠这个标志决定要不要 WARNING。真机上曾拿空哈希做校验，
    代码写的是 `if 记录值 and 实测 != 记录值` → **短路跳过，报出一轮「全部吻合」**。"""
    assert HASHING_AVAILABLE is (fullset._blake3 is not None)


@pytest.mark.skipif(not HASHING_AVAILABLE, reason="本环境没有 blake3（与生产一致）")
def test_full_hash_is_stable(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"hello")
    assert full_hash(f) == full_hash(f) and len(full_hash(f)) == 64


def test_saving_pct_is_blank_when_source_is_zero_bytes():
    assert _row(src_bytes=0)["省%"] == ""
    assert _row(dst_bytes=50, src_bytes=100)["省%"] == "50.0"


# ---- 遍历与搬运 -------------------------------------------------------------

def test_walk_skips_dotfiles_and_own_products(tmp_path):
    (tmp_path / ".vcat-trash").mkdir()
    (tmp_path / ".vcat-trash/x.MOV").write_bytes(b"x")
    (tmp_path / "._AppleDouble").write_bytes(b"x")
    (tmp_path / "_report.csv").write_bytes(b"x")
    (tmp_path / MANIFEST_NAME).write_bytes(b"x")
    (tmp_path / "real.MOV").write_bytes(b"x")
    assert walk_rel(tmp_path) == ["real.MOV"]


def test_plan_move_leaves_compressed_sources_behind(tmp_path):
    """压过的源片留在源目录当备份，只搬没压过的。"""
    src, out = tmp_path / "s", tmp_path / "o"
    src.mkdir(); out.mkdir()
    (src / "pressed.MOV").write_bytes(b"x")
    (src / "untouched.mp4").write_bytes(b"x")
    (out / "pressed.hevc.MOV").write_bytes(b"x")
    moves, problems = plan_move_untouched(src, out)
    assert problems == []
    assert [s.name for s, _ in moves] == ["untouched.mp4"]


def test_plan_move_reports_collision_instead_of_overwriting(tmp_path):
    """🔴 撞车要报出来，不能悄悄覆盖——那就是静默丢一份文件。"""
    src, out = tmp_path / "s", tmp_path / "o"
    src.mkdir(); out.mkdir()
    (src / "same.mp4").write_bytes(b"src")
    (out / "same.mp4").write_bytes(b"out")
    moves, problems = plan_move_untouched(src, out)
    assert moves == []
    assert any("撞车" in p for p in problems)


def test_plan_move_is_a_plan_only_nothing_moves(tmp_path):
    """**只出计划，不动盘**。"""
    src, out = tmp_path / "s", tmp_path / "o"
    src.mkdir(); out.mkdir()
    (src / "a.mp4").write_bytes(b"x")
    plan_move_untouched(src, out)
    assert (src / "a.mp4").exists() and not (out / "a.mp4").exists()


def test_unpairable_product_name_becomes_a_problem_not_a_guess(tmp_path):
    """带标记但配不回源片名的产物 → 记进问题清单。调用方见到 problems 就整批不搬。"""
    src, out = tmp_path / "s", tmp_path / "o"
    src.mkdir(); out.mkdir()
    (out / "x.hevcx.hevc.MOV").write_bytes(b"x")     # is_marked 认得，unmarked 配得上
    (src / "keep.mp4").write_bytes(b"x")
    moves, problems = plan_move_untouched(src, out)
    assert [s.name for s, _ in moves] == ["keep.mp4"]
    assert problems == []
