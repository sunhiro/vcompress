"""红线 2「元数据无损」到底是不是一道闸门（本轮 2026-09-05 新修）。

老代码把 `preserve_metadata()` 的返回值**丢掉了**：exiftool 失败只打一条
`logger.error`，随后照样返回「保留压缩版」、照样记 success、照样进内容清单。
而生成的 `_apply_replace.py` 会据此**删掉源片**——那份 EXIF 就永久没了。
红线 2 声明的保证，实现上只是「尽力而为 + 一条日志」。

拍摄时间是家庭影像的命根子（`varchive` 那条主线的全部意义），所以这里的
取舍是「宁可这个文件本轮失败、原片原样留着」。逃生口 `--allow-metadata-loss`
与 `vcompress.py` 的 `--allow-bitdepth-downgrade` 同一个形状：**默认拒绝，
确需放行必须显式说出来。**

本文件不调 ffmpeg / ab-av1 / exiftool，全部用 monkeypatch 顶掉。
"""

from __future__ import annotations

import pytest
import vcompress_abav1 as eng
from conftest import leftovers, make_args as _args


# ---- 元数据成功：正常就位 ---------------------------------------------------

def test_success_places_the_product_and_leaves_no_partial(bench, monkeypatch):
    monkeypatch.setattr(eng, "preserve_metadata", lambda s, t: True)
    status, *_rest = eng.process_file_abav1(bench.src, bench.out, _args())
    assert status == "success"
    assert _rest[-1] == "yuv420p->yuv420p"                 # 第 8 列照记
    assert bench.out.exists()
    assert leftovers(bench.tmp_dir) == ["CLIP.hevc.mp4", "CLIP.mp4"]


# ---- 🔴 元数据失败：闸门必须拦住 --------------------------------------------

def test_metadata_failure_produces_no_product_at_all(bench, monkeypatch):
    """🔴 最终路径上必须什么都没有——有产物就意味着下游会去删源片。"""
    monkeypatch.setattr(eng, "preserve_metadata", lambda s, t: False)
    status, *_ = eng.process_file_abav1(bench.src, bench.out, _args())
    assert status == "failed"
    assert not bench.out.exists()


def test_metadata_failure_leaves_the_source_untouched(bench, monkeypatch):
    """红线 1：原片连一个字节都不许动。"""
    before = bench.src.read_bytes()
    monkeypatch.setattr(eng, "preserve_metadata", lambda s, t: False)
    eng.process_file_abav1(bench.src, bench.out, _args())
    assert bench.src.read_bytes() == before


def test_metadata_failure_cleans_up_the_partial(bench, monkeypatch):
    """半成品也不许留——否则下一轮续压守卫会把它当成压好的产物。"""
    monkeypatch.setattr(eng, "preserve_metadata", lambda s, t: False)
    eng.process_file_abav1(bench.src, bench.out, _args())
    assert leftovers(bench.tmp_dir) == ["CLIP.mp4"]


def test_metadata_failure_is_retryable_not_a_material_verdict(bench, monkeypatch):
    """exiftool 挂了是**机器故障**，不是「这个素材不值得压」。"""
    monkeypatch.setattr(eng, "preserve_metadata", lambda s, t: False)
    status, *rest = eng.process_file_abav1(bench.src, bench.out, _args())
    assert eng.json_status_for(status) == "failed"
    assert rest[5] == eng.DECISION_METADATA_LOST
    assert rest[5] != eng.DECISION_KEEP                    # vcat 不会当成血缘收走


# ---- 逃生口：必须显式说出来 -------------------------------------------------

def test_the_escape_hatch_exists_and_is_opt_in(bench, monkeypatch):
    monkeypatch.setattr(eng, "preserve_metadata", lambda s, t: False)
    status, *_ = eng.process_file_abav1(bench.src, bench.out,
                                        _args(allow_metadata_loss=True))
    assert status == "success"
    assert bench.out.exists()


def test_the_escape_hatch_is_off_in_the_real_parser(monkeypatch):
    """🔴 默认拒绝。改成默认放行，红线 2 的闸门就只剩一条日志了。

    ⚠ 老版这条测试**自建了一个 argparse 再断言它的默认值**——`store_true` 的
    默认永远是 False，那个断言**物理上不可能失败**，且从没碰过 `main()` 的 parser。
    2026-09-06 实测：把生产 parser 改成 `default=True` → 152 条全绿。
    这正是 d0f295e 打回过的「写死一份副本跟自己核对」，我又犯了一次。
    现在打的是真 parser。
    """
    seen = {}
    monkeypatch.setattr(eng, "check_dependencies", lambda: None)
    monkeypatch.setattr(eng, "process_file_abav1",
                        lambda *a, **k: (_ for _ in ()).throw(SystemExit(0)))

    def capture(parser_self, *a, **k):
        raise AssertionError("不该走到这里")

    # 直接调 main() 的 argparse：用一个必然停在参数解析之后的最小调用
    import sys
    argv = ["vcompress_abav1.py", "-i", "/nonexistent-input", "-o", "/tmp/x.mp4"]
    monkeypatch.setattr(sys, "argv", argv)

    real_parse = eng.argparse.ArgumentParser.parse_args

    def spy(self, *a, **k):
        ns = real_parse(self, *a, **k)
        seen["allow_metadata_loss"] = ns.allow_metadata_loss
        raise SystemExit(0)          # 参数解析完就停，不真跑批
    monkeypatch.setattr(eng.argparse.ArgumentParser, "parse_args", spy)

    with pytest.raises(SystemExit):
        eng.main()
    assert seen["allow_metadata_loss"] is False, "红线 2 的逃生口默认开着"


# ---- 变异验证 ---------------------------------------------------------------

def test_mutation_discarding_the_return_value_is_caught(bench, monkeypatch):
    """还原老行为（丢弃 preserve_metadata 的返回值），本套件必须当场抓住。"""
    calls = []
    monkeypatch.setattr(eng, "preserve_metadata",
                        lambda s, t: calls.append(1) or False)
    status, *_ = eng.process_file_abav1(bench.src, bench.out, _args())
    assert calls, "preserve_metadata 根本没被调用，这条测试就是假的"
    assert status == "failed", "返回值被丢弃 → 元数据没了却记成功"
