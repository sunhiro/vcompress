#!/usr/bin/env python3
"""sync_udisk_deletions.py — 将本地已删除/整理的改动，反向安全同步删除 U 盘 / 相机 SD 卡上的对应文件。

背景与定位：
1. 吸收自一代目 `9.attic/video-compress-gen1/sync_deletions.py` 的成熟实战逻辑；
2. 配合大疆（DJI Osmo Nano/Action/Pocket）及相机卡工作流：
   用户将卡内素材备份到本地后，在本地精简、删片或挑选后，需要一键清理 U 盘释放硬件卡容量；
3. 严格安全守卫：
   - 默认 `--dry-run` 预览模式，绝不直接删盘；
   - 保护相机系统与缩略图文件（默认不触碰 `.lrf`, `.thm`, `.scr`, `.db` 等，避免损坏相机机内文件系统索引）；
   - 大小写不敏感与后缀扩展名鲁棒匹配（兼容 `.MP4`/`.mp4` 以及带 `.hevc` 产物标记等识别）；
   - 必须显式 `--apply` 并经二次确认才执行物理删除。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 保证从当前 src 目录加载模块
sys.path.insert(0, str(Path(__file__).resolve().parent))
from naming import marked_name

# 相机关键元数据与特殊缓存文件，反向删除时默认必须跳过保护
CAMERA_PROTECTED_EXTS = {'.thm', '.scr', '.db', '.dat', '.ctg'}
VIDEO_EXTS = {
    '.mp4', '.mov', '.m4v', '.avi', '.mkv', '.mts', '.m2ts',
    '.3gp', '.wmv', '.flv', '.webm', '.vob', '.mpg', '.rmvb'
}


def scan_udisk_candidates(
    udisk_dir: Path,
    local_dir: Path,
    protect_proxies: bool = True
) -> tuple[list[tuple[Path, int]], list[tuple[Path, int]], int]:
    """扫描 U 盘中在本地已不存在的候选文件。
    返回: (待删除列表, 保护跳过列表, 扫描总文件数)
    """
    to_delete = []
    skipped_protected = []
    total_files = 0

    for root, dirs, files in os.walk(udisk_dir):
        # 跳过系统隐藏目录
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {".fseventsd", ".Spotlight-V100", ".Trashes"}]

        for f in files:
            if f.startswith("."):
                continue

            total_files += 1
            udisk_file = Path(root) / f
            ext_lower = udisk_file.suffix.lower()

            # 1. 保护相机缩略图与索引文件
            if ext_lower in CAMERA_PROTECTED_EXTS:
                skipped_protected.append((udisk_file, udisk_file.stat().st_size))
                continue

            # 2. 如果开启代理保护（例如保留机内 LRF 给机器回放或传输），跳过
            is_lrf = (ext_lower == '.lrf') or udisk_file.stem.upper().endswith('_LRF')
            if protect_proxies and is_lrf:
                skipped_protected.append((udisk_file, udisk_file.stat().st_size))
                continue

            try:
                rel_path = udisk_file.relative_to(udisk_dir)
            except ValueError:
                continue

            # 3. 检查本地是否存在：
            # 兼容多种常见的目录层级偏差 (如是否包含 DCIM 顶级目录)
            base_rel_candidates = [rel_path]
            if (local_dir / "DCIM").is_dir() and (not rel_path.parts or rel_path.parts[0].upper() != "DCIM"):
                base_rel_candidates.append(Path("DCIM") / rel_path)
            if rel_path.parts and rel_path.parts[0].upper() == "DCIM":
                base_rel_candidates.append(Path(*rel_path.parts[1:]))

            exists_locally = False
            for b_rel in base_rel_candidates:
                local_target = local_dir / b_rel
                check_list = [
                    local_target,
                    local_target.with_suffix(local_target.suffix.lower()),
                    local_target.with_suffix(local_target.suffix.upper()),
                ]
                if ext_lower in VIDEO_EXTS:
                    # 兼容二代 .hevc 产物标记、大小写与转码 mp4 命名
                    marked_p = local_dir / marked_name(b_rel)
                    check_list.extend([
                        marked_p,
                        marked_p.with_suffix(marked_p.suffix.lower()),
                        marked_p.with_suffix(marked_p.suffix.upper()),
                        local_target.with_name(f"{local_target.stem}.hevc.mp4"),
                        local_target.with_name(f"{local_target.stem}.hevc.MP4"),
                        local_target.with_suffix(".mp4"),
                        local_target.with_suffix(".MP4"),
                    ])

                for cand in check_list:
                    if cand.exists():
                        exists_locally = True
                        break
                if exists_locally:
                    break

            if not exists_locally:
                try:
                    f_size = udisk_file.stat().st_size
                except OSError:
                    f_size = 0
                to_delete.append((udisk_file, f_size))

    return to_delete, skipped_protected, total_files


def apply_deletions(to_delete: list[tuple[Path, int]]) -> tuple[int, int, list[str]]:
    """真正物理删除 U 盘上指定的文件。
    返回: (成功删除数, 释放字节数, 错误信息列表)
    """
    deleted_cnt = 0
    deleted_bytes = 0
    errors = []

    for fpath, fsize in to_delete:
        try:
            fpath.unlink()
            deleted_cnt += 1
            deleted_bytes += fsize
        except Exception as e:
            errors.append(f"{fpath}: {e}")

    return deleted_cnt, deleted_bytes, errors


def main():
    parser = argparse.ArgumentParser(
        description="sync_udisk_deletions - 将本地已删片的结果，反向安全同步删除 U 盘/相机卡上的对应文件"
    )
    parser.add_argument("-u", "--udisk-dir", type=Path, default=Path("/Volumes/OsmoNano/DCIM"),
                        help="目标 U 盘/相机目录 (默认: /Volumes/OsmoNano/DCIM)")
    parser.add_argument("-l", "--local-dir", type=Path, required=True,
                        help="本地基准目录 (以本地为准：本地已经删掉的，U 盘上也相应删除)")
    parser.add_argument("--purge-lrf", action="store_true",
                        help="是否一并删除 .LRF 预览代理文件 (默认保留机内 LRF 供设备回放)")
    parser.add_argument("--apply", action="store_true",
                        help="真正执行删除 (🔴 默认 Dry-Run 预演模式，不碰磁盘)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过交互式二次确认")

    args = parser.parse_args()

    if not args.udisk_dir.exists():
        print(f"❌ 错误: U 盘/卡目录未挂载或不存在: {args.udisk_dir}")
        sys.exit(1)

    if not args.local_dir.exists():
        print(f"❌ 错误: 本地基准目录不存在: {args.local_dir}")
        sys.exit(1)

    local_files_count = sum(1 for dp, dn, fn in os.walk(args.local_dir) for f in fn if not f.startswith('.'))
    if local_files_count == 0:
        print(f"❌ 安全熔断拦截: 本地基准目录 {args.local_dir} 内没有任何文件！")
        print("   为防止误将整张相机卡/U 盘清空，已强制拒绝执行。请检查 --local-dir 路径是否正确。")
        sys.exit(1)

    print("=" * 70)
    print(" 🧹 U 盘/相机卡 反向同步删除预演 (U-Disk Deletion Sync)")
    print("=" * 70)
    print(f" 本地基准目录 (Master): {args.local_dir}")
    print(f" U 盘目标目录 (Target): {args.udisk_dir}")
    print(f" LRF 代理策略:         [{'一并清除' if args.purge_lrf else '受保护保留(不删)'}]")
    print("-" * 70)

    to_delete, skipped_prot, total_scanned = scan_udisk_candidates(
        args.udisk_dir,
        args.local_dir,
        protect_proxies=not args.purge_lrf
    )

    del_bytes = sum(x[1] for x in to_delete)
    del_mb = del_bytes / (1024 * 1024)
    del_gb = del_bytes / (1024 * 1024 * 1024)

    print(f" 扫描 U 盘总文件数: {total_scanned} 个")
    print(f" 相机系统保护保留: {len(skipped_prot)} 个 (THM/SCR/DB/LRF 等)")
    print(f" 待清理孤立文件:   {len(to_delete)} 个 | 预计可释放空间: {del_mb:.1f} MB ({del_gb:.2f} GB)")
    print("=" * 70)

    if not to_delete:
        print("\n✅ U 盘与本地完全一致，没有需要删除的文件。")
        return

    # 展示前 10 个示例
    print("\n待删除示例 (前 10 个):")
    for fpath, sz in to_delete[:10]:
        try:
            rel = fpath.relative_to(args.udisk_dir)
        except ValueError:
            rel = fpath.name
        print(f"   🗑️  {rel} ({sz / (1024 * 1024):.2f} MB)")
    if len(to_delete) > 10:
        print(f"   ... 以及其余 {len(to_delete) - 10} 个文件")

    if not args.apply:
        print("\n💡 当前处于 [Dry-Run 预演模式]，U 盘未发生任何物理改变。")
        print("   若要真正删除 U 盘上这些已在本地废弃的文件，请加上 `--apply`，例如:")
        print(f"   python3 src/sync_udisk_deletions.py -u \"{args.udisk_dir}\" -l \"{args.local_dir}\" --apply\n")
        return

    if not args.yes:
        confirm = input(f"\n⚠️  警告: 即将永久删除 U 盘上的 {len(to_delete)} 个文件！确定继续吗？(输入 yes 确认): ").strip().lower()
        if confirm != "yes":
            print("❌ 操作已取消，U 盘未做任何删除。")
            return

    print(f"\n🚀 开始执行 U 盘文件清理...")
    ok_cnt, freed_bytes, errors = apply_deletions(to_delete)
    print(f"✅ 清理完成: 成功删除 {ok_cnt} 个文件，释放 {freed_bytes / (1024 * 1024 * 1024):.2f} GB 空间。")
    if errors:
        print(f"⚠️ 遇到 {len(errors)} 个删除失败项:")
        for err in errors[:10]:
            print(f"   - {err}")


if __name__ == '__main__':
    main()
