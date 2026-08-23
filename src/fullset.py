"""全集输出：内容清单（`_manifest.csv`）+ 把未压缩的源片搬进输出目录。

# 为什么要换掉 `_report.csv`

老的 `_report.csv` 是**单次运行的日志**：每跑一次整个重写。真机上因此漏记了
66 个产物——08-03 那次跑完写了 148 行，08-04 又压了 66 个，那次的报表没落盘，
盘上 214 个产物、报表只认 148 个，谁都没发现。

`_manifest.csv` 是**这棵树的账**：一行一个产物，键是两侧的 full_hash，
续跑是 upsert 不是重写。哈希在这里是关键——路径是观测值不是身份，
人随手改个名，靠路径记的账就对不上了，靠内容哈希记的还认得出来。

# 搬运（--move-untouched）

🔴 **默认关闭，而且开了也要先自己确认别处还有副本。**

在「输出目录当全集用、源目录当备份」这个模式下，未压缩的文件是**移动**过去的
（同分区 rename，秒级，不搬数据）。移动本身不减少任何一份——但前提是
**这些文件在别的设备上还有副本**。如果源目录里放的是孤本，搬完之后
「源目录是备份」这句话就不成立了：孤本只是换了个位置，一份还是一份。

本模块只做机械搬运，**判断「别处还有没有」是调用方的责任**。vcat 用户可以用
`vcat status --cold` 看这台设备上的孤本数；不用 vcat 的人得自己心里有数。
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import stat
from pathlib import Path

from naming import is_marked

# 哈希列写的是 **blake3 全量**，与 vcat 台账的 full_hash 同一个算法同一个口径
# （vcat/hashing.py）。装了 blake3 就算，没装就留空。
#
# 🔴 绝不用 sha256 之类顶替：那会产生一列「看着像、和台账对不上」的哈希，
#    比留空危险得多——留空一眼就知道没有，写错的值会被人当真去比对。
#    留空的行由 vcat 侧 `contrib/register_hevc_lineage.py` 登记血缘时补齐
#    （它本来就要实测两侧全量哈希）。
try:
    from blake3 import blake3 as _blake3
except ImportError:                                          # pragma: no cover
    _blake3 = None

#: blake3 在不在。缺席时哈希列整列为空——**这不是小事，调用方必须说出来**：
#: 没有哈希的清单只剩路径，而路径是观测值不是身份，改个名这本账就对不上了。
#: 更坏的是它看起来完整，容易被当成校验依据用（真机上就这么误用过一次：
#: 拿空值去比对，`if 记录值 and 不相等` 短路跳过，报了一轮「全部吻合」）。
HASHING_AVAILABLE = _blake3 is not None

MANIFEST_NAME = "_manifest.csv"
MANIFEST_FIELDS = [
    "产物相对路径", "源相对路径",
    "产物full_hash", "源full_hash",
    "产物字节", "源字节", "省%",
    "VMAF", "编码器", "工具", "登记时间",
]
# 走 walk 时跳过自家产物
SKIP_NAMES = {MANIFEST_NAME, "_report.csv", "_report.html", "_report.xlsx"}


def walk_rel(root: Path) -> list[str]:
    """真实文件的相对路径。跳过点开头的目录与文件（AppleDouble `._X`、`.vcat-trash` 等）。"""
    out: list[str] = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith(".")]
        for f in fn:
            if f.startswith(".") or f in SKIP_NAMES:
                continue
            out.append(os.path.relpath(os.path.join(dp, f), root))
    return sorted(out)


# ---- 内容清单 ---------------------------------------------------------------


def read_manifest(tree: Path) -> dict[str, dict]:
    path = tree / MANIFEST_NAME
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {r["产物相对路径"]: r for r in csv.DictReader(fh)}


def write_manifest(tree: Path, rows: dict[str, dict]) -> Path:
    """整份写出，但内容是调用方 upsert 后的全量——先写临时文件再 os.replace，
    避免跑一半断电留下半截清单（那比没有清单更坏，它看起来是完整的）。"""
    path = tree / MANIFEST_NAME
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        for k in sorted(rows):
            w.writerow({f: rows[k].get(f, "") for f in MANIFEST_FIELDS})
    os.replace(tmp, path)
    return path


def full_hash(path: Path) -> str:
    """全量 blake3，与 vcat 的 full_hash 同口径。blake3 缺席时返回空串。"""
    if _blake3 is None:
        return ""
    h = _blake3()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_row(*, dst_rel: str, src_rel: str, dst_bytes: int, src_bytes: int,
                 dst_hash: str = "", src_hash: str = "", vmaf=None,
                 encoder: str = "", tool: str = "") -> dict:
    return {
        "产物相对路径": dst_rel, "源相对路径": src_rel,
        "产物full_hash": dst_hash, "源full_hash": src_hash,
        "产物字节": dst_bytes, "源字节": src_bytes,
        "省%": f"{(1 - dst_bytes / src_bytes) * 100:.1f}" if src_bytes else "",
        "VMAF": "" if vmaf is None else vmaf,
        "编码器": encoder, "工具": tool,
        "登记时间": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }


# ---- 把未压缩的搬进输出目录 -------------------------------------------------


def plan_move_untouched(src_dir: Path, out_dir: Path) -> tuple[list[tuple[Path, Path]], list[str]]:
    """返回 (待移动列表, 问题清单)。**只出计划，不动盘。**

    「未压缩」= 源树里那些、在输出树里找不到对应产物的文件。判据是产物名去掉标记
    之后的相对路径，不是「两棵树路径一一对应」——加了标记之后本来就不对应了。
    """
    products = [r for r in walk_rel(out_dir) if is_marked(r)]
    from naming import unmarked_name

    problems: list[str] = []
    has_product: set[str] = set()
    for rel in products:
        try:
            has_product.add(unmarked_name(rel))
        except ValueError as e:
            problems.append(str(e))

    moves: list[tuple[Path, Path]] = []
    taken = set(walk_rel(out_dir))
    for rel in walk_rel(src_dir):
        if rel in has_product:
            continue                       # 它压过了，源片留在源目录当备份
        if rel in taken:
            problems.append(f"移入撞车：{rel} 在输出目录里已存在")
            continue
        moves.append((src_dir / rel, out_dir / rel))
    return moves, problems


def apply_moves(moves: list[tuple[Path, Path]]) -> tuple[int, int]:
    """执行搬运，返回 (成功数, 其中恢复了不可变标志的数量)。

    🔴 macOS 上带 `uchg`（Finder「已锁定」）的文件 rename 会 EPERM。真机上撞到过
    133 个微信导出视频全带这个标志。做法是**临时摘掉、落位后原样贴回**——
    文件属性一个位都不改，只换路径。绝不能顺手把用户的锁定状态抹掉。

    ⚠ 另注：`shutil.copystat` 会把 `st_flags` 一起复制，所以压缩产物可能从源片
    继承到 `uchg`。真机上就出现过一个，之后对产物做任何改名都会 EPERM。
    """
    ok = relocked = 0
    for s, d in moves:
        d.parent.mkdir(parents=True, exist_ok=True)
        flags = getattr(os.lstat(s), "st_flags", 0)
        immutable = bool(flags & getattr(stat, "UF_IMMUTABLE", 0))
        if immutable:
            os.chflags(s, flags & ~stat.UF_IMMUTABLE)
        os.rename(s, d)          # 同分区 rename：原子，不搬数据
        if immutable:
            os.chflags(d, flags)
            relocked += 1
        ok += 1
    return ok, relocked
