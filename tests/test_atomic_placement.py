"""产物原子落位：先写隐藏临时名，全部校验过了才 rename 就位（本轮 2026-09-05 新修）。

老做法是 ffmpeg 用 `-y` 直接写**最终路径**，留着两个洞：

1. 失败时靠一段 `unlink` 收尾——可那段代码只在 **python 还活着**时执行得到，
   而最需要它的恰恰是进程被杀 / 断电 / SMB 掉线连带 python 一起死的时候。
   残片就留在最终路径上，而续压守卫只看「存在且非空」，下一轮把这个
   **没过闸门的残片**当成压好的产物（真机 MVI_0046.AVI 那次 SMB 掉线）。
2. `preserve_metadata` 的 `-overwrite_original` 走 rename 就位，群晖回收站
   把每个中间版都接住了——真机攒了 1010 个与产物同名的中间版占 40GB。

本文件锁的是「临时名怎么起」「谁不许被删」「就位时用户的文件属性动没动」，
不碰一个字节真视频，也不调 ffmpeg。
"""

from __future__ import annotations

import os
import stat

import pytest
from fullset import walk_rel
from vcompress_abav1 import discard_partial, partial_path, place_atomically


# ---- 临时名的两条硬约束 -----------------------------------------------------

@pytest.mark.parametrize("out_name,expected", [
    ("IMG_7161.hevc.MOV", ".IMG_7161.hevc.partial.MOV"),
    ("VTS_01_2.hevc.VOB", ".VTS_01_2.hevc.partial.VOB"),
    ("a.b.c.hevc.mp4",    ".a.b.c.hevc.partial.mp4"),
    ("X.hevc",            ".X.partial.hevc"),          # 源片本来就没扩展名
])
def test_extension_stays_at_the_end(tmp_path, out_name, expected):
    """🔴 扩展名必须原样留在末尾——**ffmpeg 是按它挑 muxer 的**。

    写成 `xxx.mp4.partial` 会让 ffmpeg 猜不出容器直接失败；而 R5 那个 `.VOB`
    产物正是靠沿用源扩展名才走上 MPEG-PS muxer 的（ACTIVE_MEMORY §5.6）。
    """
    tmp = partial_path(tmp_path / out_name)
    assert tmp.name == expected
    assert tmp.suffix == (tmp_path / out_name).suffix
    assert tmp.parent == tmp_path                      # 同目录 → 同分区 → rename 原子


def test_partial_is_hidden_so_it_never_enters_the_ledger(tmp_path):
    """🔴 半成品不许进内容清单，也不许被当成产物搬运/替换。

    `fullset.walk_rel` 与生成的 `_apply_replace.py` 都跳过点开头的文件，
    前导点就是让半成品对这两条路径**结构性隐身**。
    """
    (tmp_path / "real.hevc.mp4").write_bytes(b"product")
    partial_path(tmp_path / "other.hevc.mp4").write_bytes(b"half")
    assert walk_rel(tmp_path) == ["real.hevc.mp4"]


def test_a_leftover_partial_does_not_satisfy_the_resume_guard(tmp_path):
    """断电只留下半成品时，最终路径必须**仍然不存在**，下一轮才会重压。

    这正是老做法漏掉的那个场景：残片留在最终路径上，续压守卫
    （存在且非空 → 跳过）把它当成压好的产物，而且外部扫描也认不出来。
    """
    out = tmp_path / "IMG_1.hevc.mp4"
    partial_path(out).write_bytes(b"crashed midway")
    assert not (out.exists() and out.stat().st_size > 0)


# ---- 红线 1：删之前先确认删的不是原片 ---------------------------------------

def test_discard_refuses_to_delete_the_source_itself(tmp_path):
    """🔴 `-i x.mp4 -o x.mp4` 这种误用不许让这一刀落到原片上。"""
    src = tmp_path / "x.mp4"
    src.write_bytes(b"original footage")
    discard_partial(src, src, "冒充半成品的原片")
    assert src.exists() and src.read_bytes() == b"original footage"


def test_discard_removes_an_actual_partial(tmp_path):
    src = tmp_path / "x.mp4"
    src.write_bytes(b"original")
    tmp = partial_path(tmp_path / "x.hevc.mp4")
    tmp.write_bytes(b"half")
    discard_partial(tmp, src, "半成品")
    assert not tmp.exists()
    assert src.exists()


def test_discard_on_a_missing_path_is_a_no_op(tmp_path):
    discard_partial(tmp_path / "nope.mp4", tmp_path / "src.mp4", "不存在的")


