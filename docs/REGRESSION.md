# 回归测试集：已知坏例与预期结论

> 迁移自 `vcat`（2026-08-02）。**改动测量或闸门相关代码后，必须跑这一组。**
>
> 为什么需要它：本项目的失败模式**几乎全是静默的**——不报错，只是给出一个
> 看着合理的低分。没有已知答案的样本作锚，你无法判断一次改动是修好了还是弄坏了。

## 🔴 两套测试，分工不同，别互相顶替

| | 验什么 | 要什么 | 什么时候跑 |
|---|---|---|---|
| **单元套件** `tests/`（2026-09-04 新建） | **记得对不对** —— 命名契约、状态分派、报表清理、清单读写 | 纯 Python，毫秒级 | **每次改完代码**，无条件 |
| **真机套件 R1~R8**（本文档主体） | **测得准不准** —— VMAF 窗口、闸门、原片保护 | 真视频 + ffmpeg + 群晖 | 动测量/闸门相关代码时 |

单元套件跑法（本仓根目录）：

```bash
uv venv .venv && uv pip install --python .venv/bin/python pytest openpyxl   # 首次
.venv/bin/python -m pytest
```

**它锁的正是 2026-09-03 那轮跨两仓审计（vcat KANBAN Epic 13）修掉的东西**——
当时本仓 4227 行代码零自动化测试，四条真缺陷全靠一个独立上下文的复检员读代码抓出，
其中一条还是那轮自己引入的回归（preview 抹掉血缘证据）。建套件时做了 **11 次变异
验证，11 次全部当场被抓**（8 次在引擎、3 次在命名契约）。

⚠ **跑变异验证时先删 `__pycache__`。** 建套件时踩到：把 `.hevc` 换成 `.h265`
**字节数相同**，若写入与还原发生在同一秒内，Python 认为 `.pyc` 仍然有效，
于是缓存了变异版本，还原之后 25 个测试仍在报错。
`find . -name __pycache__ -type d -not -path './.venv/*' -exec rm -rf {} +`

## 素材位置

真机样本在群晖 `sunnas` 上（`ssh inas`，tailscale `100.78.213.111:17080`）：

