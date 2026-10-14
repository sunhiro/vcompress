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

# `vcompress_abav1` 在 **import 期**就 logging.basicConfig 一个 FileHandler，
# 落点由 `VCOMPRESS_LOG_DIR` 决定（默认 `~/.cache/vcompress/`）。这里把它指到
# 临时目录，日志随临时目录一起消失，仓库和 CWD 都不落东西。
# （早先靠 `os.chdir(_probe)` 再 import 的偏方已废：CWD 是全局状态，测试期间翻来
# 翻去本身也会污染别的路径断言。）
_probe = tempfile.mkdtemp(prefix="vcompress-tests-")
os.environ["VCOMPRESS_LOG_DIR"] = _probe
import vcompress_abav1  # noqa: E402  仅为把 import 期副作用圈在临时目录里
