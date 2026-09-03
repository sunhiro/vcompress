"""报表旧行清理（对应 vcat KANBAN P2-58 / P2-59）。

两个真机事故，方向相反，同一段代码：

1. **不清 → 每轮重复追加。** 续压守卫是「产物存在且非空 → 跳过」，而跳过类
   文件根本不产出产物，守卫拦不住。真机上 69 个唯一文件被写成 131 行，
   「已是高效HEVC(跳过)」12 个记成 51 个，而 HTML 仪表盘的 KPI 按行数汇总。
2. 🔴 **清过头 → 抹掉血缘证据。** 第一版修复把 preview 下的**全部**旧行清掉，
   包括「保留压缩版」那些——它们带着 VMAF/CRF 与 ADR-027 加的第 8 列实测
   像素格式，是 vcat 血缘登记 `--report` **唯一认的行**，而 preview
   重新生成不出来（它写 `PREVIEW成功`）。对一棵压完的树跑一次 preview
   就把证据抹了，**而那正是 skill 标准两趟跑法的第一趟**。

第 2 条是这一轮自己引入的回归，由独立复检员抓出。本文件就是不让它再来一次。
"""

from __future__ import annotations

import csv
import os

import pytest
from vcompress_abav1 import (REPORT_HEADER, prune_report_csv, prune_report_rows,
                             report_prune_targets)

KEEP = "保留压缩版"


def row(name, decision, vmaf="94.35", crf="26", pix="yuvj422p->yuv420p"):
    return [name, "165.92", "53.74", "67.6", vmaf, crf, decision, pix]


# ---- prune_report_rows：判据本身 -------------------------------------------

def test_normal_mode_drops_rows_that_will_be_rewritten():
    rows = [row("a.MOV", KEEP), row("b.MOV", "已是高效HEVC(跳过)")]
    assert prune_report_rows(rows, {"b.MOV"}, preview=False) == [rows[0]]


def test_normal_mode_keeps_rows_of_files_not_in_this_run():
    """行键不在本轮目标里 = 别的文件的账，一个字都不许动。"""
    rows = [row("别处.MOV", KEEP)]
    assert prune_report_rows(rows, {"a.MOV"}, preview=False) == rows


@pytest.mark.parametrize("decision", ["PREVIEW成功", "已是高效HEVC(跳过)",
                                      "小文件(跳过 <20MB)", "10-bit HDR(跳过)",
                                      "极低码率(跳过)", "未过质量门限(VMAF<93.0)",
                                      "编码失败", "无元数据(跳过)"])
def test_preview_drops_every_class_except_the_evidence_rows(decision):
    """preview 本轮会把这些行照样再产出一遍，清掉才不会每跑一次就重追整棵树。"""
    rows = [row("a.MOV", decision)]
    assert prune_report_rows(rows, {"a.MOV"}, preview=True) == []


def test_preview_must_not_touch_the_lineage_evidence_rows():
    """🔴 本文件的核心断言。preview 绝不能碰「保留压缩版」。"""
    evidence = row("a.MOV", KEEP)
    rows = [evidence, row("a.MOV", "PREVIEW成功")]
    assert prune_report_rows(rows, {"a.MOV"}, preview=True) == [evidence]


def test_preview_keeps_vmaf_crf_and_pixel_format_intact():
    """血缘登记认的是这三样，逐字节留住。"""
    evidence = row("a.MOV", KEEP, vmaf="93.02", crf="28", pix="yuvj422p->yuv420p")
    kept = prune_report_rows([evidence], {"a.MOV"}, preview=True)
    assert kept == [evidence]
    assert kept[0][4] == "93.02" and kept[0][5] == "28"
    assert kept[0][7] == "yuvj422p->yuv420p"


def test_malformed_short_rows_survive_preview():
    """⚠ 列数不足的畸形行内容未知，静默丢掉又是一次「把数据丢失说成排版瑕疵」。"""
    short = ["a.MOV", "短行"]
    assert prune_report_rows([short], {"a.MOV"}, preview=True) == [short]


def test_empty_rows_survive():
    assert prune_report_rows([[]], {"a.MOV"}, preview=True) == [[]]
    assert prune_report_rows([[]], {"a.MOV"}, preview=False) == [[]]


def test_mutation_preview_using_the_normal_predicate_is_caught():
    """变异验证：让 preview 照普通模式判据走（= 第一版那个回归）。"""
    def mutated(rows, targets, preview):
        return [r for r in rows if not (r and r[0] in targets)]

    evidence = row("a.MOV", KEEP)
    assert mutated([evidence], {"a.MOV"}, True) == []                 # 证据被抹
    assert prune_report_rows([evidence], {"a.MOV"}, True) == [evidence]


def test_mutation_dropping_nothing_at_all_is_caught():
    """变异验证：整个清理退化成 no-op（= 缺陷 1，每轮重复追加）。"""
    def mutated(rows, targets, preview):
        return list(rows)                       # no-op 变异体

    rows = [row("a.MOV", "已是高效HEVC(跳过)")]
    assert mutated(rows, {"a.MOV"}, False) == rows      # 变异体留下这行 → 下轮再追加
    assert prune_report_rows(rows, {"a.MOV"}, preview=False) == []


