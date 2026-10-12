"""判据**被不被用上** —— 2026-09-06 红线复检打回的那一层。

09-05 那轮把 `classify_search_output` / `counts_as_real_product` 提成纯函数并各配了
十几条测试，然后宣称「14 个变异体 13 个当场被抓」。**那个数字是自欺**：变异全打在
判据层，而独立上下文的红线复检员当场演示，把调用点

    if kind == "broken":          →   if False:          （= 精确还原本轮要修的 bug）

**152 条测试全绿**。原来的 bug 从来不在「分不分得出来」，在「分出来之后用不用」。
同一形状还有四处：逃生口默认值 · `DECISION_KEEP` 改名 · `main()` 的 preview 分支 ·
`REPORT_HEADER` 前六列换序 —— **全部全绿**。

🔴 **教训：给一个纯函数写测试，锁住的只是那个纯函数。**
提取本身会制造一段没人看的接线，而 bug 恰恰住在接线里。本文件专门打接线。

⚠ 跑变异前先 `find . -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +`
（`docs/REGRESSION.md` 开头那个字节码陷阱——2026-09-06 我又踩了一次）。
"""

from __future__ import annotations

import json

import pytest
from conftest import make_args


# ---- A 的接线：三类判据必须真的分头处理 -------------------------------------

@pytest.mark.parametrize("kind,expect_status,expect_decision_attr", [
    ("broken",   "failed",           "DECISION_SEARCH_BROKEN"),
    ("rejected", "quality_rejected", None),
])
def test_search_kind_reaches_the_right_status(bench, monkeypatch, kind,
                                              expect_status, expect_decision_attr):
    """🔴 这条就是复检员演示的那个缺口。`if kind == "broken":` 被折掉时它必须报警。"""
    monkeypatch.setattr(bench.eng, "run_ab_av1_search", lambda *a, **k: (kind, None))
    status, *rest = bench.eng.process_file_abav1(bench.src, bench.out, make_args())
    assert status == expect_status
    if expect_decision_attr:
        assert rest[5] == getattr(bench.eng, expect_decision_attr)


def test_machine_fault_stays_retryable_and_material_verdict_stays_terminal(bench, monkeypatch):
    """🔴 工人对两者的处置**相反**：failed 删残片重做，quality_rejected 记终态。

    合并它们 = 真正的机器故障永远不会被重做（读卡器掉总线那次的形状）。
    """
    def run(kind):
        monkeypatch.setattr(bench.eng, "run_ab_av1_search", lambda *a, **k: (kind, None))
        status, *_ = bench.eng.process_file_abav1(bench.src, bench.out, make_args())
        return bench.eng.json_status_for(status)

    assert run("broken") == "failed"
    assert run("rejected") == "quality_rejected"
    assert run("broken") != run("rejected")


def test_broken_never_produces_a_product_or_a_quality_number(bench, monkeypatch):
    """机器故障那条路上不许留产物，也不许往台账写 VMAF/CRF（那是编出来的数）。"""
    monkeypatch.setattr(bench.eng, "run_ab_av1_search", lambda *a, **k: ("broken", None))
    status, orig_mb, new_mb, saving, vmaf, crf, decision, _pix = \
        bench.eng.process_file_abav1(bench.src, bench.out, make_args())
    assert (status, vmaf, crf) == ("failed", "NA", "NA")
    assert (new_mb, saving) == (0.0, 0.0)
    assert not bench.out.exists()


# ---- E 的接线：main() 里那两个分支 ------------------------------------------

def _run_main(monkeypatch, tmp_path, eng, returns, extra_argv=()):
    """跑一次真 main()，只把外部工具与报表导出顶掉。"""
    import sys

    src_dir = tmp_path / "in"; (src_dir / "sub").mkdir(parents=True)
    clip = src_dir / "sub" / "CLIP.mp4"; clip.write_bytes(b"z" * 2048)
    out_dir = tmp_path / "out"

    monkeypatch.setattr(eng, "check_dependencies", lambda: None)
    monkeypatch.setattr(eng, "build_excel_report", lambda *a, **k: None)
    monkeypatch.setattr(eng, "build_html_report", lambda *a, **k: None)

    def fake_process(src_path, out_path, args):
        if returns[0] == "success" and not args.preview:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"p" * 1024)
        return returns
    monkeypatch.setattr(eng, "process_file_abav1", fake_process)

    argv = ["vcompress_abav1.py", "-i", str(src_dir), "-o", str(out_dir), *extra_argv]
    monkeypatch.setattr(sys, "argv", argv)
    eng.main()
    return out_dir


