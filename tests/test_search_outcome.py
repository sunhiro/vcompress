"""`ab-av1 crf-search` 的三种收场：找到 / 闸门拒了 / 工具跑挂了。

🔴 这不是「错误处理」的洁癖，是红线 §2 的落地。三种收场里前两种都不产出 CRF，
但**一种是素材结论（终态），一种是机器故障（该重试）**：

    找到     → dict                    → success
    闸门拒了 → None                    → quality_rejected  ← 素材结论，别记成 failed
    跑挂了   → raise AbAv1SearchError  → failed            ← 机器故障，别记成素材差

老版两种都 `return None`，于是「NAS 上核显没接上 / ffmpeg 少个滤镜」被写成
`quality_rejected`，detail 还写着「未过质量门限(VMAF<93.0)」——**一条把机器故障
说成素材差的假结论**，而且 `quality_rejected` 是终态、下游永不重试。

分界线是 ab-av1 自己给的（源码 `src/command/crf_search.rs`）：`--stdout-format json`
下只有 `Error::NoGoodCrf` 会往 **stdout** 打一行
``{"type":"crf-search-error","message":"Failed to find a suitable crf"}``
（那个 `println!` 挂在 `if let Error::NoGoodCrf { .. }` 里面），其余故障只写 stderr。
``message`` 的字面量取自 ab-av1 自己的单测 `error_json_message`。
"""

from __future__ import annotations

import json
import subprocess

import pytest

import vcompress_abav1 as engine

#: ab-av1 闸门拒斥那一行的原样（字面量见其源码单测 error_json_message）
GATE_LINE = json.dumps({"type": "crf-search-error", "message": "Failed to find a suitable crf"})

DONE_LINE = json.dumps({"crf": 26.0, "vmaf": 94.1, "type": "crf-search-done"})


class _Completed:
    returncode = 0

    def __init__(self, stdout):
        self.stdout = stdout
        self.stderr = ""


def _stub(monkeypatch, *, code=0, stdout="", stderr=""):
    def fake_run(cmd, *a, **k):
        if code != 0:
            raise subprocess.CalledProcessError(code, cmd, output=stdout, stderr=stderr)
        return _Completed(stdout)
    monkeypatch.setattr(engine.subprocess, "run", fake_run)


def _search():
    return engine.run_ab_av1_search("in.MOV", "vt", min_vmaf=93.0, max_percent=85.0)


def test_search_asks_ab_av1_for_json(monkeypatch):
    """🔴 整条判据挂在这个开关上：没有 `--stdout-format json`，闸门拒斥那一行
    根本不会打出来，于是**每一次闸门拒绝都会被读成机器故障**——终态变非终态，
    工人对着一批本来就不值得压的素材反复重做。"""
    captured = {}

    def fake_run(cmd, *a, **k):
        captured["cmd"] = list(cmd)
        return _Completed(DONE_LINE)

    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    _search()

    cmd = captured["cmd"]
    assert cmd[cmd.index("--stdout-format") + 1] == "json"


def test_found_crf_returns_the_dict(monkeypatch):
    _stub(monkeypatch, stdout=f"{DONE_LINE}\n")
    assert _search()["crf"] == 26.0


def test_done_record_wins_over_the_midway_sample_records(monkeypatch):
    """🔴 ab-av1 中途往 stdout 打的 `sample-encode-done` **也带 `crf`**，却没有
    `vmaf` / `predicted_encode_*`。认错了就会把 VMAF 记成 0、节省记成 0%——
    台账上看起来只是「压得不好」，而不是「读错了」。

    ⚠ 这里的行序是**故意反着放的**：把中途记录排在 done 之后。真机上 done 一定
    在最后（`Update::Done` 是最后一个 stdout 写入），所以「倒着找第一个带 crf 的」
    今天也能蒙对——这个测试要锁的不是「今天的结果」，而是**「认 type 而不是靠位置」**
    这条性质本身。靠位置的写法在这里必须翻红。
    """
    midway = json.dumps({"crf": 31.0, "type": "sample-encode-done",
                         "predicted_encode_size": 999, "vmaf": None})
    _stub(monkeypatch, stdout=f"{DONE_LINE}\n{midway}\n")

    got = _search()

    assert got["type"] == "crf-search-done"
    assert got["crf"] == 26.0
    assert got["vmaf"] == 94.1


