"""`--json-out` 的四类结论必须各走各的（对应 vcat KANBAN P2-56 / P2-59）。

真机事故链：老版写的是 `status.startswith("skipped")` 里面**再判**
`status == "failed"` —— **恒假**，`quality_rejected` 一次都没发出去过。
于是「闸门拒了」（素材结论、终态）与「编码失败」（机器故障、该重试）
被压成同一个 `failed`，而 vcat 的工人对 `failed` 是「记终态、不重试」，
结果 **真正的机器故障永远不会被重做**（MVI_0046.AVI 那次 SMB 掉线就是）。

vcat 服务端 P2-44 在自己那边花力气把三种结论分开，源头一抹平就白做了。
"""

from __future__ import annotations

import pytest
from vcompress_abav1 import json_status_for

# process_file_abav1 现有的全部内部 status → 对外 status
REAL_STATUSES = {
    "success": "success",
    "quality_rejected": "quality_rejected",
    "skipped_small_file": "skipped",
    "skipped_low_hevc": "skipped",
    "skipped_hdr": "skipped",
    "skipped_low_bitrate": "skipped",
    "failed": "failed",
}


@pytest.mark.parametrize("internal,external", REAL_STATUSES.items())
def test_every_real_status_maps_as_documented(internal, external):
    assert json_status_for(internal) == external


def test_quality_rejected_is_not_failed():
    """🔴 素材结论不许说成机器故障。合并这两类 = 机器故障永不重试。"""
    assert json_status_for("quality_rejected") != json_status_for("failed")


def test_quality_rejected_is_not_skipped():
    """🔴 也不许折进 skipped。vcat P2-44 明令「质量拒绝 / 测量不可信 / 跳过」
    三种结论分开，且不许用 failed 描述素材结论（措辞本身是红线）。"""
    assert json_status_for("quality_rejected") != json_status_for("skipped_hdr")


def test_the_four_classes_are_actually_four():
    assert len({json_status_for(s) for s in REAL_STATUSES}) == 4


@pytest.mark.parametrize("unknown", ["", "weird", "SUCCESS", "Skipped", "quality-rejected", "skip"])
def test_unknown_status_degrades_to_failed_not_to_a_terminal_state(unknown):
    """🔴 未知状态必须落到 **非终态** 的 failed，让工人删残片重做。

    落进任何终态（success / skipped / quality_rejected）都等于**替引擎编造
    一个它没说过的结论**，而那条路上盘里可能正躺着一个残片。
    注意 `"SUCCESS"` / `"Skipped"` 大小写不同即视为未知——不做模糊匹配。
    """
    assert json_status_for(unknown) == "failed"


def test_mutation_folding_quality_rejected_back_into_skipped_is_caught():
    """变异验证：把 quality_rejected 折回 skipped（即老版行为），本套件必须抓到。"""
    def mutated(status):
        if status == "success":
            return "success"
        if status.startswith("skipped") or status == "quality_rejected":
            return "skipped"
        return "failed"

    assert mutated("quality_rejected") == mutated("skipped_hdr")      # 变异体自己不报错
    assert json_status_for("quality_rejected") != json_status_for("skipped_hdr")


def test_mutation_the_original_dead_branch_is_caught():
    """变异验证：还原那个恒假的嵌套判断，quality_rejected 会掉进 failed。"""
    def mutated(status):
        if status == "success":
            return "success"
        if status.startswith("skipped"):
            return "failed" if status == "failed" else "skipped"   # 恒假分支
        return "failed"

    assert mutated("quality_rejected") == "failed"                   # 老版的错
    assert json_status_for("quality_rejected") == "quality_rejected"
