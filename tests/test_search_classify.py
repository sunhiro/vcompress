"""ab-av1 搜索结果的三类判别：`found` / `rejected` / `broken`。

真机事故链（本轮 2026-09-05 新修）：老版 `run_ab_av1_search` 用
`subprocess.run(..., check=True)`，任何非零退出都抛 `CalledProcessError`，
调用方一律记 `quality_rejected`。而 vcat 的工人对 `quality_rejected` 是
**「素材结论、终态、不重试」**——于是 /tmp 满、ab-av1 没装、ffmpeg 崩、
读卡器过热掉 USB 总线（ACTIVE_MEMORY §4.5 真机出过），剩下整批都会被
永久写成「未过质量门限(VMAF<93)」，而且没有任何人会去重跑它们。

这是 S28 修复 #1 的同一形状，只是上移了一层：那次修的是
「`quality_rejected` 恒假、发不出去」，这次修的是**上游根本没把
「素材压不动」和「机器坏了」分开**。红线 2 的原话：结果枚举里没有笼统的 failed。

下面 STDOUT_* 三份夹具是 **ab-av1 0.11.5 真机实跑抓下来的原样输出**，
不是手写的形状。三种情形里有两种都 exit 1，判别器只在 stdout 里。
"""

from __future__ import annotations

import json

import pytest
from vcompress_abav1 import (SEARCH_DONE_TYPE, SEARCH_ERROR_TYPE,
                             classify_search_output)

# 情形 A：文件不存在（机器/环境故障）。exit 1，stdout 一行 JSON 都没有。
STDOUT_BROKEN = ""

# 情形 B：素材真的达不到目标（要求 VMAF 99.9 且省 90%）。exit 1。
# ⚠ 注意末行之前那行 `sample-encode-done` —— 它带 crf，vmaf 只有 90.96，
#   而且 predicted_encode_percent 是 156%（压完反而更大）。
STDOUT_REJECTED = (
    '{"crf":55.0,"from_cache":false,"predicted_encode_percent":156.00153027066662,'
    '"predicted_encode_seconds":1.0,"predicted_encode_size":1019101,'
    '"type":"sample-encode-done","vmaf":90.9628677368164}\n'
    '{"message":"Failed to find a suitable crf","type":"crf-search-error"}\n'
)

# 情形 C：正常搜到。exit 0。前 5 行是二分搜索路上的探测点，末行才是结论。
STDOUT_FOUND = (
    '{"crf":55.0,"from_cache":true,"predicted_encode_percent":156.00153027066662,'
    '"predicted_encode_seconds":1.0,"predicted_encode_size":1019101,'
    '"type":"sample-encode-done","vmaf":90.9628677368164}\n'
    '{"crf":28.0,"from_cache":false,"predicted_encode_percent":59.71107032413501,'
    '"predicted_encode_seconds":1.0,"predicted_encode_size":390070,'
    '"type":"sample-encode-done","vmaf":63.359130859375}\n'
    '{"crf":44.0,"from_cache":false,"predicted_encode_percent":98.67285454686227,'
    '"predicted_encode_seconds":1.0,"predicted_encode_size":644593,'
    '"type":"sample-encode-done","vmaf":83.24937438964844}\n'
    '{"crf":41.0,"from_cache":false,"predicted_encode_percent":88.51568299714441,'
    '"predicted_encode_seconds":2.0,"predicted_encode_size":578240,'
    '"type":"sample-encode-done","vmaf":81.30058288574219}\n'
    '{"crf":40.0,"from_cache":false,"predicted_encode_percent":85.75573054484012,'
    '"predicted_encode_seconds":1.0,"predicted_encode_size":560211,'
    '"type":"sample-encode-done","vmaf":79.45783996582031}\n'
    '{"crf":41.0,"from_cache":false,"predicted_encode_percent":88.51568299714441,'
    '"predicted_encode_seconds":2.0,"predicted_encode_size":578240,'
    '"type":"crf-search-done","vmaf":81.30058288574219}\n'
)


# ---- 三类各自认得出来 -------------------------------------------------------

def test_found_is_the_crf_search_done_line():
    kind, data = classify_search_output(0, STDOUT_FOUND)
    assert kind == "found"
    assert data["type"] == SEARCH_DONE_TYPE
    assert data["crf"] == 41.0


def test_rejected_needs_ab_av1_to_say_so_explicitly():
    assert classify_search_output(1, STDOUT_REJECTED) == ("rejected", None)


