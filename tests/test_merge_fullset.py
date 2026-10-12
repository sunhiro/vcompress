"""test_merge_fullset.py — 针对全集合并脚本 merge_fullset.py 的单元测试。"""

import os
from pathlib import Path
import pytest

from merge_fullset import plan_merge, apply_merge, scan_tree
from naming import marked_name


def test_scan_tree_skips_hidden_and_meta(tmp_path):
    (tmp_path / "video.mp4").write_text("v")
    (tmp_path / "._video.mp4").write_text("dot")
    (tmp_path / ".DS_Store").write_text("ds")
    (tmp_path / "_report.csv").write_text("csv")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "photo.jpg").write_text("pic")

    res = scan_tree(tmp_path)
    assert "video.mp4" in res
    assert "sub/photo.jpg" in res
    assert "._video.mp4" not in res
    assert ".DS_Store" not in res
    assert "_report.csv" not in res


def test_plan_merge_identifies_compressed_and_untouched(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    # 1. 压过的视频
    (src / "v1.MOV").write_text("source v1 content")
    (dst / marked_name("v1.MOV")).write_text("comp")

    # 2. 未压的视频 (跳过)
    (src / "v2.mp4").write_text("v2 content")

    # 3. 非视频文件 (照片)
    (src / "photo.jpg").write_text("photo content")

    plan = plan_merge(src, dst)
    assert plan["compressed_products_count"] == 1
    assert plan["source_cold_backup_count"] == 1

    uv_names = [x[0] for x in plan["untouched_videos"]]
    assert uv_names == ["v2.mp4"]

    nv_names = [x[0] for x in plan["non_videos"]]
    assert nv_names == ["photo.jpg"]


def test_plan_merge_guards_against_inefficient_products(tmp_path):
    """如果产物相对原片节省率 < 20%（例如原片 100 字节，产物 95 字节，仅省 5%），
    plan_merge 不应将其视为有效压缩替代，而应将原片放入 untouched_videos 并入全集。
    """
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    (src / "small_gain.mp4").write_bytes(b"x" * 100)
    (dst / marked_name("small_gain.mp4")).write_bytes(b"y" * 95)  # 仅省 5%

    plan = plan_merge(src, dst, min_saving_pct=20.0)
    assert plan["compressed_products_count"] == 0
    assert len(plan["inefficient_products"]) == 1
    assert plan["inefficient_products"][0][1] == "small_gain.mp4"
    # 原版视频被保留并列入未压缩待并入列表
    assert [x[0] for x in plan["untouched_videos"]] == ["small_gain.mp4"]


def test_apply_merge_move_leaves_cold_backup_only(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    # v1 被压过 (原片 100 字节，压缩后 40 字节，省 60% > 20%)
    (src / "v1.mp4").write_bytes(b"s" * 100)
    (dst / marked_name("v1.mp4")).write_bytes(b"c" * 40)

    # v2 没被压
    (src / "v2.mp4").write_bytes(b"s" * 100)

    plan = plan_merge(src, dst)
    items = plan["untouched_videos"] + plan["non_videos"]
    ok, relocked, errors = apply_merge(src, dst, items, mode="move")

    assert ok == 1
    assert errors == []

    # 目标全集目录：同时有压缩产物和原版 v2
    assert (dst / marked_name("v1.mp4")).exists()
    assert (dst / "v2.mp4").exists()

    # 源目录：只留下了被压过的 v1 原片当冷备份！v2 已被搬走
    assert (src / "v1.mp4").exists()
    assert not (src / "v2.mp4").exists()


def test_plan_merge_excludes_camera_proxies_and_raw(tmp_path):
    """验证 merge_fullset 能根据模式准确排除大疆 LRF 代理文件与 DNG RAW。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    (src / "normal_video.mp4").write_text("v")
    (src / "DJI_0001_LRF.mov").write_text("proxy")
    (src / "clip.LRF").write_text("proxy2")
    (src / "photo.DNG").write_text("raw")
    (src / "photo.JPG").write_text("pic")

    plan = plan_merge(src, dst, exclude_patterns=["*._*", "*_LRF.*", "*.lrf", "*.dng"])

    uv_names = [x[0] for x in plan["untouched_videos"]]
    nv_names = [x[0] for x in plan["non_videos"]]
    ex_names = [x[0] for x in plan["excluded_items"]]

    assert "normal_video.mp4" in uv_names
    assert "photo.JPG" in nv_names
    assert "DJI_0001_LRF.mov" in ex_names
    assert "clip.LRF" in ex_names
    assert "photo.DNG" in ex_names

