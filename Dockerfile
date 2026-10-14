# syntax=docker/dockerfile:1
FROM python:3.11-slim

LABEL maintainer="sunhiro"
LABEL description="Smart hardware-accelerated batch video compression with VMAF quality gates"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# 1. ExifTool、VAAPI 硬件驱动、解压工具
#
# 🔴 这里**故意不装** Debian 的 ffmpeg：它的 ffmpeg **没有编 `--enable-libvmaf`**。
#    2026-10-14 独立复核（各自下载 libavfilter 的 .deb，解 ar + data.tar.xz，
#    直接在 .so 里数字节串）：
#      - Debian 12 bookworm: libavfilter.so.8.44.100  → libvmaf 出现 0 次
#      - Debian 13 trixie  : libavfilter.so.10.5.100 → libvmaf 出现 0 次
#    而 ab-av1 的 crf-search 完全依赖 ffmpeg 的 libvmaf 滤镜 —— 装了它只会
#    「镜像构建成功、跑批时才报错」，正是最难查的那种。见第 2 步。
#
# ⚠ 底座会漂：`python:3.11-slim` 现在指 `3.11/slim-trixie`（Debian 13），
#   历史上是 bookworm。**所以「哪个发行版」不是论证**——两个都没有 libvmaf，
#   第 2 步末尾那两行构建期断言才是真正兜住这件事的东西，改底座时别删它。
RUN apt-get update && apt-get install -y --no-install-recommends \
    libimage-exiftool-perl \
    va-driver-all \
    i965-va-driver \
    intel-media-va-driver \
    mesa-va-drivers \
    curl \
    ca-certificates \
    xz-utils \
    zstd \
    tar \
    && rm -rf /var/lib/apt/lists/*

# 2. FFmpeg：BtbN 静态构建（linux64-gpl，同时含 libvmaf 与 VAAPI），**锁死版本**。
#    原先用 master-latest 滚动构建：ab-av1 都锁了 0.11.7，VMAF 闸门依赖的 ffmpeg
#    反而天天在变，破坏 REGRESSION.md「R1 必须可复现」的契约。现改指到
#    BtbN 带日期的冻结 autobuild（资产名含具体 ffmpeg commit，内容不再变），
#    并加 sha256 校验。
#    ⚠ 静态二进制体积较大（ffmpeg+ffprobe 约 350MB 未压缩），这是换来 VMAF 可用的代价。
ARG FFMPEG_URL=https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-13-13-05/ffmpeg-n8.1.3-18-g46d899d7a0-linux64-gpl-8.1.tar.xz
ARG FFMPEG_SHA256=b3506f47a71a2f8cff8c0775112bf02a8a49035e3994232aa5f218058328e0e7
ARG FFMPEG_DIR=ffmpeg-n8.1.3-18-g46d899d7a0-linux64-gpl-8.1
# 先落盘 → sha256 校验 → 再解压：curl|tar 管道直通会让截断的半截二进制溜进镜像。
RUN curl -fsSL -o /tmp/ffmpeg.tar.xz "${FFMPEG_URL}" \
    && echo "${FFMPEG_SHA256}  /tmp/ffmpeg.tar.xz" | sha256sum -c - \
    && mkdir -p "/tmp/${FFMPEG_DIR}" && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp \
    && install -m 0755 "/tmp/${FFMPEG_DIR}/bin/ffmpeg"  /usr/local/bin/ffmpeg \
    && install -m 0755 "/tmp/${FFMPEG_DIR}/bin/ffprobe" /usr/local/bin/ffprobe \
    && rm -rf /tmp/ffmpeg.tar.xz "/tmp/${FFMPEG_DIR}"

# 构建期断言：缺 libvmaf 或 VAAPI 编码器就当场让构建失败，
# 而不是等它在 NAS 上跑批时才表现为「画质门限算不出来」。
RUN ffmpeg -hide_banner -filters  | grep -qw libvmaf \
 && ffmpeg -hide_banner -encoders | grep -qw hevc_vaapi \
 && ffmpeg -hide_banner -version | head -1

# 3. 安装 Rust ab-av1 (直接下载官方预编译二进制，同样锁版本 + sha256 + 先落盘校验)
ARG ABAV1_VERSION=0.11.7
ARG ABAV1_SHA256=40c035df608162fcb1c991d1474bc9bc8817e0354ac7aef5be5314d3efd64069
RUN curl -fsSL -o /tmp/ab-av1.tar.zst "https://github.com/alexheretic/ab-av1/releases/download/v${ABAV1_VERSION}/ab-av1-v${ABAV1_VERSION}-x86_64-unknown-linux-musl.tar.zst" \
    && echo "${ABAV1_SHA256}  /tmp/ab-av1.tar.zst" | sha256sum -c - \
    && tar --zstd -xf /tmp/ab-av1.tar.zst -C /usr/local/bin/ \
    && chmod +x /usr/local/bin/ab-av1 && rm /tmp/ab-av1.tar.zst

# 4. 设置工作目录并复制源码安装
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

# 5. 创建挂载点与默认入口
WORKDIR /data
ENTRYPOINT ["vcompress"]
CMD ["--help"]