# ---- 就位 -------------------------------------------------------------------

def test_place_moves_it_and_leaves_no_partial(tmp_path):
    out = tmp_path / "IMG_1.hevc.mp4"
    tmp = partial_path(out)
    tmp.write_bytes(b"finished product")
    assert place_atomically(tmp, out) is True
    assert out.read_bytes() == b"finished product"
    assert not tmp.exists()


def test_place_reports_failure_instead_of_raising(tmp_path):
    """就位失败要能被调用方接住（它随后会丢弃半成品并记 failed），不是抛出去。"""
    out = tmp_path / "missing_dir" / "IMG_1.hevc.mp4"
    tmp = partial_path(tmp_path / "IMG_1.hevc.mp4")
    tmp.write_bytes(b"x")
    assert place_atomically(tmp, out) is False
    assert tmp.exists()                                # 没就位就别把它弄丢


@pytest.mark.skipif(not hasattr(os, "chflags"), reason="需要 BSD/macOS 的 chflags")
def test_inherited_uchg_does_not_block_placement_and_is_put_back(tmp_path):
    """🔴 `shutil.copystat` 会把 `st_flags` 一起复制过来。

    源片带 `uchg`（Finder「已锁定」）时产物就继承了不可变标志，**之后对它做
    任何改名都会 EPERM**——`fullset.apply_moves` 的注释记着同一个坑，真机上
    撞到过 133 个微信导出视频全带这个标志。做法必须与那边一致：
    临时摘掉、落位后**原样贴回**，用户的文件属性一个位都不改。
    """
    out = tmp_path / "IMG_1.hevc.mp4"
    tmp = partial_path(out)
    tmp.write_bytes(b"product")
    os.chflags(tmp, stat.UF_IMMUTABLE)
    try:
        assert place_atomically(tmp, out) is True
        assert out.exists() and not tmp.exists()
        assert os.lstat(out).st_flags & stat.UF_IMMUTABLE, "锁定状态被吃掉了"
    finally:
        for p in (out, tmp):
            if p.exists():
                os.chflags(p, 0)


@pytest.mark.skipif(not hasattr(os, "chflags"), reason="需要 BSD/macOS 的 chflags")
def test_mutation_dropping_the_uchg_dance_is_caught(tmp_path):
    """变异验证：去掉摘锁那步，继承了 uchg 的产物就位会 EPERM。"""
    out = tmp_path / "IMG_2.hevc.mp4"
    tmp = partial_path(out)
    tmp.write_bytes(b"product")
    os.chflags(tmp, stat.UF_IMMUTABLE)
    try:
        with pytest.raises(OSError):
            os.replace(tmp, out)                       # 变异体：直接 rename
        assert place_atomically(tmp, out) is True      # 真实现照样过
    finally:
        for p in (out, tmp):
            if p.exists():
                os.chflags(p, 0)


# ---- 编排层：每一条失败路径都不许在盘上留东西 -------------------------------
#
# 上面锁的是三个 helper 各自的行为，这里锁的是 `process_file_abav1` **用不用
# 它们**。真机上出事的从来不是 helper 写错了，而是某条失败路径忘了调它
# （2026-09-03 那轮修的第 2 条就是「编码失败时不 unlink 半成品」）。

from conftest import leftovers, make_args


def test_encode_failure_leaves_nothing_behind(bench, monkeypatch):
    """🔴 编码失败 → 半成品必须删掉，最终路径必须仍然不存在。

    不删它，下一轮的续压守卫（存在且非空 → 跳过）就会把这个**没过闸门的
    残片**当成压好的产物（真机 MVI_0046.AVI 那次 SMB 掉线）。
    """
    def crashing_encode(src_path, out_path, *a, **k):
        out_path.write_bytes(b"half a file")     # 盘上真的留下了半个文件
        return False
    monkeypatch.setattr(bench.eng, "run_ab_av1_encode", crashing_encode)

    status, *rest = bench.eng.process_file_abav1(bench.src, bench.out, make_args())
    assert status == "failed"
    assert rest[5] == "编码失败"
    assert not bench.out.exists()
    assert leftovers(bench.tmp_dir) == ["CLIP.mp4"], "残片留在盘上了"


def test_placement_failure_leaves_nothing_behind(bench, monkeypatch):
    """就位那一步失败（EPERM / 磁盘满 / 掉线）同样不许留残片。"""
    monkeypatch.setattr(bench.eng, "place_atomically", lambda t, o: False)
    status, *rest = bench.eng.process_file_abav1(bench.src, bench.out, make_args())
    assert status == "failed"
    assert rest[5] == bench.eng.DECISION_PLACE_FAILED
    assert not bench.out.exists()
    assert leftovers(bench.tmp_dir) == ["CLIP.mp4"]


