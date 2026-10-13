# syntax=docker/dockerfile:1
FROM python:3.11-slim

LABEL maintainer="sunhiro"
LABEL description="Smart hardware-accelerated batch video compression with VMAF quality gates"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# 1. 安装 FFmpeg (带 libvmaf 与 VAAPI 支持), ExifTool, VAAPI 硬件驱动, zstd
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libimage-exiftool-perl \
    va-driver-all \
    i965-va-driver \
    intel-media-va-driver \
    mesa-va-drivers \
    curl \
    ca-certificates \
    zstd \
    tar \
    && rm -rf /var/lib/apt/lists/*

# 2. 安装 Rust ab-av1 (直接下载官方预编译二进制)
ARG ABAV1_VERSION=0.11.7
RUN curl -fsSL "https://github.com/alexheretic/ab-av1/releases/download/v${ABAV1_VERSION}/ab-av1-v${ABAV1_VERSION}-x86_64-unknown-linux-musl.tar.zst" \
    | tar --zstd -x -C /usr/local/bin/ \
    && chmod +x /usr/local/bin/ab-av1

# 3. 设置工作目录并复制源码安装
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

# 4. 创建挂载点与默认入口
WORKDIR /data
ENTRYPOINT ["vcompress"]
CMD ["--help"]