def test_gate_rejection_returns_none(monkeypatch):
    """闸门拒了（VMAF 不达标 **或** 省不够，ab-av1 里都是 NoGoodCrf）→ None。"""
    _stub(monkeypatch, code=1, stdout=f"{GATE_LINE}\n", stderr="Error: Failed to find a suitable crf\n")
    assert _search() is None


def test_tool_crash_raises_instead_of_pretending_the_material_is_bad(monkeypatch):
    """🔴 核心一条：ffmpeg 崩了 / 核显没接上，**不许**伪装成 `quality_rejected`。"""
    _stub(monkeypatch, code=1, stdout="",
          stderr="Error: ffmpeg failed: No such filter: 'libvmaf'\n")
    with pytest.raises(engine.AbAv1SearchError):
        _search()


def test_unparseable_success_is_a_tool_fault_not_a_gate_rejection(monkeypatch):
    """退出码 0 却读不出结果行 —— 同样是机器/工具故障，不是「素材不行」。"""
    _stub(monkeypatch, code=0, stdout="not json at all\n")
    with pytest.raises(engine.AbAv1SearchError):
        _search()


def test_search_decodes_output_leniently(monkeypatch):
    """🔴 读 stdout/stderr 必须用**不严**的编码。

    `text=True` 默认 `errors='strict'`，撞上非 UTF-8 字节（indicatif 进度条、
    老素材的警告文案）会抛 `UnicodeDecodeError`，而它在老版是 `except Exception`
    → `return None` → 「素材不行」。**一条已经成功的搜索或一次已经成立的闸门结论，
    不该被一段无关字节翻案。** 现在是 `errors='replace'`：怪异字节变替换符，
    ASCII 的 JSON 行照样解析（若真读不出，也只会落到「工具故障」那一类）。
    """
    seen = {}

    def fake_run(cmd, *a, **k):
        seen.update(k)
        return _Completed(DONE_LINE)

    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    _search()

    assert seen.get("errors") == "replace"


