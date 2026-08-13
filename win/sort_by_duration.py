#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sort video files by duration and size into 1_Clips, 2_Records, and 3_Archives.
Designed for both Windows and Linux environments.
"""

import os
import sys
import re
import subprocess
import shutil
import csv
import argparse
from concurrent.futures import ThreadPoolExecutor

VIDEO_EXT = set("mp4 mov m4v avi mkv wmv flv mpg mpeg 3gp 3g2 ts m2ts mts webm vob ogv f4v asf divx m2v mxf rm rmvb".split())
IMAGE_EXT = set("jpg jpeg heic heif png dng tif tiff gif bmp webp cr2 cr3 nef arw raf rw2 orf sr2 aae".split())

CATS = {
    "clips": "1_Clips",
    "records": "2_Records",
    "archives": "3_Archives"
}

DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def ffdur(path, ffmpeg_bin):
    """Probe duration of a video file using ffmpeg."""
    try:
        p = subprocess.run([ffmpeg_bin, "-hide_banner", "-i", path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=90)
        m = DUR_RE.search(p.stderr.decode("utf-8", "replace"))
        if not m:
            return None
        h, mn, s = m.groups()
        return int(h) * 3600 + int(mn) * 60 + float(s)
    except Exception:
        return None


def is_ignored_dir(path, dest_path_set):
    """Check if the directory should be ignored during walk."""
    name = os.path.basename(path)
    if name == "#recycle" or name.startswith("@") or name.startswith("."):
        return True
    if os.path.abspath(path) in dest_path_set:
        return True
    return False


def has_sibling_image(path):
    """Check if the video has a sibling image file with the same base name."""
    d = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    try:
        for f in os.listdir(d):
            b, e = os.path.splitext(f)
            if b.lower() == stem and e[1:].lower() in IMAGE_EXT:
                return True
    except Exception:
        pass
    return False


def walk_videos(root, dest_paths):
    """Recursively walk through the directory and yield video file paths."""
    dest_path_set = {os.path.abspath(p) for p in dest_paths}
    for dp, dns, fns in os.walk(root):
        # Filter directories in-place to avoid traversing ignored folders
        dns[:] = [d for d in dns if not is_ignored_dir(os.path.join(dp, d), dest_path_set)]
        for fn in fns:
            if fn.startswith("._") or fn == ".DS_Store":
                continue
            ext = os.path.splitext(fn)[1][1:].lower()
            if ext in VIDEO_EXT:
                yield os.path.join(dp, fn)


def classify(dur, size, clips_dur, arch_dur, arch_size_bytes):
    """Classify video into clips, archives, or records based on duration and size."""
    if (dur is not None and dur >= arch_dur) or size >= arch_size_bytes:
        return "archives"
    if dur is None:
        return "unknown"
    if dur < clips_dur:
        return "clips"
    return "records"


def unique_dest(dest):
    """Return a unique destination path if a collision occurs."""
    if not os.path.exists(dest):
        return dest
    base, ext = os.path.splitext(dest)
    i = 1
    while True:
        cand = f"{base} ({i}){ext}"
        if not os.path.exists(cand):
            return cand
        i += 1


def is_cross_volume(src, dest):
    """Determine if a move requires cross-volume copy+delete."""
    try:
        src_drive = os.path.splitdrive(os.path.abspath(src))[0].lower()
        dest_drive = os.path.splitdrive(os.path.abspath(dest))[0].lower()
        if src_drive != dest_drive:
            return True
        src_dir = os.path.dirname(os.path.abspath(src))
        dest_dir = os.path.dirname(os.path.abspath(dest))
        if os.path.exists(src_dir) and os.path.exists(dest_dir):
            return os.stat(src_dir).st_dev != os.stat(dest_dir).st_dev
    except Exception:
        pass
    return False


def human(n):
    """Return a human-readable file size."""
    for u in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024.0
    return f"{n:.1f} PiB"


def parse_args():
    parser = argparse.ArgumentParser(description="Sort videos by duration/size into 1_Clips, 2_Records, 3_Archives.")
    parser.add_argument("scan_dir", help="Directory to scan for video files")
    parser.add_argument("--dest", help="Destination base directory (defaults to scan_dir)")
    parser.add_argument("--execute", action="store_true", help="Actually move the files (default: dry-run)")
    parser.add_argument("--flatten", action="store_true", help="Flatten directory structure in destination (default: preserve structure)")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="Path to ffmpeg executable (default: 'ffmpeg')")
    parser.add_argument("--workers", type=int, default=6, help="Number of concurrent workers for duration probing (default: 6)")
    parser.add_argument("--clips-dur", type=float, default=120.0, help="Max duration for 1_Clips category in seconds (default: 120.0, which is 2 min)")
    parser.add_argument("--arch-dur", type=float, default=900.0, help="Min duration for 3_Archives category in seconds (default: 900.0, which is 15 min)")
    parser.add_argument("--arch-size", type=float, default=500.0, help="Min size for 3_Archives category in MiB (default: 500.0)")
    return parser.parse_args()



def main():
    args = parse_args()

    # Resolve paths
    scan_dir = os.path.abspath(args.scan_dir)
    dest_dir = os.path.abspath(args.dest) if args.dest else scan_dir

    if not os.path.exists(scan_dir):
        print(f"Error: Scan directory '{scan_dir}' does not exist.")
        sys.exit(1)

    # Test if ffmpeg works
    try:
        subprocess.run([args.ffmpeg, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        print(f"Error: ffmpeg executable '{args.ffmpeg}' was not found in path.")
        print("Please install ffmpeg and make sure it is in your system PATH, or specify it via --ffmpeg <path>.")
        sys.exit(1)

    # Destination directories
    dest_paths = [os.path.join(dest_dir, CATS[cat]) for cat in CATS]

    # Gather video files
    print(f"Scanning directory: {scan_dir} ...")
    items = list(walk_videos(scan_dir, dest_paths))
    total = len(items)
    print(f"Discovered {total} video files. Probing durations with {args.workers} workers...", flush=True)

    if total == 0:
        print("No videos found to sort.")
        return

    durs = [None] * total
    done = [0]

    def probe(i):
        durs[i] = ffdur(items[i], args.ffmpeg)
        done[0] += 1
        if done[0] % 100 == 0 or done[0] == total:
            print(f"  probed {done[0]}/{total}", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(probe, range(total)))
    print("Probing completed.", flush=True)

    # Convert size argument to bytes
    arch_size_bytes = args.arch_size * 1024 * 1024

    # Build plan
    rows = []
    stats = {}  # (action, cat) -> [count, bytes]
    for i, path in enumerate(items):
        try:
            size = os.path.getsize(path)
        except Exception:
            size = 0
        dur = durs[i]
        sib = has_sibling_image(path)

        action = "move"
        cat = classify(dur, size, args.clips_dur, args.arch_dur, arch_size_bytes)

        # live photo motion component check
        if sib and (dur is None or dur < 6.0):
            action = "skip_live"
            cat = ""
        elif cat == "unknown" and size < arch_size_bytes:
            action = "skip_unknown"
            cat = ""

        dest = ""
        is_cross = False
        if action == "move":
            if args.flatten:
                dest = os.path.join(dest_dir, CATS[cat], os.path.basename(path))
            else:
                rel = os.path.relpath(path, scan_dir)
                dest = os.path.join(dest_dir, CATS[cat], rel)
            is_cross = is_cross_volume(path, dest)

        key = (action, cat)
        stats.setdefault(key, [0, 0])
        stats[key][0] += 1
        stats[key][1] += size

        rows.append({
            "action": action,
            "cat": cat,
            "dur": "" if dur is None else round(dur, 1),
            "size": size,
            "src": path,
            "dest": dest,
            "cross": "yes" if is_cross else "no"
        })

    # Write plan csv
    plan_filename = "_sort_executed.csv" if args.execute else "_sort_plan.csv"
    plan_path = os.path.join(dest_dir, plan_filename)
    try:
        os.makedirs(dest_dir, exist_ok=True)
        with open(plan_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["action", "category", "duration_s", "size_bytes", "cross_volume", "src", "dest"])
            for r in rows:
                w.writerow([r["action"], r["cat"], r["dur"], r["size"], r["cross"], r["src"], r["dest"]])
        print(f"Plan written to: {plan_path}")
    except Exception as e:
        print(f"Warning: Could not write CSV plan file: {e}")

    # Summary
    mode_str = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"\n==================== SUMMARY ({mode_str}) ====================")
    order = [
        ("move", "archives"),
        ("move", "records"),
        ("move", "clips"),
        ("skip_live", ""),
        ("skip_unknown", "")
    ]
    seen = set()
    for k in order:
        if k in stats:
            c, b = stats[k]
            cat_name = k[1] if k[1] else "(none)"
            print(f"  {k[0]:<14} {cat_name:<9} : {c:5} files, {human(b):>10}")
            seen.add(k)
    for k in sorted(stats):
        if k not in seen:
            c, b = stats[k]
            cat_name = k[1] if k[1] else "(none)"
            print(f"  {k[0]:<14} {cat_name:<9} : {c:5} files, {human(b):>10}")

    move_rows = [r for r in rows if r["action"] == "move"]
    cross_bytes = sum(r["size"] for r in move_rows if r["cross"] == "yes")
    same_bytes = sum(r["size"] for r in move_rows if r["cross"] == "no")
    print(f"\n  cross-volume (copy+delete): {human(cross_bytes)}   |   same-volume (rename): {human(same_bytes)}")

    if not args.execute:
        print("\n  DRY-RUN only. Nothing moved. Re-run with --execute to perform moves.")
        return

    # Execute
    print("\n==================== EXECUTING MOVES ====================", flush=True)
    ok = 0
    fail = 0
    renamed = 0
    for r in move_rows:
        dest = unique_dest(r["dest"])
        if dest != r["dest"]:
            renamed += 1
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(r["src"], dest)
            ok += 1
            if ok % 50 == 0 or ok == len(move_rows):
                print(f"  moved {ok}/{len(move_rows)}", flush=True)
        except Exception as e:
            fail += 1
            print(f"  FAIL: {r['src']} -> {dest} : {e}", flush=True)

    print(f"\nDONE. moved={ok}, collisions_renamed={renamed}, failed={fail}")


if __name__ == "__main__":
    main()