# ---- report_prune_targets：谁算「本轮会重新产出」 ---------------------------

def _tree(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    (src / "a").mkdir(parents=True)
    for rel in ("a/done.MOV", "a/todo.MOV", "a/empty.MOV"):
        (src / rel).write_bytes(b"x")
    out.mkdir()
    (out / "a").mkdir()
    (out / "a/done.hevc.MOV").write_bytes(b"compressed")
    (out / "a/empty.hevc.MOV").write_bytes(b"")        # 零字节 = 残片，不算数
    files = [src / "a/done.MOV", src / "a/todo.MOV", src / "a/empty.MOV"]
    return src, out, files


def test_targets_skip_files_the_resume_guard_will_skip(tmp_path):
    src, out, files = _tree(tmp_path)
    assert report_prune_targets(files, src, out, preview=False) == {
        os.path.join("a", "todo.MOV"), os.path.join("a", "empty.MOV")}


def test_zero_byte_product_does_not_count_as_done(tmp_path):
    """残片不是产物——续压守卫要求「存在**且非空**」。"""
    src, out, files = _tree(tmp_path)
    assert os.path.join("a", "empty.MOV") in report_prune_targets(files, src, out, preview=False)


def test_preview_targets_everything_because_it_bypasses_the_guard(tmp_path):
    src, out, files = _tree(tmp_path)
    assert len(report_prune_targets(files, src, out, preview=True)) == 3


def test_comma_in_filename_matches_the_report_row_key(tmp_path):
    """报表行键写入时把逗号换成空格（裸 CSV 拼接的历史包袱），目标集必须同口径，
    否则那一行永远匹配不上、永远清不掉，于是每轮重复追加。"""
    src, out = tmp_path / "s", tmp_path / "o"
    src.mkdir(); out.mkdir()
    f = src / "有,逗号.MOV"
    f.write_bytes(b"x")
    assert report_prune_targets([f], src, out, preview=False) == {"有 逗号.MOV"}


# ---- prune_report_csv：落盘 -------------------------------------------------

def _write_report(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(REPORT_HEADER)
        w.writerows(rows)


def test_rewrite_keeps_header_once(tmp_path):
    rc = tmp_path / "_report.csv"
    _write_report(rc, [row("a.MOV", KEEP), row("b.MOV", "编码失败")])
    assert prune_report_csv(rc, {"b.MOV"}, preview=False) is True
    with open(rc, encoding="utf-8-sig", newline="") as fh:
        got = list(csv.reader(fh))
    assert got[0] == REPORT_HEADER
    assert [r[0] for r in got[1:]] == ["a.MOV"]
    assert sum(1 for r in got if r == REPORT_HEADER) == 1


def test_rewrite_leaves_no_tmp_file_behind(tmp_path):
    rc = tmp_path / "_report.csv"
    _write_report(rc, [row("a.MOV", KEEP)])
    prune_report_csv(rc, set(), preview=False)
    assert not (tmp_path / "_report.csv.tmp").exists()


def test_a_crash_mid_rewrite_must_not_zero_the_report(tmp_path, monkeypatch):
    """🔴 直接 open('w') 是先截断后写，中途崩就把整份报表清零——而 except 里
    那句「可能出现重复行」会把一次**数据丢失**说成排版瑕疵。
    先写临时文件再 os.replace，崩了原报表必须一个字节没动。"""
    rc = tmp_path / "_report.csv"
    _write_report(rc, [row("a.MOV", KEEP), row("b.MOV", "编码失败")])
    before = rc.read_bytes()

    def boom(*a, **k):
        raise OSError("盘满")
    monkeypatch.setattr(os, "replace", boom)

    assert prune_report_csv(rc, {"b.MOV"}, preview=False) is False
    assert rc.read_bytes() == before          # 原报表逐字节未动
    assert not (tmp_path / "_report.csv.tmp").exists()


def test_two_preview_runs_do_not_grow_the_report(tmp_path):
    """缺陷 1 的形状：`--preview` 绕过续压守卫，每跑一次就把整棵树重追一遍。"""
    rc = tmp_path / "_report.csv"
    evidence = row("a.MOV", KEEP)
    _write_report(rc, [evidence, row("a.MOV", "PREVIEW成功")])

    for _ in range(3):
        prune_report_csv(rc, {"a.MOV"}, preview=True)
        with open(rc, "a", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerow(row("a.MOV", "PREVIEW成功"))   # 模拟本轮追加

    with open(rc, encoding="utf-8-sig", newline="") as fh:
        got = [r for r in csv.reader(fh) if r and r[0] == "a.MOV"]
    assert len(got) == 2                       # 证据 1 行 + 本轮 preview 1 行
    assert got.count(evidence) == 1            # 🔴 证据活到了第三轮
