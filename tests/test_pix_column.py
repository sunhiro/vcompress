"""台账第 8 列「像素格式」（对应 ADR-019 决策 2/3、ADR-027、vcat P2-57/P2-59）。

ADR-019 当年买下的那笔交易是：**色度/位深降级默认放行，但一律记录**。
放行照做了，记录没做——引擎只写源片那一半。2026-09-03 实测查出 46 个
`yuvj422p` 源片的产物变成 `yuv420p`（色度减半 + 范围重映射），**账上一个字
都看不出来**，而它们正卡在「退役那 70.7 GB 原片」这一步的路上。

ADR-019「代价」栏原文预言过这件事，预言应验了，对价没交付。这里锁住对价。
"""

from __future__ import annotations

import pytest
from vcompress_abav1 import pix_column


def test_records_the_transition_not_just_one_side():
    """真机上那 46 个的形状。"""
    assert pix_column("yuvj422p", "yuv420p") == "yuvj422p->yuv420p"


def test_no_change_is_still_recorded_as_a_transition():
    """10-bit 那 128 个的形状——位深保住了，但那是 ffmpeg 替 videotoolbox
    协商对了，**巧合的安全不是设计的安全**。记下来才看得出是巧合。"""
    assert pix_column("yuv420p10le", "yuv420p10le") == "yuv420p10le->yuv420p10le"


def test_unprobeable_product_writes_a_question_mark():
    """🔴 探不出来必须写 `源->?`。回落成单值和「测了，没变化」长得一模一样，
    账上就分不出「没变」还是「没测出来」——那正是 ADR-027 要消灭的半条记录。"""
    assert pix_column("yuv420p", "") == "yuv420p->?"
    assert pix_column("yuv420p", None) == "yuv420p->?"


def test_unprobeable_product_is_not_confusable_with_no_change():
    assert pix_column("yuv420p", "") != pix_column("yuv420p", "yuv420p")


def test_degenerates_only_when_the_source_is_unknown_too():
    """源片也探不出来时无从记变迁（这条路上 process_file_abav1 早就返回了）。"""
    assert pix_column("", "yuv420p") == "yuv420p"
    assert pix_column("", "") == ""
    assert pix_column(None, None) == ""


@pytest.mark.xfail(reason="vcompress.py 缺陷 2 未修：清洗该在 probe_pix_fmt 里做", strict=True)
@pytest.mark.parametrize("src,out", [
    # 🔴 真实威胁：新版 ffprobe 的 `-of csv=p=0` 会多吐一个尾随逗号（`yuv420p,`）。
    # 生产引擎走 `-of json` 免疫，但 `vcompress.py` 那一路真被咬过（缺陷 2）：
    # 数据行被挤成 9 列，`export_report` 按位取 r[7]，把 `->yuv420p` 静默丢掉。
    ("yuv420p,", "yuv420p"), ("yuv420p", "yuv420p,"), ("yuvj422p,", "yuv420p,"),
])
def test_a_comma_polluted_probe_result_must_not_reach_the_column(src, out):
    """探测结果带逗号时，这一列绝不能把逗号原样传下去。

    ⚠ **当前是失败的**——`pix_column` 只做拼接，不清洗。清洗的正确位置在
    探测函数（`probe_pix_fmt` 取第一个非空 token），那是 vcompress.py 缺陷 2
    的修法，还没做。这条 xfail 就是那件事的锚：修好当天它会 XPASS 提醒摘掉标记。
    """
    assert "," not in pix_column(src, out)


def test_mutation_falling_back_to_a_single_value_is_caught():
    """变异验证：探测失败时退化成源片单值（P2-59 打回的那条）。"""
    def mutated(src, out):
        return f"{src}->{out}" if src and out else (src or out or "")

    assert mutated("yuv420p", "") == "yuv420p"                    # 与「没测」无法区分
    assert pix_column("yuv420p", "") == "yuv420p->?"
