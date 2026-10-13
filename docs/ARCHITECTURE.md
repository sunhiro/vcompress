# vcompress 架构设计与 VMAF 采样原理

## 1. 系统总体架构

`vcompress` 的核心设计理念是 **“拦截优先于覆盖”**。

```
[原始视频文件]
     │
     ▼
[元数据探针 (ffprobe)] ──(小文件 < 100MB?) ──► [直接跳过/原样保留]
     │
     ▼
[窗口采样阶段 (Beginning/Middle/End 15s)]
     │
     ├──► 提取 15s 窗口 -> 编码临时片段
     └──► 计算 VMAF 得分 (对齐滤镜链: trim + setpts + fps + format)
     │
     ▼
[质量与体积双闸门校验]
     ├──► VMAF < vmaf_min (92分)  ──► [拒绝全片编码，抛弃产物]
     ├──► 节省空间 < 20%          ──► [拒绝全片编码，保留原片]
     └──► 双校验通过              ──► [允许执行全片编码]
     │
     ▼
[全片编码与元数据继承 (exiftool)] ──► [输出无损压缩文件]
```

---

## 2. VMAF 帧对齐 (Frame Synchronization) 核心原理

在过去的脚本测试中，计算 VMAF 时经常遇到假低分（如肉眼无差别但评分仅 40 分）。其核心根源是：

1. **输入端 Seeking (`-ss`) 导致 keyframe 错位**：压缩后的 H.265 与原片的 GOP 结构不同，Seek 到的起跑帧不在同一真实秒数。
2. **变帧率 (VFR) 时间轴漂移**：手机录制的视频无固定帧率，比对时 FFmpeg 自动补帧/跳帧导致错位。

### vcompress 的解法：

在 `compute_vmaf_sample` 中，使用复杂的 FFmpeg `filtergraph` 滤镜链：

```
[1:v] trim=start=SS:end=END, setpts=PTS-STARTPTS, fps=30, format=yuv420p [r];
[0:v] setpts=PTS-STARTPTS, fps=30, format=yuv420p [d];
[d][r] libvmaf=model=version=vmaf_v0.6.1:n_threads=8:log_path=LOG:log_fmt=json
```

- `trim`: 精确截取时间段（基于 PTS 而非 GOP）。
- `setpts=PTS-STARTPTS`: 将两路视频的时间戳起点重置为 0。
- `fps=30`: 将变帧率视频统一重采样到固定的 30fps，消除时间轴漂移。
- `format=yuv420p`: 统一像素格式，消除色彩深度转换引起的评估干扰。
