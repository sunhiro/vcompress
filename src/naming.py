"""压缩产物的命名与输出布局规则（对应 vcat ADR-020）。

# 输出目录是「全集」，不是「产物堆」

老布局：输出目录只放压缩成功的产物。实测下来这份东西**单独拷走没有意义**——
真机上 3630 个源文件只有 214 个值得压，输出目录 5.1GB，既不能顶替原目录用，
也不是一份完整的东西，只是一堆碎片。

新布局：输出目录是**全集**。
    输出目录/  a/b/IMG_7161.hevc.MOV   ← 压缩产物，带标记
               a/b/其它视频.mp4         ← 不值得压的，原封不动搬过来
    源目录/    a/b/IMG_7161.MOV        ← 只留被压过的那批源片，当备份

这样输出目录可以整个拷走、整个管理，源目录退化成「被压掉那些的原片备份」。

# 为什么产物要带 `.hevc` 标记

1. 全集里混着压缩的和没压缩的，不标记就只能逐个 ffprobe 才分得出。
2. **防静默覆盖**——这条更要命。老版 `vcompress.py` 用 `.with_suffix('.mp4')`
   强制转名，源 `A.MOV` 的产物写成 `A.mp4`；全集布局下同目录若有个未压缩的
   `A.mp4` 搬进来，两者正好同名，后到的把先到的覆盖掉，不报任何错。

# 为什么不把码率写进文件名

码率是浮点数，写进文件名必然取整；取整之后就不准了，而**一个不准的数字挂在
文件名上比不写更糟**——它会被当成准确值引用。真值 ffprobe 一读就有，
详细参数在 `_manifest.csv` 里按内容哈希记着。

🔴 这套规则必须与 vcat 的 `vcat/compress_worker.py` 逐字一致（那边是单一出处，
   有 `tests/test_compress_naming.py` 锁着）。两边走偏，血缘配对会**静默错位**：
   产物连到一个碰巧同名的无关原片上，账面看不出任何异常。
"""

from __future__ import annotations

from pathlib import Path

COMPRESSED_MARKER = ".hevc"


def marked_name(rel: str | Path) -> str:
    """IMG_7161.MOV → IMG_7161.hevc.MOV（容器后缀沿用源，只插标记）。"""
    p = Path(rel)
    return str(p.with_name(f"{p.stem}{COMPRESSED_MARKER}{p.suffix}"))


def is_marked(rel: str | Path) -> bool:
    p = Path(rel)
    # 源片没有扩展名时（X → X.hevc），标记落在 suffix 位而不是 stem 尾
    return p.suffix == COMPRESSED_MARKER or p.stem.endswith(COMPRESSED_MARKER)


def unmarked_name(rel: str | Path) -> str:
    """从产物名反查源片名，`marked_name` 的严格逆运算。

    配不上就抛异常，绝不返回一个「差不多」的名字——悄悄返回原名会让血缘登记
    把产物连到它自己，或连到一个碰巧同名的无关文件（红线：宁可拒绝，不可错）。
    """
    p = Path(rel)
    if p.suffix == COMPRESSED_MARKER:        # 源片本来就没有扩展名
        return str(p.with_name(p.stem))
    if not p.stem.endswith(COMPRESSED_MARKER):
        raise ValueError(f"不是压缩产物名（缺 {COMPRESSED_MARKER} 标记）：{rel}")
    return str(p.with_name(f"{p.stem[: -len(COMPRESSED_MARKER)]}{p.suffix}"))