def test_broken_when_ab_av1_produced_no_conclusion_line():
    assert classify_search_output(1, STDOUT_BROKEN) == ("broken", None)


# ---- 🔴 本轮要修的那件事 ----------------------------------------------------

def test_machine_fault_and_material_verdict_are_not_the_same_class():
    """🔴 两者**都是 exit 1**，只能靠 stdout 分。合并 = 机器故障永不重试。"""
    broken, _ = classify_search_output(1, STDOUT_BROKEN)
    rejected, _ = classify_search_output(1, STDOUT_REJECTED)
    assert broken != rejected


@pytest.mark.parametrize("stdout", [
    "",
    "Error: No such file or directory (os error 2)\n",     # 真机 stderr 串进来
    "not json at all\n",
    '{"type":"sample-encode-done","crf":55.0,"vmaf":90.96}\n',   # 只有探测点，没结论
    '[1,2,3]\n',                                            # JSON 但不是对象
])
def test_anything_without_an_explicit_verdict_is_broken(stdout):
    """⚠ 拿不准一律 broken：白重跑一次 << 永久静默写错账且永不重试。"""
    assert classify_search_output(1, stdout)[0] == "broken"


# ---- 🔴 B：成功行的判据不许放宽到「带 crf 就算」 ----------------------------

def test_intermediate_probe_lines_are_never_mistaken_for_a_conclusion():
    """🔴 `sample-encode-done` 同样带 crf，但它**没过闸门**。

    真实数据里的探测点包括 vmaf 63.36、以及 156%（压完反而更大）那一档。
    认错一次的后果不是少省点空间，是**拿一个没过闸门的 CRF 去压全片，
    并把那个 VMAF 当成实测值写进台账**。
    """
    probes_only = "\n".join(
        l for l in STDOUT_FOUND.splitlines() if SEARCH_DONE_TYPE not in l
    )
    assert SEARCH_ERROR_TYPE not in probes_only
    assert classify_search_output(0, probes_only)[0] == "broken"


def test_a_trailing_extra_line_does_not_shift_the_answer():
    """老版靠「crf-search-done 恰好排在最后」才碰巧对。ab-av1 多打一行就翻车。"""
    noisy = STDOUT_FOUND + '{"type":"sample-encode-done","crf":9.0,"vmaf":99.0}\n'
    kind, data = classify_search_output(0, noisy)
    assert kind == "found"
    assert data["crf"] == 41.0          # 仍是结论行，不是那条追加的探测点


def test_nonzero_exit_never_yields_found_even_with_a_done_line():
    """退出码非零 = ab-av1 自己不认为跑成了，那就不下结论（保守）。"""
    assert classify_search_output(1, STDOUT_FOUND)[0] == "broken"


# ---- 变异验证：把它改回老行为，本套件必须当场抓住 --------------------------

def _mutant_old_reverse_scan(returncode, stdout):
    """老版：check=True 抛掉一切非零退出 + 反向扫第一个带 crf 的行。"""
    if returncode != 0:
        return "rejected", None                      # ← 老版的两合一
    for line in reversed([l for l in stdout.splitlines() if l.strip()]):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if "crf" in data:                            # ← 老版过松的判据
            return "found", data
    return "broken", None


def test_mutation_old_behaviour_calls_a_machine_fault_a_material_verdict():
    assert _mutant_old_reverse_scan(1, STDOUT_BROKEN)[0] == "rejected"    # 老版的错
    assert classify_search_output(1, STDOUT_BROKEN)[0] == "broken"


def test_mutation_old_loose_predicate_picks_an_unqualified_probe():
    """把 check=True 去掉、判据却没收紧，会当场挑中那个 156% 的探测点。"""
    kind, data = _mutant_old_reverse_scan(0, STDOUT_REJECTED)
    assert (kind, data["crf"], data["type"]) == ("found", 55.0, "sample-encode-done")
    assert data["predicted_encode_percent"] > 100     # 压完反而更大，还被当成了结论
    assert classify_search_output(0, STDOUT_REJECTED)[0] == "rejected"


def test_mutation_dropping_the_returncode_guard_is_caught():
    def mutated(returncode, stdout):
        return classify_search_output(0, stdout)      # 无视真实退出码
    assert mutated(1, STDOUT_FOUND)[0] == "found"     # 变异体自己不报错
    assert classify_search_output(1, STDOUT_FOUND)[0] == "broken"