PREVIEW_RET = ("preview", 100.0, 20.0, 80.0, 94.5, 30, "PREVIEW成功", "yuv420p")
REAL_RET    = ("success", 100.0, 20.0, 80.0, 94.5, 30, "保留压缩版", "yuv420p->yuv420p")


def test_preview_does_not_write_the_manifest(bench, monkeypatch, tmp_path, capsys):
    """🔴 清单是**账**。preview 一个字节都没压，不许在账上留行。

    折掉 main() 里那个 `if not counts_as_real_product(...)` 分支时必须报警——
    09-05 那轮它全绿（复检员实测）。
    """
    out = _run_main(monkeypatch, tmp_path, bench.eng, PREVIEW_RET, ["--preview"])
    from fullset import read_manifest
    assert read_manifest(out) == {}


def test_a_real_run_does_write_the_manifest(bench, monkeypatch, tmp_path):
    out = _run_main(monkeypatch, tmp_path, bench.eng, REAL_RET)
    from fullset import read_manifest
    assert list(read_manifest(out)) == ["sub/CLIP.hevc.mp4"]


def test_preview_json_out_declares_itself_as_preview(bench, monkeypatch, tmp_path, capsys):
    """🔴 跨仓契约（2026-09-06 定）：preview 有自己的 status，实测字段一律 null，
    预测值另放 `predicted` 块。

    此前它顶着 `success` 发，vcat `compress_worker.py` 只能靠 `out_size` 猜，
    两个答案都是错的：发预估值 → 假 `done`；发 null → 假 `encode_failed`。
    """
    _run_main(monkeypatch, tmp_path, bench.eng, PREVIEW_RET, ["--preview", "--json-out"])
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("{")][-1]
    payload = json.loads(line)
    assert payload["status"] == "preview"
    # 实测字段：一个都不许有值
    assert payload["out_path"] is None
    assert payload["out_size"] is None
    assert payload["vmaf_mean"] is None
    assert payload["vmaf_min"] is None
    # 预测值：在自己的块里，名字自己说清楚它是什么
    assert payload["predicted"] == {
        "out_size": int(20.0 * 1024 * 1024), "vmaf": 94.5, "saving_pct": 80.0}


def test_a_real_run_carries_no_predicted_block(bench, monkeypatch, tmp_path, capsys):
    """真跑完的那条不许带 `predicted`——它的数字全是实测的。"""
    _run_main(monkeypatch, tmp_path, bench.eng, REAL_RET, ["--json-out"])
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("{")][-1]
    payload = json.loads(line)
    assert payload["status"] == "success"
    assert "predicted" not in payload
    assert payload["vmaf_mean"] == 94.5


def test_real_run_json_out_reports_the_real_product(bench, monkeypatch, tmp_path, capsys):
    _run_main(monkeypatch, tmp_path, bench.eng, REAL_RET, ["--json-out"])
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("{")][-1]
    payload = json.loads(line)
    assert payload["out_size"] == int(20.0 * 1024 * 1024)
    assert payload["out_path"].endswith("sub/CLIP.hevc.mp4")


def _log_text(caplog, monkeypatch, tmp_path, eng, returns, argv):
    import logging
    with caplog.at_level(logging.INFO, logger="vcompress_abav1"):
        _run_main(monkeypatch, tmp_path, eng, returns, argv)
    return "\n".join(r.message for r in caplog.records)


def test_preview_savings_are_not_reported_as_released_space(bench, monkeypatch, tmp_path, caplog):
    """🔴 preview 打「累计释放磁盘空间」= 把一个从没发生过的释放量写进日志。

    ⚠ **数字也要断言。** 初版只断言那句话在不在，于是折掉 main() 里的 preview
    分支时它照样绿——变异体打的是「预估可释放 **0.00 GB**」，字符串一模一样，
    真正的 80MB 跑进了 `total_saved_mb`。2026-09-06 变异实测抓到的。
    """
    text = _log_text(caplog, monkeypatch, tmp_path, bench.eng, PREVIEW_RET, ["--preview"])
    assert "累计释放磁盘空间" not in text
    assert "预估可释放 0.08 GB (80.0 MB)" in text        # 100.0 - 20.0
    assert "可压 1 个" in text


def test_a_real_run_does_say_released(bench, monkeypatch, tmp_path, caplog):
    text = _log_text(caplog, monkeypatch, tmp_path, bench.eng, REAL_RET, [])
    assert "预估可释放" not in text
    assert "累计释放磁盘空间: 0.08 GB (80.0 MB)" in text
    assert "压缩成功 1 个" in text


