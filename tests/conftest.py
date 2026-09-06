"""pytest 公共装置。

🔴 **本套件一个字节的真视频都不碰**，也不调 ffmpeg / ab-av1 / exiftool。
它锁的是「账怎么记」那一层的纯逻辑——2026-09-03 那轮跨两仓审计
（vcat KANBAN Epic 13）修掉的真缺陷全在这一层，而当时**没有任何东西在跑**，
四条全靠一个独立上下文的复检员读代码抓出来。

与 `docs/REGRESSION.md` 的 R1~R8 真机套件不重叠：
那边验「**测得准不准**」（要真视频、真 ffmpeg、真 NAS），
这边验「**记得对不对**」（纯函数，毫秒级，每次改完都该跑）。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# `vcompress_abav1` 在 **import 期**就 logging.basicConfig 一个
# FileHandler("vcompress_abav1.log")，落点是当时的 CWD。测试模块 import 它
# 的时机在收集阶段，CWD 还是仓库根 —— 会在仓库里拉出一个日志文件。
# 这里先在临时目录里把它 import 掉，日志随临时目录一起消失。
_probe = tempfile.mkdtemp(prefix="vcompress-tests-")
_cwd = os.getcwd()
try:
    os.chdir(_probe)
    import vcompress_abav1  # noqa: F401  仅为把 import 期副作用圈在临时目录里
finally:
    os.chdir(_cwd)


# ---- 让 process_file_abav1 能在没有 ffmpeg / ab-av1 / exiftool 的情况下跑完 ----
#
# 它本身是「搜索 → 编码 → 元数据 → 就位」四步的编排，每一步都在调外部工具。
# 把四个外部调用 monkeypatch 掉之后，**编排逻辑本身**（谁失败了要丢弃什么、
# 记哪个状态、最终路径上留不留东西）就是纯逻辑，毫秒级可测——而 2026-09-05
# 这轮修的四件事全在这一层。

from types import SimpleNamespace

import pytest


def make_args(**kw):
    """一份 process_file_abav1 认得的最小 args。"""
    base = dict(min_file_mb=1.0, max_hevc_bitrate=30.0, skip_hdr=False, min_bitrate=1.0,
                vmaf_min=93.0, min_saving_pct=20.0, sample_sec=4, samples=2,
                vmaf_threads=2, vmaf_scale_1080p=True, encoder="vt", preset=None,
                preview=False, allow_metadata_loss=False)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """「搜索过了、编码也成了」的场景，后面几步留给各测试自己摆布。

    `encode` 把产物写在**调用方给的那个路径**上——也就是临时名。测试据此
    检查「盘上最后剩下什么」，这正是残片问题的判据。
    """
    import vcompress_abav1 as eng

    src = tmp_path / "CLIP.mp4"
    src.write_bytes(b"x" * (3 * 1024 * 1024))
    out = tmp_path / "CLIP.hevc.mp4"

    monkeypatch.setattr(eng, "get_video_metadata",
                        lambda p: ("h264", 720, 20.0, 11_500_000, "yuv420p"))
    monkeypatch.setattr(eng, "run_ab_av1_search",
                        lambda *a, **k: ("found", {"crf": 30, "vmaf": 95.0,
                                                   "predicted_encode_percent": 40.0,
                                                   "predicted_encode_size": 1 << 20}))

    def fake_encode(src_path, out_path, *a, **k):
        out_path.write_bytes(b"y" * (1024 * 1024))
        return True
    monkeypatch.setattr(eng, "run_ab_av1_encode", fake_encode)
    monkeypatch.setattr(eng, "preserve_metadata", lambda s, t: True)
    return SimpleNamespace(src=src, out=out, tmp_dir=tmp_path, eng=eng)


def leftovers(d):
    """目录里最后剩下什么（含隐藏文件）。"""
    return sorted(p.name for p in d.iterdir())
