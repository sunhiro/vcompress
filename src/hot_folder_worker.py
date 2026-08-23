#!/usr/bin/env python3
"""
vcompress Hot Folder Worker (方案 A)
功能：监控 NAS 上指定的“待压缩”文件夹（热文件夹）。
一旦有新的视频文件放入，自动调用 vcompress_abav1.py 进行压制，并将产物输出到“已压缩”文件夹。
"""

import os
import time
import subprocess
import logging
from pathlib import Path

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# === 配置区 ===
# 请在运行前确保这两个目录存在（可在 NAS 上创建）
NAS_ROOT = Path(os.path.expanduser("~/nas/video"))
INBOX_DIR = NAS_ROOT / "00_待压缩接收箱"
OUTBOX_DIR = NAS_ROOT / "01_压缩完成"

# 扫描间隔（秒）
POLL_INTERVAL = 30

# vcompress 脚本路径
VCOMPRESS_SCRIPT = Path(__file__).parent / "vcompress_abav1.py"

def ensure_dirs():
    """确保监控和输出目录存在"""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)

def get_pending_videos():
    """获取 Inbox 中所有待压视频文件"""
    extensions = {'.mov', '.mp4', '.mkv', '.avi', '.m4v'}
    pending = []
    for f in INBOX_DIR.rglob('*'):
        if f.is_file() and f.suffix.lower() in extensions and not f.name.startswith('.'):
            # 过滤掉已经是 hevc 的文件或正在压的中间产物
            if '.hevc.' not in f.name and '.tmp' not in f.name:
                pending.append(f)
    return pending

def process_video(video_path: Path):
    """调用 vcompress_abav1.py 压制视频"""
    logging.info(f"🚀 发现新任务: {video_path.name}，开始压制...")
    
    # 保持目录结构
    rel_path = video_path.relative_to(INBOX_DIR)
    out_path = OUTBOX_DIR / rel_path.parent / f"{video_path.stem}.hevc{video_path.suffix}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    cmd = [
        "python3", str(VCOMPRESS_SCRIPT),
        "-i", str(video_path),
        "-o", str(out_path),
        "-e", "vt",
        "--json-out"
    ]
    
    try:
        # 使用 subprocess.run 阻塞等待完成
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            logging.info(f"✅ 压制成功: {out_path.name}")
            # 压制成功后，可选择将源文件移动到备份区，或者直接删除。这里为了安全，重命名源文件加个标记。
            done_mark = video_path.with_name(f".{video_path.name}.done")
            video_path.rename(done_mark)
        else:
            logging.error(f"❌ 压制失败: {video_path.name}\n{result.stderr}")
    except Exception as e:
        logging.error(f"⚠️ 执行出错: {e}")

def main():
    ensure_dirs()
    logging.info(f"👀 Hot Folder Worker 启动...")
    logging.info(f"📥 监控目录: {INBOX_DIR}")
    logging.info(f"📤 输出目录: {OUTBOX_DIR}")
    
    while True:
        try:
            videos = get_pending_videos()
            for v in videos:
                # 检查文件是否正在被写入（比如刚拷贝了一半）
                size_1 = v.stat().st_size
                time.sleep(2)
                size_2 = v.stat().st_size
                if size_1 == size_2 and size_1 > 0:
                    process_video(v)
                else:
                    logging.info(f"⏳ 文件 {v.name} 仍在写入中，跳过本次处理。")
        except Exception as e:
            logging.error(f"监控循环出错: {e}")
            
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