def test_ab_av1_missing_entirely_is_a_tool_fault(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("ab-av1")
    monkeypatch.setattr(engine.subprocess, "run", boom)
    with pytest.raises(engine.AbAv1SearchError):
        _search()


# ------------------------------------------------------------------ 落到 status 上

def _args(**over):
    """`--preview` 在位的参数集：过闸门后不写文件，测试只关心 status。"""
    import types

    base = dict(
        min_file_mb=0.0, min_bitrate=0.0, max_hevc_bitrate=30.0,
        encoder="vt", vmaf_min=93.0, min_saving_pct=15.0,
        sample_sec=10, samples=3, vmaf_threads=6, vmaf_scale_1080p=True,
        preset=None, preview=True,
        skip_hdr=False, allow_bitdepth_downgrade=False, no_chroma_downgrade=False,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


@pytest.fixture
def driven(monkeypatch, tmp_path):
    """把 `process_file_abav1` 走到第 7 步（ab-av1 搜索）所需的一切都换成桩。"""
    src = tmp_path / "IMG_0001.MOV"
    src.write_bytes(b"x" * 4096)
    monkeypatch.setattr(engine, "get_video_metadata",
                        lambda p: ("h264", 720, 60.0, 5_000_000, "yuv420p"))
    return src, tmp_path / "IMG_0001.hevc.MOV", _args()


def test_gate_rejection_becomes_quality_rejected(driven, monkeypatch):
    src, out, args = driven
    monkeypatch.setattr(engine, "run_ab_av1_search", lambda *a, **k: None)

    status, *_rest, decision, _pix = engine.process_file_abav1(src, out, args)

    assert status == "quality_rejected"
    assert engine.json_status_for(status) == "quality_rejected"


def test_tool_crash_becomes_failed_not_quality_rejected(driven, monkeypatch):
    """🔴 这条如果翻红：机器故障又开始伪装成素材结论了（红线 §2）。"""
    src, out, args = driven

    def boom(*a, **k):
        raise engine.AbAv1SearchError("ab-av1 退出码 1，且 stdout 没有闸门拒斥标记")

    monkeypatch.setattr(engine, "run_ab_av1_search", boom)

    status, *_rest, decision, _pix = engine.process_file_abav1(src, out, args)

    assert status == "failed", f"工具故障被记成了 {status!r}（{decision}）"
    assert status != "quality_rejected"
    assert "工具故障" in decision
    assert engine.json_status_for(status) == "failed"


def test_both_gates_are_named_in_the_rejection_detail(driven, monkeypatch):
    """ab-av1 的 NoGoodCrf 同时覆盖「VMAF 不达标」和「省不够」。

    只写 VMAF 会在后一半上说假话 —— 台账里那句话是要被人读的。
    """
    src, out, args = driven
    monkeypatch.setattr(engine, "run_ab_av1_search", lambda *a, **k: None)

    *_head, decision, _pix = engine.process_file_abav1(src, out, args)

    assert f"VMAF<{args.vmaf_min}" in decision
    assert str(args.min_saving_pct) in decision


# ------------------------------------------------------------------ 红线 §1 前置守卫

def test_refuses_to_encode_when_output_path_is_the_source(tmp_path, monkeypatch):
    """🔴 红线 §1：`-i x.mp4 -o x.mp4` 这种误用必须在**动手之前**被拒。

    单文件模式下 `out_path` 就是 `-o` 原样。全量编码的 ffmpeg 带 `-y`，会**一边读
    一边截断同一个文件**——原片当场报废且不报错。老代码只有一个**事后**的
    `samefile` 守卫（避免删掉那个文件），那时原片已经被覆盖过了。

    注意断言里连 `run_ab_av1_search` 都不许被调用：拦晚了就等于白跑一次二分。
    """
    src = tmp_path / "IMG_0001.MOV"
    src.write_bytes(b"x" * 4096)

    def _never(*a, **k):
        raise AssertionError("守卫没拦住，已经动手了")

    monkeypatch.setattr(engine, "get_video_metadata", _never)
    monkeypatch.setattr(engine, "run_ab_av1_search", _never)
    monkeypatch.setattr(engine, "run_ab_av1_encode", _never)
    monkeypatch.setattr(engine, "preserve_metadata", _never)

    status, *_rest, decision, _pix = engine.process_file_abav1(src, src, _args())

    assert status == "failed", "不许记成素材结论"
    assert "拒绝覆写" in decision
    assert src.read_bytes() == b"x" * 4096, "源片被动过了"


def test_guard_does_not_fire_on_a_normal_output_path(tmp_path, monkeypatch):
    """正常布局（产物是另一个文件）不许被这条守卫误伤。"""
    src = tmp_path / "IMG_0001.MOV"
    src.write_bytes(b"x" * 4096)
    out = tmp_path / "IMG_0001.hevc.MOV"

    monkeypatch.setattr(engine, "get_video_metadata",
                        lambda p: ("h264", 720, 60.0, 5_000_000, "yuv420p"))
    monkeypatch.setattr(engine, "run_ab_av1_search", lambda *a, **k: None)

    status, *_rest = engine.process_file_abav1(src, out, _args())

    assert status == "quality_rejected"      # 正常走到了闸门那一步


def test_guard_does_not_fire_on_an_already_existing_product(tmp_path, monkeypatch):
    """🔴 **产物已经存在**时也不许误伤——而且这条真跑得到。

    `--preview` 会绕过断点续压（判据里带 `and not args.preview`），对一棵压过的树
    跑预览是标准两趟跑法的第一趟：那时 `out_path` 就是上一轮的产物、**确实存在**。
    守卫若写成「存在就拒」，一次 preview 会把整棵树报成 `failed`。
    所以判据必须是 `samefile`，不是 `exists`。
    """
    src = tmp_path / "IMG_0001.MOV"
    src.write_bytes(b"x" * 4096)
    out = tmp_path / "IMG_0001.hevc.MOV"
    out.write_bytes(b"previous run's product" * 10)   # 上一轮的产物，另一个文件

    monkeypatch.setattr(engine, "get_video_metadata",
                        lambda p: ("h264", 720, 60.0, 5_000_000, "yuv420p"))
    monkeypatch.setattr(engine, "run_ab_av1_search", lambda *a, **k: None)

    status, *_rest = engine.process_file_abav1(src, out, _args())

    assert status == "quality_rejected", "上一轮的产物被这条守卫误伤了"
