"""test_sync_udisk_deletions.py — 针对 U 盘反向同步删除工具的单元测试。"""

from pathlib import Path
import pytest

from sync_udisk_deletions import scan_udisk_candidates, apply_deletions
from naming import marked_name


def test_scan_udisk_candidates_dry_run(tmp_path):
    udisk = tmp_path / "udisk"
    local = tmp_path / "local"
    udisk.mkdir()
    local.mkdir()

    # 1. 存在且未删
    (udisk / "keep.mp4").write_text("keep content")
    (local / "keep.mp4").write_text("keep content")

    # 2. 本地已转码并带 .hevc 标记
    (udisk / "transcoded.MOV").write_text("transcoded content")
    (local / marked_name("transcoded.MOV")).write_text("hevc content")

    # 3. 本地已删
    (udisk / "deleted_video.mp4").write_text("deleted content")

    # 4. 相机特殊保护文件 (THM, SCR)
    (udisk / "video.THM").write_text("thumb")
    (udisk / "video.SCR").write_text("screen")

    # 5. 默认受保护的 LRF 代理
    (udisk / "clip.LRF").write_text("proxy")

    # 默认扫描：LRF 保护
    to_del, skipped, total = scan_udisk_candidates(udisk, local, protect_proxies=True)
    del_names = [f[0].name for f in to_del]
    skip_names = [f[0].name for f in skipped]

    assert total == 6
    assert "deleted_video.mp4" in del_names
    assert "keep.mp4" not in del_names
    assert "transcoded.MOV" not in del_names  # 带 .hevc 产物识别成功，不误删
    assert "video.THM" in skip_names
    assert "video.SCR" in skip_names
    assert "clip.LRF" in skip_names


def test_scan_udisk_candidates_purge_lrf(tmp_path):
    udisk = tmp_path / "udisk"
    local = tmp_path / "local"
    udisk.mkdir()
    local.mkdir()

    (udisk / "clip.LRF").write_text("proxy")
    (udisk / "DJI_0001_LRF.mov").write_text("proxy_mov")

    # 1. 默认保护模式：两者均受保护
    to_del, skipped, _ = scan_udisk_candidates(udisk, local, protect_proxies=True)
    skip_names = [f[0].name for f in skipped]
    assert "clip.LRF" in skip_names
    assert "DJI_0001_LRF.mov" in skip_names

    # 2. 允许清除 LRF 模式：本地无该文件时列入待删除
    to_del, skipped, total = scan_udisk_candidates(udisk, local, protect_proxies=False)
    del_names = [f[0].name for f in to_del]

    assert "clip.LRF" in del_names
    assert "DJI_0001_LRF.mov" in del_names


def test_scan_udisk_candidates_dcim_folder_alignment(tmp_path):
    """验证即使本地备份在 DCIM 子目录下，或者 U 盘带 DCIM 根路径，也能智能对齐识别，避免误删。"""
    udisk = tmp_path / "udisk"
    local = tmp_path / "local"
    (udisk / "100MEDIA").mkdir(parents=True)
    (local / "DCIM" / "100MEDIA").mkdir(parents=True)

    (udisk / "100MEDIA" / "photo.JPG").write_text("pic")
    (local / "DCIM" / "100MEDIA" / "photo.JPG").write_text("pic")

    to_del, _, total = scan_udisk_candidates(udisk, local)
    assert len(to_del) == 0  # 自动识别 DCIM/100MEDIA/photo.JPG，不误删



def test_apply_deletions_physically_removes(tmp_path):
    udisk = tmp_path / "udisk"
    udisk.mkdir()
    f1 = udisk / "f1.mp4"
    f2 = udisk / "f2.mp4"
    f1.write_bytes(b"12345")
    f2.write_bytes(b"67890")

    candidates = [(f1, 5), (f2, 5)]
    cnt, freed_bytes, errs = apply_deletions(candidates)

    assert cnt == 2
    assert freed_bytes == 10
    assert not f1.exists()
    assert not f2.exists()
    assert errs == []