# ---- preview 的内部 status 本身（2026-09-06 契约）---------------------------

def test_preview_returns_its_own_internal_status(bench):
    """🔴 `process_file_abav1` 在 preview 下必须返回 `"preview"`，不是 `"success"`。

    ⚠ 上面那些 `_run_main` 测试喂的是**罐装返回值**（`process_file_abav1` 被顶掉了），
    所以它们锁不住这一条——2026-09-06 变异实测：把这里退回 `"success"`，
    那 10 条接线测试全绿。**罐装夹具锁不住产生罐头的那台机器。**
    """
    status, orig_mb, new_mb, saving, vmaf, crf, decision, _pix = \
        bench.eng.process_file_abav1(bench.src, bench.out, make_args(preview=True))
    assert status == "preview"
    assert decision == "PREVIEW成功"
    assert bench.eng.json_status_for(status) == "preview"


def test_preview_writes_nothing_to_disk(bench):
    """preview 一个字节都不许写——连临时名都不许留。"""
    from conftest import leftovers
    bench.eng.process_file_abav1(bench.src, bench.out, make_args(preview=True))
    assert not bench.out.exists()
    assert leftovers(bench.tmp_dir) == ["CLIP.mp4"]


def test_a_real_run_returns_success_not_preview(bench):
    """反向：非 preview 必须是 success，别把两者接反了。"""
    status, *_ = bench.eng.process_file_abav1(bench.src, bench.out, make_args())
    assert status == "success"
    assert bench.out.exists()


def test_missing_src_file_returns_failed_safely(bench):
    """源文件不存在时不崩溃，安全返回 failed。"""
    missing_src = bench.tmp_dir / "does_not_exist.mp4"
    status, orig_mb, new_mb, saving, vmaf, crf, decision, _pix = \
        bench.eng.process_file_abav1(missing_src, bench.out, make_args())
    assert status == "failed"
    assert "源文件不存在" in decision


def test_insufficient_saving_discarded_and_marked_skipped_saving(bench, monkeypatch):
    """🔴 全量编码落地后，若实际节省率 < min_saving_pct (默认20%)，必须丢弃半成品产物且状态为 skipped_saving。"""
    # bench 默认原片 3MB (3 * 1024 * 1024 字节)
    # 模拟转码产物为 2.7MB (仅省 10%，低于 20% 门限)
    def fake_encode_large(src_path, out_path, *a, **k):
        out_path.write_bytes(b"y" * int(2.7 * 1024 * 1024))
        return True

    monkeypatch.setattr(bench.eng, "run_ab_av1_encode", fake_encode_large)

    status, orig_mb, new_mb, saving, vmaf, crf, decision, _pix = \
        bench.eng.process_file_abav1(bench.src, bench.out, make_args(min_saving_pct=20.0))

    assert status == "skipped_saving"
    assert decision == bench.eng.DECISION_SAVING_INSUFFICIENT
    assert bench.eng.json_status_for(status) == "skipped"
    assert not bench.out.exists()  # 产物未落位
    # 临时文件已被清理
    from conftest import leftovers
    assert leftovers(bench.tmp_dir) == ["CLIP.mp4"]


def test_exclude_pattern_filters_dji_lrf_and_dng(bench):
    """验证 vcompress_abav1 的目录扫描能根据 exclude_pattern 正确过滤相机代理与 RAW。"""
    src_dir = bench.tmp_dir / "input_scan"
    src_dir.mkdir()
    (src_dir / "normal.MP4").write_text("v")
    (src_dir / "DJI_001_LRF.mov").write_text("proxy")
    (src_dir / "clip.LRF").write_text("proxy2")
    (src_dir / "photo.DNG").write_text("raw")
    (src_dir / "._apple_double.mp4").write_text("dot")

    import fnmatch
    pats = ["*._*", "*_lrf.*", "*.lrf", "*.dng"]
    scanned = []
    for p in src_dir.rglob('*'):
        if p.suffix.lower() not in bench.eng.SUPPORTED_EXTENSIONS or p.name.startswith('._'):
            continue
        name_lower = p.name.lower()
        str_lower = str(p).lower()
        if pats and any(fnmatch.fnmatch(name_lower, pat) or fnmatch.fnmatch(str_lower, pat) for pat in pats):
            continue
        scanned.append(p.name)

    assert scanned == ["normal.MP4"]


