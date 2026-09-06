"""preview 不许把**预估**当**实测**记账（本轮 2026-09-05 新修）。

`--preview` 返回的内部 status 也是 `"success"`——它确实「成功地评估完了」——
但它一个字节都没写，`new_mb` 是 ab-av1 二分搜索的预测值。老代码把两种 success
累进同一个 `total_saved_mb`，于是收尾那句「累计释放磁盘空间: X GB」在 preview
模式下打的是一个**从没发生过的释放量**。而 skill 的标准两趟跑法第一趟正是
`--preview`，所以这个假数字每次评估都会出现，并且长得跟真账一模一样。

ACTIVE_MEMORY §5.5 的教训原话：那份运行日志里的数字「是我从运行日志算的，
不是台账」。同一个形状——**预估与实测不许共用一个口径**。
"""

from __future__ import annotations

import pytest
from vcompress_abav1 import (DECISION_KEEP, DECISION_METADATA_LOST,
                             DECISION_PLACE_FAILED, DECISION_SEARCH_BROKEN,
                             REPORT_HEADER, counts_as_real_product,
                             json_status_for)


def test_preview_success_is_not_a_real_product():
    """🔴 09-05 要修的那件事：preview 顶着 success 时也不算真产物。"""
    assert counts_as_real_product("success", preview=True) is False


def test_the_preview_status_alone_is_enough():
    """09-06 起 preview 有了自己的内部 status，单看它就够——
    `preview` 参数退化成纵深防御（两道判据同时失效才会出错）。"""
    assert counts_as_real_product("preview", preview=False) is False
    assert counts_as_real_product("preview", preview=True) is False


def test_a_real_run_success_is():
    assert counts_as_real_product("success", preview=False) is True


@pytest.mark.parametrize("status", [
    "preview", "quality_rejected", "failed", "skipped_small_file",
    "skipped_low_hevc", "skipped_hdr", "skipped_low_bitrate",
])
@pytest.mark.parametrize("preview", [True, False])
def test_nothing_but_success_ever_counts(status, preview):
    assert counts_as_real_product(status, preview) is False


def test_mutation_folding_preview_back_into_success_is_caught():
    """变异验证：还原老行为（只看 status），preview 会被当成真产物记账。"""
    def mutated(status, preview):
        return status == "success"

    assert mutated("success", preview=True) is True                 # 老版的错
    assert counts_as_real_product("success", preview=True) is False


# ---- 跨仓契约：失败类「决定」不许撞上 vcat 认的那一个 -----------------------

#: 🔴 全部从引擎 import，**不在这里抄副本**——抄一份写死的字符串去比对，
#: 等于自己跟自己核对，引擎哪天改了名这条测试照样绿（d0f295e 打回过两条
#: 这样的「名不副实的测试」）。
NEW_FAILURE_DECISIONS = [
    DECISION_SEARCH_BROKEN,     # A：ab-av1 自己没跑成
    DECISION_METADATA_LOST,     # D：红线 2 的闸门
    DECISION_PLACE_FAILED,      # C：原子 rename 没成
]


@pytest.mark.parametrize("decision", NEW_FAILURE_DECISIONS)
def test_failure_decisions_are_not_ingested_as_lineage(decision):
    """vcat 的 lineage.load_report 按第 7 列精确匹配 DECISION_KEEP 筛行。"""
    assert decision != DECISION_KEEP


def test_report_header_column_order_is_frozen():
    """🔴 只能加列不能改列序。老版只钉了 [6]/[7] 和长度——**前六列对调照样全绿**
    （2026-09-06 变异实测），而 vcat `contrib/backfill_compress_report.py:66` 是
    `rel, _orig_mb, _new_mb, ... = row[:7]` **按位解包**，换序即静默错位。
    所以这里钉全序，不是抽查。
    """
    assert REPORT_HEADER == ['文件', '原MB', '新MB', '省%', 'VMAF', 'CRF/Q', '决定', '像素格式']


def test_decision_keep_is_the_exact_cross_repo_literal():
    """🔴 `DECISION_KEEP` 的值本身就是跨仓契约，不是一个可以随便改的内部名字。

    vcat `lineage.load_report` 按它**精确匹配**筛可登记血缘的行；`export_report.py`
    十余处按它渲染；本仓 `prune_report_rows` 按它决定 preview 下删不删一行。
    2026-09-06 变异实测：改成「保留压缩版本」→ 152 条全绿，而后果是 vcat
    一条血缘都收不到、preview 反过来把成功行全删光。
    """
    assert DECISION_KEEP == "保留压缩版"
