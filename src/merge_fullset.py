#!/usr/bin/env python3
"""merge_fullset.py — 将「压缩产物」与「未压缩原版」安全合并为完整自包含的「全集目录」。

设计原则（遵从 ADR-020 与 fullset.py）：
1. 产物目录（输出目录）包含所有带 `.hevc` 标记的优质压缩视频；
2. 本脚本将源目录中「未被压缩的文件」（包括跳过的小文件、极低码率文件、未达标视频以及家庭照片/文档）
   安全并入目标目录，使其成为一份自包含、可单独拷走的完整全集；
3. 源目录中那些已经被成功压缩的原版视频将原样保留在源目录，充当原始冷备份；
4. 默认必须是 --dry-run 预览模式（零写盘、绝对不触碰任何真实文件）；
5. 只有显式传入 --apply 时才执行（同盘默认使用秒级原子 rename，带 macOS uchg 锁定恢复保护）。
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import stat
import sys
from pathlib import Path

# 保证从当前 src 目录加载模块
sys.path.insert(0, str(Path(__file__).resolve().parent))
from naming import is_marked, unmarked_name

SKIP_FILENAMES = {
    "_manifest.csv", "_report.csv", "_report.xlsx", "_report.html",
    ".DS_Store", ".Spotlight-V100", ".Trashes", "Thumbs.db"
}

# 默认排除的相机临时/代理/超大RAW模式（如大疆 .LRF 预览代理、.DNG RAW、AppleDouble残渣）
DEFAULT_EXCLUDE_PATTERNS = {'*._*', '*_lrf.*', '*.lrf', '*.dng'}

VIDEO_EXTS = {
    '.mp4', '.mov', '.m4v', '.avi', '.mkv', '.mts', '.m2ts',
    '.3gp', '.wmv', '.flv', '.webm', '.vob', '.mpg', '.rmvb', '.rm'
}


def scan_tree(root: Path) -> dict[str, int]:
    """扫描目录下的真实普通文件，返回 {相对路径: 字节大小}。
    跳过隐藏点文件（如 ._* AppleDouble）及台账文件。
    """
    res = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith(".")]
        for f in fn:
            if f.startswith(".") or f in SKIP_FILENAMES:
                continue
            full_p = Path(dp) / f
            try:
                rel = str(full_p.relative_to(root))
                res[rel] = full_p.stat().st_size
            except (OSError, ValueError):
                continue
    return res


def plan_merge(
    src_dir: Path,
    dst_dir: Path,
    videos_only: bool = False,
    min_saving_pct: float = 20.0,
    exclude_patterns: set[str] | list[str] | None = None,
    exclude_exts: set[str] | list[str] | None = None
) -> dict:
    """生成全集合并计划，返回分析与待处理清单。只出计划，不修改任何文件。"""
    src_files = scan_tree(src_dir)
    dst_files = scan_tree(dst_dir)

    patterns = set()
    if exclude_patterns:
        patterns.update(p.strip().lower() for p in exclude_patterns if p.strip())
    if exclude_exts:
        for e in exclude_exts:
            e = e.strip().lower()
            if not e:
                continue
            patterns.add(f"*{e}" if e.startswith('.') else f"*.{e}")

    # 找出目标目录里所有已压缩的产物（带 .hevc 标记），并逆向映射回原文件名
    # 🔴 质量守卫：如果压缩产物实际节省率 < min_saving_pct（如 <20% 或负增长），
    # 则不视为合格有效产物，提示用户并保留源目录原版（将其作为 untouched_videos 并入全集）
    compressed_products = {}  # {原相对路径: 产物相对路径}
    inefficient_products = []  # [(产物路径, 原片路径, 节省率)]

    for rel_dst, dst_sz in dst_files.items():
        if is_marked(rel_dst):
            try:
                orig_rel = unmarked_name(rel_dst)
                if orig_rel in src_files:
                    orig_sz = src_files[orig_rel]
                    saving_pct = round(((orig_sz - dst_sz) / orig_sz) * 100, 1) if orig_sz > 0 else 0.0
                    if saving_pct < min_saving_pct:
                        inefficient_products.append((rel_dst, orig_rel, saving_pct))
                        continue
                compressed_products[orig_rel] = rel_dst
            except ValueError:
                pass

    # 待并入的文件分类
    untouched_videos: list[tuple[str, int]] = []
    non_videos: list[tuple[str, int]] = []
    already_in_dst: list[tuple[str, int]] = []
    excluded_items: list[tuple[str, int]] = []
    conflicts: list[str] = []

    for rel_src, size in src_files.items():
        # 如果这个文件在目标目录已经有对应的压缩产物，则不需要搬运（留在源目录当冷备份）
        if rel_src in compressed_products:
            continue

        p_src = Path(rel_src)
        name_lower = p_src.name.lower()
        rel_lower = str(rel_src).lower()
        if patterns and any(fnmatch.fnmatch(name_lower, pat) or fnmatch.fnmatch(rel_lower, pat) for pat in patterns):
            excluded_items.append((rel_src, size))
            continue

        # 检查是否已在目标目录存在同名文件
        if rel_src in dst_files:
            already_in_dst.append((rel_src, size))
            continue

        is_video = p_src.suffix.lower() in VIDEO_EXTS

        if is_video:
            untouched_videos.append((rel_src, size))
        else:
            if not videos_only:
                non_videos.append((rel_src, size))

    plan = {
        "compressed_products_count": len(compressed_products),
        "compressed_products_bytes": sum(dst_files[p] for p in compressed_products.values() if p in dst_files),
        "source_cold_backup_count": len(compressed_products),
        "source_cold_backup_bytes": sum(src_files[o] for o in compressed_products if o in src_files),
        "inefficient_products": inefficient_products,
        "untouched_videos": untouched_videos,
        "non_videos": non_videos,
        "already_in_dst": already_in_dst,
        "excluded_items": excluded_items,
        "conflicts": conflicts,
    }
    return plan


def apply_merge(
    src_dir: Path,
    dst_dir: Path,
    items_to_transfer: list[tuple[str, int]],
    mode: str = "move"
) -> tuple[int, int, list[str]]:
    """真正执行合并操作。
    mode='move': 同分区原子 rename（带 uchg 保护）；
    mode='copy': 跨文件系统安全复制（保留元数据）。
    返回: (成功数, 解锁恢复数, 失败错误列表)
    """
    success_cnt = 0
    relocked_cnt = 0
    errors = []

    for rel, _ in items_to_transfer:
        s = src_dir / rel
        d = dst_dir / rel
        d.parent.mkdir(parents=True, exist_ok=True)

        try:
            if mode == "move":
                # macOS uchg 标志处理：若有只读锁定，临时摘掉，搬过去后再锁回
                flags = getattr(os.lstat(s), "st_flags", 0)
                immutable = bool(flags & getattr(stat, "UF_IMMUTABLE", 0))
                if immutable:
                    os.chflags(s, flags & ~stat.UF_IMMUTABLE)

                os.rename(s, d)

                if immutable:
                    os.chflags(d, flags)
                    relocked_cnt += 1
            elif mode == "copy":
                shutil.copy2(s, d)
            else:
                raise ValueError(f"不支持的模式: {mode}")

            success_cnt += 1
        except Exception as e:
            errors.append(f"{rel}: {e}")

    return success_cnt, relocked_cnt, errors


def print_plan_report(plan: dict, src_dir: Path, dst_dir: Path, mode: str):
    """格式化打印合并计划汇报卡片。"""
    c_count = plan["compressed_products_count"]
    c_mb = plan["compressed_products_bytes"] / (1024 * 1024)

    b_count = plan["source_cold_backup_count"]
    b_mb = plan["source_cold_backup_bytes"] / (1024 * 1024)

    uv_items = plan["untouched_videos"]
    uv_count = len(uv_items)
    uv_mb = sum(x[1] for x in uv_items) / (1024 * 1024)

    nv_items = plan["non_videos"]
    nv_count = len(nv_items)
    nv_mb = sum(x[1] for x in nv_items) / (1024 * 1024)

    to_merge_count = uv_count + nv_count
    to_merge_mb = uv_mb + nv_mb

    fullset_final_count = c_count + to_merge_count + len(plan["already_in_dst"])
    fullset_final_mb = c_mb + to_merge_mb

    print("\n" + "=" * 70)
    print(" 📦 全集目录合并预演分析报告 (Fullset Merge Plan)")
    print("=" * 70)
    print(f" 源目录:   {src_dir}")
    print(f" 全集目录: {dst_dir}")
    print(f" 运作模式: [{'同盘秒级原子移动' if mode == 'move' else '全量数据复制'}] ({mode.upper()})")
    print("-" * 70)
    print(" 1. 现状资产构成:")
    print(f"    • 已成功压缩产物:   {c_count:>5} 个视频 | 占用 {c_mb/1024:>7.2f} GB (目标目录下)")
    print(f"    • 对应原片(冷备份): {b_count:>5} 个视频 | 原始 {b_mb/1024:>7.2f} GB (保留在源目录)")
    if b_mb > 0:
        saved_gb = (b_mb - c_mb) / 1024
        print(f"      ➔ 这部分已纯释放空间: {saved_gb:.2f} GB (节省率 {(saved_gb / (b_mb/1024))*100:.1f}%)")
    print("-" * 70)
    print(" 2. 待并入全集目录的原版文件:")
    print(f"    • 未压缩原版视频:   {uv_count:>5} 个视频 | 占用 {uv_mb/1024:>7.2f} GB (小文件/极低码率/保护放弃)")
    print(f"    • 家庭照片与非视频: {nv_count:>5} 个文件 | 占用 {nv_mb/1024:>7.2f} GB (JPG/PNG/文档/字幕等)")
    print(f"    • 待并入总计:       {to_merge_count:>5} 个文件 | 占用 {to_merge_mb/1024:>7.2f} GB")
    print("-" * 70)
    print(" 3. 合并后预期结果:")
    print(f"    🌟 [全集目录] 最终总计: {fullset_final_count} 个文件 (约 {fullset_final_mb/1024:.2f} GB)")
    print("       (包含全部优质压缩新片 + 未压缩原片 + 全部照片/文档，完全自包含、随时可独立拷走)")
    print(f"    🛡️  [源目录]   最终留存: {b_count} 个视频 (约 {b_mb/1024:.2f} GB)")
    print("       (仅保留已经被优质压缩替代的原片，作为无损冷备份，不再掺杂任何未压片)")
    print("=" * 70)

    if plan.get("excluded_items"):
        ex_cnt = len(plan["excluded_items"])
        ex_mb = sum(x[1] for x in plan["excluded_items"]) / (1024 * 1024)
        print(f"\n🚫 [过滤跳过] 依规则排除文件: {ex_cnt} 个 (约 {ex_mb:.1f} MB，如 .LRF 代理或 .DNG RAW，原样保留在源端)")

    if plan.get("inefficient_products"):
        print(f"\n⚠️  提示: 发现 {len(plan['inefficient_products'])} 个实际节省率不足门限的低效产物 (已自动保底保留原片):")
        for prod, orig, saving in plan["inefficient_products"][:5]:
            print(f"   - {orig} (产物省 {saving}%)")

    if plan["conflicts"]:
        print(f"\n⚠️  警告: 发现 {len(plan['conflicts'])} 处命名冲突，请检查:")
        for cf in plan["conflicts"][:5]:
            print(f"   - {cf}")


def main():
    parser = argparse.ArgumentParser(
        description="merge_fullset - 将「压缩产物」与「未压缩原版」安全合并为完整自包含的「全集目录」"
    )
    parser.add_argument("-s", "--src-dir", type=Path, default=Path("/Volumes/T7/9.home"),
                        help="源视频目录 (默认: /Volumes/T7/9.home)")
    parser.add_argument("-d", "--dst-dir", type=Path, default=Path("/Volumes/T7/9.home-压缩后"),
                        help="输出全集目录 (默认: /Volumes/T7/9.home-压缩后)")
    parser.add_argument("--mode", choices=["move", "copy"], default="move",
                        help="合并方式 (move=同盘秒级移动/源目录只留备份; copy=复制一份)")
    parser.add_argument("--min-saving-pct", type=float, default=20.0,
                        help="最低空间节省率门限 (默认 20.0%%，低于此值的产物不视为替代)")
    parser.add_argument("--exclude-pattern", type=str, default="*._*,*_LRF.*,*.lrf,*.dng",
                        help="合并时排除的文件模式 (glob)，逗号分隔 (默认: '*._*,*_LRF.*,*.lrf,*.dng'，留空可禁用)")
    parser.add_argument("--exclude-exts", type=str, default="",
                        help="兼容选项：按扩展名排除，逗号分隔 (如 'lrf,dng')")
    parser.add_argument("--videos-only", action="store_true",
                        help="仅合并未压缩视频，忽略非视频文件 (默认会将照片文档等全量并入全集)")
    parser.add_argument("--apply", action="store_true",
                        help="真正执行合并操作 (🔴 默认不加此参数时仅进行 Dry-run 预演分析，绝不动盘)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过交互式二次确认")

    args = parser.parse_args()

    pats = [p.strip() for p in args.exclude_pattern.split(',') if p.strip()] if args.exclude_pattern else []
    exts = [e.strip() for e in args.exclude_exts.split(',') if e.strip()] if args.exclude_exts else []

    if not args.src_dir.is_dir():
        print(f"❌ 错误: 源目录不存在: {args.src_dir}")
        sys.exit(1)
    if not args.dst_dir.is_dir():
        print(f"❌ 错误: 目标全集目录不存在: {args.dst_dir}")
        sys.exit(1)

    # 1. 计划与预演分析 (Dry-run)
    plan = plan_merge(
        args.src_dir,
        args.dst_dir,
        videos_only=args.videos_only,
        min_saving_pct=args.min_saving_pct,
        exclude_patterns=pats,
        exclude_exts=exts,
    )
    print_plan_report(plan, args.src_dir, args.dst_dir, args.mode)

    items_to_transfer = plan["untouched_videos"] + plan["non_videos"]

    if not args.apply:
        print("\n💡 当前处于 [Dry-Run 预演模式]，磁盘未发生任何改变。")
        print("   若要真正执行，请附加 `--apply` 参数，例如:")
        print(f"   python3 src/merge_fullset.py -s \"{args.src_dir}\" -d \"{args.dst_dir}\" --mode {args.mode} --apply\n")
        return

    if not items_to_transfer:
        print("\n✅ 没有需要合并的文件，全集目录已是最新状态。")
        return

    # 2. 真实执行保护确认
    if not args.yes:
        confirm = input(f"\n⚠️  确定要真正执行合并 ({args.mode.upper()}) 吗？(输入 yes 确认): ").strip().lower()
        if confirm != "yes":
            print("❌ 操作已取消，磁盘未做任何修改。")
            return

    print(f"\n🚀 开始执行合并 ({len(items_to_transfer)} 个文件)...")
    ok_cnt, relocked_cnt, errors = apply_merge(args.src_dir, args.dst_dir, items_to_transfer, mode=args.mode)

    print(f"✅ 执行完成: 成功合并 {ok_cnt} 个文件" + (f" (恢复了 {relocked_cnt} 个锁定文件属性)" if relocked_cnt else ""))
    if errors:
        print(f"⚠️ 遇到 {len(errors)} 个错误:")
        for err in errors[:10]:
            print(f"   - {err}")


if __name__ == "__main__":
    main()
