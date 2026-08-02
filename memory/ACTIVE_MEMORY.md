# ACTIVE_MEMORY.md — vcompress 项目记忆与当前状态

> **最近更新**：2026-08-03（由 vcat 终端更新，完成引擎迁移与评审回复）
> **当前状态**：引擎已被 vcat 真机版覆盖；本仓定位调整为**自包含的备份实现**

---

## 0. 先读这三份

| 文件 | 为什么重要 |
|---|---|
| [`docs/RED-LINES.md`](../docs/RED-LINES.md) | **12 条红线 + 每条的真机证据。改代码前必读。** 这份比代码重要 |
| [`docs/REGRESSION.md`](../docs/REGRESSION.md) | 已知坏例、预期结论、**已证伪的假说清单**。改完必跑 |
| [`REVIEW_FROM_VCAT.md`](../REVIEW_FROM_VCAT.md) | 对解耦提案的完整评审 + 本次迁移做了什么 |

---

## 1. 2026-08-02/03 发生了什么

用户指示：把 vcat 里经过群晖真机打磨的压缩能力**完整迁移**到本仓，作为将来
不用 ab-av1 时仍可用的备份实现。已完成：

- `src/vcompress.py` **被 vcat 的完整版本覆盖**（1093 行），旧版留档为 `.pre-migration.bak`
- 新增 `src/diag_vmaf_window.py`（VMAF 测量诊断，import 引擎复用同一套 seek）
- 新增 `docs/RED-LINES.md`、`docs/REGRESSION.md`
- README 里已被证伪的说法已更正

**迁移带进来的能力**（原实现没有的）：`vaapi` 编码路径、混合 seek、
近无损对照与 `measurement_unreliable` 状态、位深/色度守卫、扩展名补齐、
窗口不带音轨、起始窗口不从字节 0 起解、MP2→aac 音轨兜底。

### 🔴 许可证提醒

**vcat 是 AGPL-3.0，本仓是 MIT**，迁移即一次**降级重新许可**。
两边版权同属一人（sunhiro）故可行，用户已知情。vcat 若转公开，两边都要写清。

---

## 2. 对上一版留下的三个评审项的答复（都有真机数据）

### ✅ 评审项 1：VMAF 采样帧率动态化 —— 你的疑虑是对的，但问题比这更深

**先答你问的**：`fps=30` 写死确实不对。真机 235 个文件的帧率分布：
**30(163) / 24(19) / 25(7) / 120(4) / 240(1)** —— 那 5 个是 iPhone 慢动作，
固定 30 会让 **87.5% 的帧根本不参与画质评估**。

**但更要紧的是：帧率归一化压根修不了串帧那个 bug。** 真机实测：

```
IMG_2151.MOV Middle@29s QP20
  原样                                  49.51
  两侧 fps=24/1 + format=yuv420p        46.75   (Δ-2.76，反而略降)
  逐帧形状纹丝不动：前10帧 99.5 / 后10帧 7.2
```

真正的错位是**两侧时间跨度不同**（失真侧 359 帧 / 参考侧 369 帧），
`fps=` 只改一段时间内的采样密度，**改不了这段时间有多长**。

**并且要把两件事分开**：
- **输出帧率**：**永远不改**。改帧率是改内容，不是压缩。（现在就没加 `-r`，是对的）
- **测量帧率**：真要归一化只能用源片自己的帧率，绝不用常数。

**⚠ 另外 `format=yuv420p` 有个没人注意的风险**：真机有 `yuvj420p` 56 个 + `yuvj422p` 50 个，
`yuvj` 是全范围（0-255）、`yuv` 是有限范围（16-235），这个转换**直接改动亮度**——
而 VMAF 恰恰只测亮度。**未经验证，当前不采用**。详见 RED-LINES §11。

### ⏸ 评审项 2：多文件并发 —— 现在不做，理由是收益取决于瓶颈在哪

- **群晖上无收益**：只有一块 Intel 核显，VAAPI 编码是**硬件串行**的，
  开并发只会互相排队。实测单任务已经吃到 ~400% CPU（四核满）。
- **Mac 上可能有收益**（M 系列多个 Media Engine），但要先测。
- **风险**：并发会引入临时文件竞争。当前临时文件用 `NamedTemporaryFile` 尚安全，
  但 `_report.csv` 是**逐行追加**的，多线程写会串行，得先加锁。