| 代号 | 路径 | 属性 |
|---|---|---|
| **健康对照** | `/volume3/video/1_Clips/2014/2014-07/IMG_2150.MOV` | h264 1080p 65.0s 21.4Mbps，r=24.000/avg=24.086 |
| **教科书坏例** | `/volume3/video/1_Clips/2014/2014-07/IMG_2151.MOV` | h264 1080p 73.2s 21.1Mbps，r=24.000/**avg=24.135**（轻微 VFR） |
| **三窗口全坏** | `/volume3/video/1_Clips/2015/2015-04/IMG_1933.MOV` | 三个窗口在 QP24 下全崩（14.26/6.62/8.28） |
| **DVD 流片段** | `/volume3/video/0_records/3_Archives/2008/2008-05/VTS_01_2.VOB` | mpeg2video，start_time 1609s，无序列头；MP2 音轨 |

`IMG_2150` 与 `IMG_2151` 是**同相机同次拍摄的连号文件**，能测的属性几乎全一样——
这对连号对照是本项目最有价值的样本，**别弄丢**。

## 预期结论

### R1 · 近无损对照的标定（§3）

```
vcompress -i <dir> -o <out> -e vaapi --file-list <只含 IMG_2150 的清单>
```

| 检查点 | 期望 | 2026-08-02 实测 |
|---|---|---|
| `IMG_2150` QP20 窗口 VMAF | ≥ 99 | 99.35 |
| `IMG_2150` QP1 近无损对照 | ≥ 99.5 | **99.99** |
| `IMG_2150` 两侧帧数 | 相等 | 360 / 360 |
| `IMG_2151` QP20 窗口 VMAF（Middle） | ≈ 49.5 | 49.51（两次独立运行一致） |
| `IMG_2151` QP1 近无损对照（Middle） | **仍然低**（< 55） | **49.57** |
| `IMG_2151` 两侧帧数 | **不相等** | 359 / 369 |

**🔴 如果 `IMG_2151` 的近无损对照变高了**，说明测量路径被修好了（好事），
但 `--control-floor 95` 的余量需要**重新标定**，别直接沿用。

### R2 · 端到端：坏窗口被剔除后文件应被救回（§3 + §5）

`IMG_2151.MOV` 走完整流程：

```
QP28: Beginning 92.11 · Middle 46.24 · End 91.69
      → Middle 触发对照 → 49.57 < 95 → 判定不可信、剔除
      → 剩余两窗口 min = 91.69，仍 < 93        ← 🔴 必须继续往下一档，不许放水
QP26: Beginning 96.47 · Middle 已跳过 · End 96.13
      → min 96.13 ≥ 93 → 通过闸门 → 全片编码
结果: 保留压缩版，184.58MB → 68.00MB，省 63.2%，报表记 VMAF 96.13 / QP26
```

**这条最重要的断言是那个「不许放水」**：剔除坏窗口后如果分数仍不达标，
必须老实继续走阶梯，而不是因为「剔除过了」就放行。

### R3 · 三窗口全坏 → 判 `measurement_unreliable`（§2）

`IMG_1933.MOV` 三个窗口的对照应全部不过关 → 报表写
**「需人工检查(测量不可信)」**，**绝不允许**出现「画质不达标」字样。

历史数据：QP24 三窗口 14.26 / 6.62 / 8.28；QP28 三窗口 78.64 / 17.83 / 29.33。
注意 QP24 那轮三窗口极差只有 7.64 —— **「极差 > 20」这类启发式抓不住它**，
这正是改用近无损对照的原因。

### R4 · DVD 流片段不从字节 0 起解（§7）

`VTS_01_2.VOB` 的起始窗口必须落在 15s（输入端 seek = 5 > 0）。
若退回 5.0s，该文件会得到约 **16.45** 分的假低分（帧数相等 450=450）。

### R5 · MP2 音轨不被静默丢弃（§10）

`VTS_01_2.VOB` 全片编码时 `-c:a copy` 会被 MP4 muxer 拒 →
应自动退成 aac 重试，**产物必须有音轨**。

```
ffprobe -select_streams a -show_entries stream=codec_name -of csv=p=0 <产物>
# 期望：aac（不是空）
```

### R6 · 像素格式解析（§11，纯单元测试，秒级）

```python
pix_bit_depth('p010le') == 10      # semi-planar 那一族只能查表，正则会读错
pix_chroma('nv12')      == '420'
pix_chroma('yuv420p10le') == '420' # 别把位深数字误读成色度
pix_bit_depth('yuv420p')  == 8
```

### R7 · 报表加列不破坏下游（§11）

第 8 列「像素格式」是后加的。下游 `backfill_compress_report.py` 用
`decision != "保留压缩版"` **精确匹配**挑行、读 `row[:7]`。

```
"a.MOV",184.58,68.00,63.2,96.13,26,保留压缩版,yuvj420p->yuv420p
                                   ^^^^^^^^^^ 第 7 列必须一字不变
```

**加列安全，改列致命。** 往「决定」里拼任何后缀都会让血缘登记一条都认不出来。

### R8 · 原片零改动（§1）

任何一轮跑完后：

```
原片 sha256 压缩前后必须一字节未变
```

**这条要做变异验证**：故意改代码让它回写原片，确认测试当场抓出，再改回来。
否则不知道它是真锁着还是恒真。

### R9 · 生产引擎不降位深（§11，2026-09-06 新增）

**为什么要有这条**：红线 11 要求「位深降级默认拒绝」，而这道守卫**只在
`vcompress.py` 里**（`:688`），生产引擎 `vcompress_abav1.py` 一行都没有。
红线文档给的理由是「10bit 现在撞不上，只因为 HEVC 默认跳过」——**那个推理是错的**：
HEVC 只在 `≤ --max-hevc-bitrate`（默认 30Mbps）时跳过，10bit HEVC 高码率素材
照样走进全量转码；而 `--skip-hdr` 默认还是 `False`。

真正让它撞不上的是另一件事：**`hevc_videotoolbox` 会保住 10bit**。
这是实测结论，不是设计保证——所以要有这条用例看着它。

```bash
# 造源片（10bit，两种编码各一个）
ffmpeg -y -f lavfi -i "testsrc2=size=1920x1080:rate=30:duration=6" \
       -c:v libx265 -pix_fmt yuv420p10le -b:v 40M -tag:v hvc1 src_hevc10.mp4
ffmpeg -y -f lavfi -i "testsrc2=size=1920x1080:rate=30:duration=6" \
       -c:v libx264 -pix_fmt yuv420p10le -b:v 40M src_h264_10.mp4

# 照抄 run_ab_av1_encode 的 vt 分支
ffmpeg -y -i src_hevc10.mp4 -map 0:v:0 -map "0:a?" -c:v hevc_videotoolbox \
       -c:a copy -movflags +faststart -q:v 50 -tag:v hvc1 out.mp4

ffprobe -v error -select_streams v:0 -show_entries stream=pix_fmt -of csv=p=0 out.mp4
```

**2026-09-06 实测（Mac mini / macOS 25.6 / ffmpeg 8.1.2）**：

| 源片 | 源 pix_fmt | 产物 pix_fmt | 结论 |
|---|---|---|---|
| `src_hevc10.mp4` | `yuv420p10le` | **`yuv420p10le`** | ✅ 位深保住 |
| `src_h264_10.mp4` | `yuv420p10le` | **`yuv420p10le`** | ✅ 位深保住 |
| `src_422.mov`（mjpeg） | `yuvj422p` | `yuv420p` | 色度减半（红线 11 允许，第 8 列记着）|

🔴 **产物出现 8bit 就是回归**，意味着位深守卫从「不需要」变成「必需」，
要么补守卫、要么在编码命令里显式 `-pix_fmt`。banding 不可逆，且 **VMAF 测不出来**
（它只看亮度），闸门一个字都不会说。

⚠ 这条**换编码器要重测**：结论只对 `hevc_videotoolbox` 成立。vaapi 那条路
（`-vf format=nv12` 写死 8bit 4:2:0）**一定会降**，那正是 `vcompress.py`
保留位深守卫的原因；`-e vaapi` 已于 2026-09-06 从生产引擎的 choices 里摘掉。

### R10 · ab-av1 的取窗是无损流拷贝（§4 + §5，2026-09-06 补记）

**为什么要有这条**：红线 5「两把尺子」在 `vcompress.py` 上**当前已知未修**
（359/369 帧的签名还在）。生产引擎 `vcompress_abav1.py` 把测量整个交给了 ab-av1，
所以那个根因在这条路上**结构性地不存在**——但这是建立在「ab-av1 用无损流拷贝
取窗、两侧共用同一个文件」这个上游设计上的，不是我们自己保证的。上游改了，
我们这边一声不吭就回到两把尺子。

```bash
ab-av1 crf-search -i <样本> --encoder hevc_videotoolbox --min-vmaf 90 \
       --sample-duration 4s --samples 1 --stdout-format json
# 看它在 --temp-dir 下留下的样本文件名与编码方式
```

**2026-09-05 实测（ab-av1 0.11.5）**：样本落成 `<name>.sample4+120f.mkv`，
参照侧与失真侧共用它。窗口只被确定一次 → 只有一把尺子。

🔴 **若某次升级后样本变成重编码产物，红线 4 的「45 个文件跑 21 小时零通过」
那个坑会原样回来**，且症状是「不随 QP 变化的平坦低分」，看着像画质差。

## 已证伪的假说（别再走回头路）

| 假说 | 实测结果 | 日期 |
|---|---|---|
| 用 x264 `-qp 0` 抠无损中间片当参照物 | 色彩范围不一致，45 个文件 21 小时零通过 | 2026-07-29 |
| 两侧 `fps` 归一化 + `format=yuv420p` 能修错位 | 49.51 → **46.75**，逐帧形状纹丝不动 | 2026-08-02 |
| 「同一 QP 下窗口极差 > 20」可作为坏窗口判据 | `IMG_1933` QP24 三窗口全坏、极差仅 7.64，抓不住 | 2026-08-02 |
| 中断是「Docker 守护进程重启」的孤立事件 | **存疑**：两次中断都在长时间 VAAPI 满载后；Apollo Lake 的 i915 挂死是已知问题。机器恢复后先查 `dmesg \| grep -iE "i915\|GPU HANG"` | 2026-08-03 |

---

## 怎么跑（2026-08-26 首次在本仓执行时补写）

**🔴 NAS 的系统 ffmpeg 跑不了这套回归。** `/usr/bin/ffmpeg` 是 4.1.9，
`-filters` 里只有 `vmafmotion`，**没有 `libvmaf`**。必须进 `vcat-media` 容器：
那里是 ffmpeg N-125781（带 libvmaf）+ iHD VAAPI 驱动 25.2.3 + exiftool 13.25。

样本在群晖上（`ssh inas`，需先 `tailscale up --accept-routes`；Tailscale 停了的话
`ssh inas` 会报 `Connection closed`，不是密钥问题）。**scp 到群晖会被拒**（`Connection closed`），
传文件用 `ssh inas 'cat > 目标' < 源`。

把本仓 `src/` 推上去，挂进容器跑（这样测的是**本仓代码**，不是镜像里烤死的 `/opt/compress_videos_nas.py`）：

```bash
ssh inas 'mkdir -p /volume1/docker/vcat/vcompress-repo/src /volume1/docker/vcat/regression'
for f in vcompress.py diag_vmaf_window.py naming.py fullset.py export_report.py; do
  ssh inas "cat > /volume1/docker/vcat/vcompress-repo/src/$f" < "src/$f"
done

# 批量路径（R2/R3/R4/R5）
ssh inas 'sudo -n /usr/local/bin/docker run --rm --device /dev/dri \
  -v /volume3:/volume3 -v /volume1/docker/vcat/vcompress-repo:/repo \
  -v /volume1/docker/vcat/regression:/reg --entrypoint python3 vcat-media \
  -u /repo/src/vcompress.py -i /volume3 -o /reg/out-R2 --file-list /reg/list-R2.txt -e vaapi'

# R1 的检查点（QP20 / QP1 对照 / 两侧帧数）要用 diag 工具，批量路径复现不出来
ssh inas 'sudo -n /usr/local/bin/docker run --rm --device /dev/dri \
  -v /volume3:/volume3 -v /volume1/docker/vcat/vcompress-repo:/repo \
  --entrypoint python3 vcat-media \
  -u /repo/src/diag_vmaf_window.py --window middle --qp 20 --control-qp 1 -e vaapi \
  /volume3/video/1_Clips/2014/2014-07/IMG_2150.MOV \
  /volume3/video/1_Clips/2014/2014-07/IMG_2151.MOV'
```

**为什么 R1 必须用 diag 而不是批量跑**：默认阶梯是 `28 26 24 22`，健康样本 `IMG_2150`
在 QP26 就过闸门了，**QP20 永远走不到，近无损对照也不会触发**（只有分数低于
`--suspect-below 80` 才触发）。R1 表里那两个数是 diag 工具直接指定 `--qp 20 --control-qp 1` 量出来的。

**顺带记一条**：`IMG_2150` 时长 65.0s，而三窗口的门槛正是 `duration >= 65.0`
（`SEEK_PREROLL(10) + 5 + 15*3 + 5`）。它**卡在边界上走单窗口路径**，所以 R1 表里
只有一个窗口、帧数 360（15s × 24fps）。`IMG_2151` 73.2s 才是三窗口。别以为少了两个窗口是 bug。

**别并发跑**：只有一块 `/dev/dri`，且 Apollo Lake 的 i915 在 VAAPI 长时间满载后挂死
是已知问题（见「已证伪的假说」末条）。串行跑。

## 最近一次执行记录

| 日期 | 结果 |
|---|---|
| 2026-08-26 | **首次在本仓执行**（此前只在 vcat 真机上跑过，见 `REVIEW_FROM_VCAT.md`）。R1·R2·R3·R4·R6·R8 ✅ / R5 ⚠ / R7 ❌ |
| 2026-09-04 | **单元套件建立并首次跑通**：93 passed / 1 skipped（skip = 本机无 blake3，与生产一致）。真机套件 R1~R8 本次**未重跑**（无真机改动）。 |

- **R1 ✅ 逐位复现 2026-08-02 的每一个数**：`IMG_2150` QP20=99.35、QP1 对照=99.99、帧数 360/360；
  `IMG_2151` QP20=49.51、QP1 对照=49.57、帧数 **359/369**。
  → RED-LINES §5「两把尺子」**依然未修**，签名原样健在。
- **R2 ✅ 逐位复现**，含最要命的「不许放水」：QP28 三窗口 92.11/46.24/91.69 → Middle 对照
  49.57 判不可信剔除 → 剩余 min 91.69 仍 < 93 → **老实走下一档**，没放行 →
  QP26 拿 96.47/96.13 过闸 → 184.58MB➔68.00MB / 63.2% / VMAF 96.13 / QP26。
- **R6 ✅** 6/6。**R8 ✅** 原片 sha256+size+mtime+inode+mode 零改动，
  **且做了变异验证**：注入「回写原片」后被当场抓出（检查是真在锁，不是恒真）。
  ⚠ 变异体自己全程不报错，照样打「✅ 转码成功」——这条闸只在外部校验里成立。
- **R7 ❌ 在 NAS 侧不通过**：数据行 9 列 / 表头 8 列。见 ACTIVE_MEMORY 的缺陷 2。
  R7 断言的「第 7 列一字不变」本身仍成立，下游 `backfill` 用 `row[:7]` 侥幸没被破。
- **R3 ✅**：`IMG_1933` QP28 三窗口 78.64 / 17.83 / 29.33（与文档历史值一字不差），
  报表写 `需人工检查(测量不可信: 近无损对照未过关)`，**没有出现「画质不达标」**。
- **R4 ✅**：`VTS_01_2.VOB` 起始窗口落在 **Beginning@15s**，QP28 拿到 89.84 ——
  没有退回 5.0s，也没出现文档说的 16.45 假低分。最终 QP24 过闸，1023.97MB➔539.71MB / 47.3%。
- **R5 ⚠ 断言通过，但机制没被触发，文档的期望值要改**：
  产物**有**音轨（`mp2`, 2ch），音轨没被静默丢弃 —— 这是 R5 真正要守的东西，成立。
  但期望值写的 `aac` **对不上**：产物是 `VTS_01_2.hevc.VOB`，**沿用了源扩展名**，
  走 MPEG-PS muxer，`-c:a copy` 直接成功，「MP4 muxer 拒收 → 退 aac 重试」那条路根本没走到。
  → 要么把 R5 的期望值改成「产物必须有音轨（此样本为 mp2）」，
    要么另找一个输出为 `.mp4` 的 MP2 样本才能真正验到 aac 退避路径。
  → 另有一问待定：HEVC 封进 MPEG-PS（`.VOB`）的播放兼容性，本次未评估。
- **R8 ✅（真机三样本）**：`IMG_2150` / `IMG_2151` / `IMG_1933` 跑完后 sha256 全部一字节未变。
