"""台账渲染不许把「机器故障」和「跳过」并成一类（2026-09-06 红线复检打回）。

红线 2 在引擎里把「素材压不动」（终态）和「机器坏了」（该重试）拆开了。
但 `export_report.py` 的判据一直是二分的：

    tag_cls = "tag-succ" if decision == '保留压缩版' else "tag-skip"

于是 09-05 新加的三个失败类决定（`质量搜索未跑成(机器故障)` / `元数据继承失败` /
`产物就位失败`）在 HTML 仪表盘上与 `已是高效HEVC(跳过)` **同色同标签**。
看报表的人会以为那些文件「不值得压」，而实际是那台机器当时坏了、该重跑。

🔴 **加枚举值时要扫一遍消费方。** 引擎里拆开、台账里并回去，等于白拆。
"""

from __future__ import annotations

import csv

import pytest
from export_report import build_html_report
from vcompress_abav1 import (DECISION_KEEP, DECISION_METADATA_LOST,
                             DECISION_PLACE_FAILED, DECISION_SEARCH_BROKEN,
                             REPORT_HEADER)

MACHINE_FAULTS = [DECISION_SEARCH_BROKEN, DECISION_METADATA_LOST, DECISION_PLACE_FAILED]
MATERIAL_VERDICTS = ["已是高效HEVC(跳过)", "小文件(跳过 <20MB)", "极低码率(跳过)"]


@pytest.fixture
def rendered(tmp_path):
    csv_path = tmp_path / "_report.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(REPORT_HEADER)
        w.writerow(["ok.mp4", "100.00", "20.00", "80.0", "95.1", "30",
                    DECISION_KEEP, "yuv420p->yuv420p"])
        for i, d in enumerate(MACHINE_FAULTS):
            w.writerow([f"fault{i}.mp4", "100.00", "0.00", "0.0", "NA", "NA", d, "yuv420p"])
        for i, d in enumerate(MATERIAL_VERDICTS):
            w.writerow([f"skip{i}.mp4", "100.00", "0.00", "0.0", "NA", "NA", d, "yuv420p"])
    html_path = tmp_path / "_report.html"
    build_html_report(csv_path, html_path)
    return html_path.read_text(encoding="utf-8")


def _row_of(html, name):
    """取出含该文件名的那一行 <tr>。"""
    for chunk in html.split("<tr>"):
        if name in chunk:
            return chunk
    raise AssertionError(f"报表里找不到 {name}")


@pytest.mark.parametrize("i", range(len(MACHINE_FAULTS)))
def test_machine_faults_are_not_painted_as_skips(rendered, i):
    """🔴 本轮要修的那件事。"""
    row = _row_of(rendered, f"fault{i}.mp4")
    assert "tag-fail" in row
    assert "tag-skip" not in row


@pytest.mark.parametrize("i", range(len(MATERIAL_VERDICTS)))
def test_material_verdicts_stay_skips(rendered, i):
    """素材结论仍然是「跳过」，别矫枉过正把它们也染成故障。"""
    row = _row_of(rendered, f"skip{i}.mp4")
    assert "tag-skip" in row
    assert "tag-fail" not in row


def test_success_is_still_success(rendered):
    row = _row_of(rendered, "ok.mp4")
    assert "tag-succ" in row
    assert "tag-fail" not in row and "tag-skip" not in row


def test_the_fail_style_actually_exists(rendered):
    """光给了 class 名、CSS 里没有对应样式，视觉上还是分不出来。"""
    assert ".tag-fail" in rendered


def test_mutation_folding_faults_back_into_skips_is_caught(rendered):
    """变异验证：还原二分判据，机器故障与跳过会同色。"""
    def mutated(decision):
        return "tag-succ" if decision == DECISION_KEEP else "tag-skip"

    assert mutated(DECISION_SEARCH_BROKEN) == mutated("已是高效HEVC(跳过)")   # 变异体
    assert "tag-fail" in _row_of(rendered, "fault0.mp4")                      # 真实现