- **若走 ab-av1 路线则整条moot**——它自己就并行跑样本编码。

**结论：先别做。** 真要做，先在 Mac 上量出瓶颈到底是不是编码器。

### ✅ 评审项 3：`--json-out` —— 该做，这是对接 vcat 的正确接口

同意，而且这是本仓**最有独立价值**的方向。建议每个文件输出一条 JSON，
字段至少覆盖当前报表 8 列 + 以下诊断信息（现在只在日志里，vcat 拿不到）：

```jsonc
{
  "rel_path": "video/1_Clips/2014/2014-07/IMG_2151.MOV",
  "status": "success",            // success / quality_rejected / encode_failed
                                  // / measurement_unreliable / skipped_*
  "orig_mb": 184.58, "new_mb": 68.00, "saving_pct": 63.2,
  "vmaf": 96.13, "q_label": "QP", "q_val": 26,
  "src_pix": "yuvj420p", "out_pix": "yuv420p",   // 位深/色度变迁，实测非推断
  "windows": [                    // 每档每窗口的分数，vcat 用来做对比看板
    {"q": 28, "name": "Beginning", "vmaf": 92.11},
    {"q": 28, "name": "Middle", "vmaf": 46.24,
     "control_vmaf": 49.57, "distrusted": true,   // 近无损对照结果
     "dist_frames": 359, "ref_frames": 369}       // 错位签名
  ]
}
```

**🔴 `status` 里绝不能有笼统的 `failed`** —— 「机器坏了」「素材压不动」「测量不可信」
是三件不同的事，混在一起就是 RED-LINES §2 那条红线。

---

## 3. 本仓接下来的定位

**主线不在这里。** 查证 `ab-av1` 源码后的结论（详见 `REVIEW_FROM_VCAT.md §5`）：
它的采样是 `ffmpeg -ss X -i in -frames:v N -c:v copy -an -sn`，
**编码这个 sample 并与这个 sample 比 VMAF**——窗口只被确定一次、两侧共用同一个文件，
**结构上不可能错位**。外加 CRF 插值二分搜索、结果缓存、hevc_vaapi 支持、
以及经 ffmpeg 白得的 NVENC / QSV / VideoToolbox。

所以规划是：**ab-av1 当引擎，vcat 当大脑，本仓当备份实现。**

**本仓若要继续投入，值得做的只有一件**：`--json-out`（评审项 3）。
**不建议**再朝「又一个自研测量与选档」的方向写——那部分 ab-av1 做得更好。

---

## 4. 尚未验证 / 已知欠缺

- **迁移后未在本仓做过真机跑批**（迁移时群晖 `inas` 不可达）。
  首次真机运行前请跑 `docs/REGRESSION.md` 的 **R1 / R2 / R3**。
- **没有 Windows 硬编码**（NVENC / QSV）——`encode_video` 只有 vaapi / vt / x265。
  若走 ab-av1 路线可白得，故未自行补。
- **`--json-out` 未实现**。
- `examples/example_usage.sh` 与 `docs/ARCHITECTURE.md` **尚未按新引擎更新**，
  其中关于「fps=30 彻底解决串帧」的表述已过时，读时留意。

---

## 5. 真机环境备忘（来自 vcat）

- 群晖 `sunnas`：`ssh inas`（tailscale `100.78.213.111:17080`）。**局域网 `nas` 别名在 Mac 不在家时不通。**
- DSM 的 docker 在 `/usr/local/bin/docker`，非交互 ssh 的 PATH 里没有，**要写全路径**。
- `scp` 到 DSM **必须加 `-O`**。
- 长任务用 `docker run -d`，**别用 `--restart unless-stopped`**——它不看退出码，
  批量正常跑完（exit 0）也会被拉起来无限重跑。用 `on-failure:5`。
- 容器里跑 python **必须加 `-u`**，否则 stderr 块缓冲，VMAF 约 3.5 分钟才一行、
  攒满一屏要 6 小时，**看着像卡死其实活得很好**。
- **⚠ 稳定性存疑**：两次长时间 VAAPI 满载后整机失联（2026-07-30、2026-08-02）。
  Apollo Lake（J3455）的 i915 挂死是已知问题类别。机器恢复后先查
  `dmesg | grep -iE "i915|GPU HANG|hung task"`。