def test_a_stale_partial_never_survives_into_this_round_product(bench):
    """上一轮崩掉留下的半成品，不许混进本轮产物。

    ⚠ 老实说：这条**锁不住那行 `discard_partial(..., "上一轮遗留的半成品")`**。
    ffmpeg 带 `-y`、夹具的 fake_encode 也一样，临时名本来就会被覆写，
    去掉那行本测试照样绿（2026-09-05 变异验证实测漏网）。这里锁住的是
    「结果对不对」，那行的定位是冗余防御，引擎里已如实标注。
    """
    stale = partial_path(bench.out)
    stale.write_bytes(b"garbage from last time")
    status, *_ = bench.eng.process_file_abav1(bench.src, bench.out, make_args())
    assert status == "success"
    assert bench.out.read_bytes() == b"y" * (1024 * 1024)   # 是本轮压的，不是旧残片
    assert leftovers(bench.tmp_dir) == ["CLIP.hevc.mp4", "CLIP.mp4"]


# ---- 2026-09-06 红线复检打回的四条 -----------------------------------------

def test_orphan_partial_is_swept_even_when_the_file_takes_an_early_return(
        bench, monkeypatch, tmp_path):
    """🔴 孤儿半成品：进程被杀留下它，而下一轮这个文件走了「提前返回」的分支。

    原子落位把残渣挪成了隐藏文件——**三本账都看不见它**（`_manifest.csv` /
    `_report.csv` / `walk_rel` 全跳点文件）。若只在编码步里清，一个几 GB 的
    残片会因为「这个文件后来被判成小文件/低码率/已是HEVC/闸门拒绝」而永久留下。
    """
    import sys
    src_dir = tmp_path / "in"; src_dir.mkdir()
    clip = src_dir / "CLIP.mp4"; clip.write_bytes(b"z" * 2048)
    out_dir = tmp_path / "out"; out_dir.mkdir()

    orphan = partial_path(out_dir / "CLIP.hevc.mp4")
    orphan.write_bytes(b"g" * 4096)
    assert orphan.exists()

    eng = bench.eng
    monkeypatch.setattr(eng, "check_dependencies", lambda: None)
    monkeypatch.setattr(eng, "build_excel_report", lambda *a, **k: None)
    monkeypatch.setattr(eng, "build_html_report", lambda *a, **k: None)
    # 提前返回的分支：小文件跳过（连 process_file_abav1 的编码步都进不去）
    monkeypatch.setattr(eng, "process_file_abav1",
                        lambda s_, o_, a_: ("skipped_small_file", 0.1, 0.0, 0.0,
                                            "NA", "NA", "小文件(跳过 <20MB)", ""))
    monkeypatch.setattr(sys, "argv",
                        ["vcompress_abav1.py", "-i", str(src_dir), "-o", str(out_dir)])
    eng.main()
    assert not orphan.exists(), "孤儿半成品没被清掉，会永久留在盘上且不进任何账"


def test_a_locked_partial_can_still_be_discarded(tmp_path):
    """🔴 带 uchg 的半成品必须删得掉，否则就位失败时残片永久留下。

    `shutil.copystat` 会从源片继承 `st_flags`——继承它的正是「133 个微信导出
    视频」那批人群，而那批人恰恰最可能撞上就位失败。
    """
    if not hasattr(os, "chflags"):
        pytest.skip("需要 BSD/macOS 的 chflags")
    src = tmp_path / "x.mp4"; src.write_bytes(b"orig")
    tmp = partial_path(tmp_path / "x.hevc.mp4"); tmp.write_bytes(b"half")
    os.chflags(tmp, stat.UF_IMMUTABLE)
    try:
        discard_partial(tmp, src, "带锁的半成品")
        assert not tmp.exists(), "带 uchg 的半成品删不掉，隐藏残片会永久留在盘上"
    finally:
        if tmp.exists():
            os.chflags(tmp, 0); tmp.unlink()


def test_place_refuses_when_target_is_the_partial_itself(tmp_path):
    """🔴 红线 1 纵深防御：`os.replace` 覆写比 unlink 更不可逆，
    `discard_partial` 设了 samefile 守卫，这边不设就是双重标准。"""
    tmp = tmp_path / "x.hevc.mp4"; tmp.write_bytes(b"data")
    assert place_atomically(tmp, tmp) is False
    assert tmp.read_bytes() == b"data"
