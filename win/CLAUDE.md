# win/ — Windows 侧压缩工具链（SSIM 引擎）

> 🔴 **这不是 `src/` 那套的旧版本，是按平台分工的另一套引擎**（ADR-007）。
> 2026-08-13 从 `nas` 仓的 `video/` 迁入——此前"从 nas 解耦独立"只做了一半，
> 旧的一直没退役且是 Windows 侧唯一入口，两边谁也没指认谁。

## 分工

| | `win/`（本目录） | `src/` |
|---|---|---|
| 平台 | **Windows** | macOS |
| ffmpeg | digiKam 自带的 `D:\Program Files\digiKam\ffmpeg.exe` | 完整 ffmpeg + ab-av1 |
| 质量评分 | **SSIM / PSNR** | **VMAF** 二分查找 |
| 接入 | `bin\{compress-video,compare-video,sort-video}.cmd`、yazi `\` 菜单、用户级 skill `video-compress` | Mac 上跑 T7 批处理 |

**为什么不统一**：digiKam 的 ffmpeg 是 `--disable-libvmaf`，**跑不了 VMAF**。
Windows 侧要用 VMAF 就得另装带 libvmaf 的 ffmpeg 并重接三处入口，收益只是评分口径一致；
而 SSIM ≥0.98 在家庭视频上已足够判"几乎无损"（实测 CRF23 普遍 0.97~0.99）。

## 脚本

| 脚本 | 干什么 |
|---|---|
| `batch_compress_structure.py` | 递归压缩。原件**移入** `<root>/.old/`、压缩片放 `<root>/.new/`（前导点让资源管理器置顶，保留相对路径），其余原样留在原目录。默认出 `.report.csv` + `.report.xlsx` |
| `compare_compressed.py` | 对比 `.old` vs `.new` 出报告：CSV + xlsx（首行筛选 + 冻结）+ summary.txt，含结构化的「综合建议」列 |
| `sort_by_duration.py` | 按时长/体积分拣（argparse、`--ffmpeg`、Win+Linux 通用） |

## 🔴 质量评分的坑（踩满一轮才调对的）

1. **原片与压缩片的时基/帧率常不同**（iPhone 可变帧率、竖拍旋转元数据）。
   直接 ssim 会**逐帧错位得到假分数**（见过 0.87，实际 0.98）。必须：
   两路都 `fps=fps=<原片fps>,settb=AVTB` 归一，再用 **`scale2ref`** 把压缩片缩到原片实际尺寸
   —— **硬编码 scale 到编码尺寸会和自动旋转打架**，导致分数空白。
2. **子进程调 ffmpeg 必须加 `-nostdin`**，否则个别文件会让 ffmpeg 等 stdin 输入而**永久挂起**
   （见过一个 4.5 分钟长片卡死，加 `-nostdin` 后 12s 出分）。单段超时设 240s，卡住杀掉记 `None`。
3. **任何一段（含整片）都必须用 `-t` 收在结尾前**（GUARD 0.5s），有效时长取原/压两者较小值。
   否则可变帧率原片跑到结尾与恒定帧率压缩片总帧数不齐，触发 `scale2ref` 断言崩溃
   `best_input>=0` → 整片路径全 `None` 甚至硬崩。
4. **`--quality-sample N` 不是"截开头 N 秒"**，而是拆成 `--quality-windows`（默认 3）段
   均匀撒在全片前/中/后再平均——否则中段/后段才爆发的运动会漏测、分数偏乐观。`0` = 整片（最准）。

## 编码与评级

- `libx265 -crf 23 -preset veryfast`，音频 `copy`（失败回退 aac 128k）。
  **CRF 是恒定质量，码率随内容浮动**——每个文件压缩率不同是正常的。想更省体积调高 CRF（26），别用固定码率。
- `--min-size` / `--min-bitrate` 是**"选哪些文件压"的筛选器**，不是输出码率。
- 音频 `-c:a copy` 不动：AAC 已高效，只占总码率 ~2%，不值得压。
- SSIM 评级：**≥0.98 几乎无损 / 0.95~0.98 优 / 0.90~0.95 良 / <0.95 可见损失**。
  完全相同（原样拷贝）→ SSIM 1.0、PSNR ∞。
- 实测家庭视频 CRF23 普遍 **0.97~0.99**，旧的 `KEEP_ORIGINAL` 启发式（按码率降幅）偏保守，
  质量分能给客观证据翻案。
