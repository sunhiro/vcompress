# ACTIVE_MEMORY.md — vcompress 项目记忆与当前状态

> **最近更新**：2026-10-12（吸收一代目经验：大疆相机垃圾/代理文件过滤 + U盘反向同步删除 + 独立弹窗监控）
> **当前状态**：`src/vcompress_abav1.py` 与 `src/merge_fullset.py` 支持 `--exclude-pattern`；新增 `src/sync_udisk_deletions.py` 与 `scripts/pop_monitor.sh`；
> 单元测试 `tests/` **217 passed** ✅。

---

## 0. 下一个会话从这里接手（2026-09-06）

**先读 §8**（09-06 红线复检打回了什么、以及那条最值钱的教训），再读 §7、§6。
⚠ **§7.7 的「14 个变异体 13 个当场被抓」是自评，已被 §8 推翻**，按 §8 的数字算。
§5 是 08-26 的快照，里面「三个缺陷都没修」那句**已经过时**：缺陷 1 已修，
缺陷 2/3 仍在（且只在 `vcompress.py`）。

手上没有进行中的任务，工作区干净。优先级最高的三件：

1. 🔴 **对一次 Mac mini 上的引擎哈希** —— S28 的修复是**引擎与工人配对的**，
   只更一边会退回旧行为。09-05 这轮又改了引擎，那台落后得更多了。
   开下一批压缩前必须做，不是可选项。
2. 🔴 **vcat 侧要配合改一处**（§9.1）：本仓已发 `status: "preview"`，
   而 vcat `compress_worker.py:539` 还没有这一支，会继续落到 `encode_failed`
   （响且安全，不污染台账，但每条 preview 都记一次假失败）。
   **在 vcat 那个会话里加分派**，本仓这边已经完事。
3. **`vcompress.py` 的定位**（§7.6 + §8.5）—— 09-06 查清它是 **Linux 测量基准**
   （R1~R8 跑的就是它），不是「备份」。红线 3/10/11 的守卫**实测都不需要移植**
   到生产引擎（§8.5），三项相关 TODO 的优先级理由要重写。
4. **缺陷 2/3**（§5.3/5.4）—— 只在 `vcompress.py` 里。

🔴 动手前先看 `CLAUDE.md` 红线 5：**台账是 vcat 的，本仓不碰。**
🔴 改完代码**无条件跑** `.venv/bin/python -m pytest`（毫秒级）。它和真机套件分工不同：
   单元验「记得对不对」，R1~R8 验「测得准不准」，**互相顶替不了**。

## 0.1 先读这三份

| 文件 | 为什么重要 |
|---|---|
| [`docs/RED-LINES.md`](../docs/RED-LINES.md) | **12 条红线 + 每条的真机证据。改代码前必读。** 这份比代码重要 |
| [`docs/REGRESSION.md`](../docs/REGRESSION.md) | 已知坏例、预期结论、**已证伪的假说清单**。改完必跑 |
| [`REVIEW_FROM_VCAT.md`](../REVIEW_FROM_VCAT.md) | 对解耦提案的完整评审 + 本次迁移做了什么 |
| [`win/CLAUDE.md`](../win/CLAUDE.md) | **Windows 侧 SSIM 引擎**的分工理由与四个评分坑（2026-08-13 从 nas 仓迁入）。🔴 两套引擎按平台分工，别再合并或退役其一 |
| [`archive/2026-08-13-win压缩与质量分-原始记忆.md`](archive/2026-08-13-win压缩与质量分-原始记忆.md) | Windows 侧压缩/评分的原始笔记（逐字归档，按需读） |

---

## 1. 2026-08-03 重大迭代与功能升级总结

针对用户对个人移动硬盘与家庭影像（如 iPhone MOV / MP4）的大规模高性价比择优压缩需求，完成以下重大更新：

### 1. `src/vcompress_abav1.py` 引擎增强
- **8GB 内存多线程加速模式 (`--vmaf-threads 6`)**：提升 VMAF 二分查找搜寻速度 ~60%，利用 Apple Silicon 算力大幅缩短通宵/长任务时间。
- **动态小文件门限 (`--min-file-mb 20.0`)**：由原来的 100MB 调整为黄金门限 20MB。精准捕获 20MB~100MB 之间的高码率 H.264 MOV 视频（平均可省 85%+ 空间，累计可多释出数十 GB）。
- **Apple `mebx` 数据轨容器隔离**：全量转码采用原生 FFmpeg `-map 0:v:0 -map "0:a?"` 隔离 QuickTime `mebx` 数据轨，解决 MP4 容器封装 Crash。
- **无缝断点续压**：自动跳过目标目录下尺寸非零的已有产物，支持随时中断与无损恢复。

### 2. 多格式可视化台账系统 (`src/export_report.py`)
- **格式化规范**：统一输出 `MB` / `Mbps` / `%` 格式，固定保留 1 位小数，拒绝不可读的长字节数。
- **三重台账自动导出**：
  1. `_report.csv`：基础 CSV，支持增量追加。
  2. `_report.xlsx`：专业 Excel 电子表格，带颜色高亮、网格线与单位格式。
  3. `_report.html`：极简现代 Web 仪表盘，包含顶部 KPI 统计卡片、搜索框、状态筛选器以及表头可点击排序。

---

## 2. T7 批处理实测战果 (`/Volumes/T7/nas` ➔ `/Volumes/T7/nas-压缩后`)

1. **第一轮 (100MB 门限)**：
   - 扫描 3,630 个视频，成功压缩 74 个，**释放磁盘空间 `14.19 GB`**（平均节省率 86.5%），耗时 29 分 36 秒，成功率 100%。
2. **第二轮 (20MB 门限补漏中)**：
   - 包含新增 20MB~100MB 素材，正在后台平稳进行中。

---

## 3. 本仓核心文件说明

- `src/vcompress_abav1.py`：基于 Rust `ab-av1` 的核心择优引擎。
- `src/export_report.py`：极速 Excel 与 HTML 报表生成器。
- `src/vcompress.py`：真机完整版 Python 引擎（保留备份）。


---

## 4. 2026-08-23 OsmoNano 实战与三个缺陷修复

大疆 Osmo Nano 卡（`/Volumes/OsmoNano`）23 个正片，**7.74 GB ➔ 1.1 GB（省 85.6%）**，
VMAF 全部落在 93.14–94.81，23/23 无失败。源片已按用户确认删除（卡上与本机副本都删），
卡上 70 个 `.LRF` 代理与 191 张 JPG 原样保留（给孩子预览用）。

### 4.1 最优 Q 的跨度极大 —— 固定 Q 一刀切在这类素材上不成立

同一台机器、同一次拍摄的 23 个片子，二分搜到的最优 Q **从 25 一路分布到 53**。
实测过固定 Q 的后果：同一个 Q=45，四个素材的 VMAF 落在 **83.5 / 86.8 / 92.8 / 97.1**，
跨度 14 分。按最难的标定则简单素材严重欠压，按平均标定则难素材跌破门限。

🔴 **别再提「跳过 VMAF 搜索、固定 CRF 跑批」这个优化。** 它省的是时间，赔的是
每片画质的确定性，而这条路已经实测走过一次了。

### 4.2 断点续压漏登记内容清单（已修）

`main()` 的续压分支直接 `continue`，跳过了 `manifest[dst_rel] = ...`。
后果：**中断重跑时，上一轮的产物永远进不了 `_manifest.csv`，且不报任何错**——
正是 `fullset.py` 开头讲的「漏记 66 个」的同一个形状，只是换了触发路径。
修法：续压分支里按需补登记（仅在缺行时补，避免大树上每次续跑重算全量哈希），
VMAF 本轮未测则**留空而不是编一个数**。

### 4.3 `_apply_replace.py` 无声自动生成（已修）

这个脚本会删除 `-i` 指向目录下的源片并把产物搬过去。老版**每次跑完都自动生成**，
不打招呼。本次 `-i` 指向的是一份临时原片副本，跑一下副本就没了。
修法：新增 `--emit-replace-script`，**默认不生成**；生成时打 WARNING 说明它会删哪里的文件。

### 4.4 blake3 缺席时清单静默产出空哈希（已修）

`fullset.py` 的设计是「装了 blake3 就算，没装就留空」——留空本身是对的（写错的哈希
比留空危险）。但**它不吭一声**，于是产出一份看起来完整、实则没有内容身份的账。

真机上已经因此误用过一次：删源片前拿清单哈希做校验，校验代码写的是
`if 记录的哈希 and 实测 != 记录`，记录值为空时**短路跳过，报出一轮「全部吻合」**。
好在真正拦得住损坏的是另外两道闸（逐个**完整解码**无错、时长差 0.000s），数据没出事。

修法：`fullset.py` 导出 `HASHING_AVAILABLE`，引擎在目录模式下缺 blake3 时打 WARNING。

🔴 **教训：写校验代码时，「记录值缺失」必须是显式失败或显式跳过，不能悄悄算通过。**
🔴 本机 Homebrew python 是 PEP 668 externally-managed，`pip install blake3` 装不进去。
   OsmoNano 批次的 `_manifest.csv` 因此 23 行哈希全空，只能按路径认账。

### 4.5 现场坑：读卡时大疆机身过热掉总线

用大疆机身当读卡器，实测顺序读只有 **40 MB/s**，桥接芯片在无风道的机身里发热。
VMAF 搜索要 seek 抽样（随机读），连续跑一小时后**设备直接从 USB 总线掉线**：
ffmpeg 报 `partial file` + `Device not configured`（ENXIO），卷从挂载表消失。

**对策：先顺序拷到本机，再脱卡转码。** 7.74 GB 顺序拷 ~3.5 分钟一次过，
比转码时断续读盘 20 分钟对发热友好得多；顺带在删源片前留了一份可校验的兜底副本。

---

## 5. 2026-08-26 首次在本仓跑回归套件 + 三个仍存在的静默缺陷

> ⚠ **本节是 2026-08-26 的快照，标题里「三个仍存在」已过时**——缺陷 1 已于
> 2026-09-03 修复（见 §6），缺陷 2/3 仍在，且**只在备份引擎 `vcompress.py`**。
> 5.1 的测量结论与 5.5 的边界仍然有效，照读。

`REVIEW_FROM_VCAT.md:140` 挂了三周的「尚未在本仓做真机跑批验证」，这次补上了大半。
**怎么跑写进了 `docs/REGRESSION.md` 末尾**（容器、传文件、为什么 R1 要用 diag 工具），
下次别再重新摸索一遍。

### 5.1 结论：测量与闸门逻辑是**准的**

R1、R2 **逐位复现了 2026-08-02 的每一个数**，24 天后、换成本仓代码、在同一台群晖上：

- R1：`IMG_2150` QP20=99.35 / QP1 对照=99.99 / 帧数 360:360；
  `IMG_2151` QP20=49.51 / QP1 对照=49.57 / 帧数 **359:369**
- R2：QP28 三窗口 92.11/46.24/91.69 → Middle 剔除 → 剩余 min 91.69 仍不达标 →
  **老实走下一档**（「不许放水」断言成立）→ QP26 过闸 → 63.2% / VMAF 96.13
- R6 ✅ 6/6；R8 ✅ 且**做了变异验证**（注入「回写原片」被 sha256 当场抓出）

🔴 **RED-LINES §5「两把尺子」依然未修**，359/369 的签名原样健在。这是已知的，
不是这次跑坏的；当前靠 §3 近无损对照剔除坏窗口兜底，兜得住。

⚠ **R8 的变异体自己全程不报错**，照样打「✅ 转码成功」、照样记账成功 1 个。
原片保护这条闸**只在外部 sha256 校验里成立**，引擎内部没有任何自检。

### 5.2 缺陷 1：`_report.csv` 的跳过类行每轮重复追加（**✅ 2026-09-03 已修，见 §6**）

实测：1 个小文件跑 3 轮 → 报表 3 行。断点续压的守卫是
`if out_path.exists() and size>0`，而**跳过类文件根本不产出产物**，守卫拦不住，
每轮重新走一遍 `process_file_abav1` 并再 append 一次。

真实后果：`~/Movies/压缩报告/_report.csv` 里 **69 个唯一文件被写成 131 行**，
「已是高效HEVC(跳过)」12 个记成 51 个。HTML 仪表盘的 KPI 卡片直接按行数汇总，
于是「扫描了多少」是虚的。**压缩成功的行没被重复**（产物存在→续压跳过），
所以省空间的数是准的——但这是运气，`--preview` 模式（`and not args.preview`
绕过守卫）每跑一次就把整棵树重新追加一遍，而 skill 文档的标准两趟跑法第一趟正是 `--preview`。

🔴 51f819a 修的是 `_manifest.csv` 那一路，report 这一路是同一个形状。
**✅ report 这一路已于 2026-09-03 修（§6 第 6 条），且 09-04 有 `tests/test_report_prune.py`
两个方向都锁着**——不清会重复追加，清过头会抹掉血缘证据。

### 5.3 缺陷 2：`probe_pix_fmt()` 在新版 ffprobe 上把逗号写进 CSV（**仍未修**，只在 `vcompress.py`）

`src/vcompress.py:157` 用 `-of csv=p=0` 取 pix_fmt。容器里的 ffmpeg N-125781
**对同一条命令会多吐一个尾随逗号**（`yuv420p,`），Mac 的 8.1.2 不会。
而 `vcompress.py:1116` 用**裸 f-string** 拼 CSV（不是 `csv.writer`），于是：

```
文件,原MB,新MB,省%,VMAF,CRF/Q,决定,像素格式          ← 表头 8 列
"...IMG_2150.MOV",165.92,53.74,67.6,94.35,26,保留压缩版,yuv420p,->yuv420p   ← 数据 9 列
```

杀伤：`export_report.py` 按位取 `r[7]`，把 `yuv420p` 收下、`->yuv420p` **静默丢弃**——
这一列记录 pix_fmt 变迁的意义就此失效，全程零报错。
下游 `backfill_compress_report.py` 用 `row[:7]`，**侥幸**没被破。
`vcompress_abav1.py` 走 `-of json` + `csv.writer`，**免疫**。

修法方向：`probe_pix_fmt` 取第一个非空 token；`vcompress.py` 那行改用 `csv.writer`。

### 5.4 缺陷 3：`model_hd`/`model_4k` 是死参数，日志报假路径（**仍未修**，只在 `vcompress.py`）

`find_vmaf_models()` 找不到就回落到 `/opt/homebrew/share/model`，然后**穿过 8 个函数签名
从未被使用**——真正的调用是 `libvmaf=model=version=vmaf_v0.6.1`（按版本名加载内置模型，
这是对的、可移植的）。但启动日志会打
`Using VMAF HD Model: /opt/homebrew/share/model/vmaf_v0.6.1.json`，
**在 Debian 容器里报一个不存在的 macOS 路径**。不影响评分，但足以让人在 Linux 上
查 VMAF 问题时查错方向。

### 5.5 T7 的账：**不缺，在 vcat 里**（本节 2026-08-26 更正）

⚠ 本节初稿写的是「T7 第二轮的账仍然缺，需要插盘补」——**那句是错的**，
错在跑去找 vcompress 的运行日志，而运行日志本来就不该当账用。插上 T7 后实测：

- `/Volumes/T7/nas-压缩后/` 下**根本没有 `_report.csv`**，只有 `_manifest.csv`（84KB）
- 那份清单里有一列 `源所在树`（取值 `src-dir` / `tree`）——**这列不是本仓写的**，
  是 `vcat/src/client/vcat/lineage.py:45` 写的，取值就是 `vcat derivation import-tree` 的参数名

**结论：T7 全集 222 条，25.54 GB ➔ 5.14 GB，释放 20.41 GB（省 79.9%），
两侧全量哈希齐全，2026-08-08 由 vcat 登记入账。** 账早就做完了。

同理，本节初稿里「`~/Movies` 那批 34 个文件释放 12.24 GB」也是**我从运行日志算的，
不是台账**。要作数得走 `vcat derivation import-tree`。数字本身可参考
（Q 跨度 30–68 再次印证 §4.1：固定 Q 一刀切不成立），但**别拿它当账**。

### 5.5.1 🔴 边界：台账是 vcat 的，vcompress 不碰

| | 归谁 |
|---|---|
| 转码、元数据保护、**VMAF 测量与闸门**、产物完整性 | **vcompress** |
| 台账、血缘、「这棵树里有什么 / 省了多少」 | **vcat** |

**别拿一次性脚本解析 `_report.csv` 去算总账。** 那是把台账逻辑在本仓又实现一遍，
正是 `REVIEW_FROM_VCAT.md` §7.1 警告的「三份副本分叉」。

而且那份文件本来就不可信——§5.2 实测它会重复追加行（69 个文件写成 131 行）。
vcat 早把这事想清楚了，`derivation import-tree` 的参数说明白写着：

> `--report` 归档的 `_report.csv`，**只用来补 VMAF/Q（配对与哈希一概不信它）**

要算一棵树的账，用 vcat：

```bash
vcat derivation import-tree --tree <压缩后目录> --src-dir <原目录> [--report <归档的 _report.csv>]
```

**且应该在 vcat 那个会话里跑**——`~/Projects/vcat` 是 AGPL，本仓 MIT，别从这边调过去。

### 5.6 R3 / R4 / R5 已收口

- **R3 ✅** `IMG_1933` QP28 三窗口 78.64/17.83/29.33（与文档一字不差），
  报表写「需人工检查(测量不可信: 近无损对照未过关)」，**没出现「画质不达标」**。
- **R4 ✅** VOB 起始窗口落在 15s，QP28=89.84，没有 16.45 假低分。QP24 过闸，省 47.3%。
- **R5 ⚠ 断言过，但文档期望值要改**：产物有音轨（`mp2` 2ch），音轨没被丢——这条守住了。
  但期望的 `aac` 对不上：产物 `VTS_01_2.hevc.VOB` **沿用源扩展名**走 MPEG-PS muxer，
  `-c:a copy` 直接成功，「MP4 muxer 拒收→退 aac」那条路**根本没被触发**。
  详见 `docs/REGRESSION.md` 末尾，那里记了两条改法和一个待定问题
  （HEVC 封进 `.VOB` 的播放兼容性，本次未评估）。
- **R8 ✅** 三个真机样本跑完后 sha256 全部零改动。

**测试产物待清理**：`/volume1/docker/vcat/regression/out-R*/`（约 1.2GB，R45 产物 540MB），
以及推上去的 `/volume1/docker/vcat/vcompress-repo/`。

### 5.7 环境备忘

- **Tailscale 默认是停的**，`ssh inas` 会报 `Connection closed`（不是密钥问题）。
  先 `/Applications/Tailscale.app/Contents/MacOS/Tailscale up --accept-routes`。
- **scp 到群晖会被拒**，传文件用 `ssh inas 'cat > 目标' < 源`。
- macOS 上**没有 `timeout` 命令**（非 GNU coreutils），别在脚本里用。
- blake3 仍未装上（§4.4 的 PEP 668）。`brew search blake3` 那个 formula 是 CLI/库、
  不是 Python 模块。可行：venv/pipx 装 `blake3`，或改用标准库 `hashlib.blake2b`——
  但后者**改变清单哈希口径**，`_manifest.csv` 要和 vcat 血缘对账，换算法得两边一起改。

---

## 6. 2026-09-03 跨两仓审计整改 + 2026-09-04 单元测试套件

### 6.1 引擎这边改了什么（对应 vcat KANBAN Epic 13）

一次跨 vcat / vcompress 两仓的全量审计。**引擎侧六处**，全部已在
`src/vcompress_abav1.py` 里：

| # | 改了什么 | 为什么要命 |
|---|---|---|
| 1 | `quality_rejected` 真的发出去了 | 老版 `status.startswith("skipped")` 里再判 `status == "failed"` —— **恒假**，闸门拒绝与编码失败一起被下游当 `failed`，而工人对 `failed` 是「记终态不重试」→ **真正的机器故障永远不会被重做** |
| 2 | 编码失败时 unlink 半成品 | 不删，下一轮续压守卫（存在且非空→跳过）会把**没过闸门的残片**当成压好的产物 |
| 3 | unlink 前加 `samefile` 守卫 | 红线 1 的纵深防御：`-i x.mp4 -o x.mp4` 这种误用会让那一刀落到原片上 |
| 4 | 续压跳过分支补发 JSON | 老版「退出码 0 + 什么都不写」，调用方只能猜，于是把残片记成 success |
| 5 | 产物像素格式实测入账（第 8 列 `源->产物`） | 补 ADR-019 决策 2/3 欠的那一半，详见 vcat ADR-027。探不出来写 `源->?`，**不回落成单值**（单值和「测了没变化」长得一样）|
| 6 | 报表旧行清理（临时文件 + `os.replace`）| 缺陷 1。⚠ 第一版**修过头**，见下 |

### 6.2 🔴 这轮最该记住的两条

**① 四条真缺陷是 `redline-reviewer`（独立上下文、没参与那轮对话）在提交后抓出来的。**
其中一条是宣称修好实际没修，一条是那轮**自己引入的新回归**：`--preview` 会把
「保留压缩版」行清掉——那些行带着 VMAF/CRF 与第 8 列实测像素格式，是 vcat 血缘
登记 `--report` **唯一认的行**，且 preview 重新生成不出来（它写 `PREVIEW成功`）。
**而对压完的树跑 preview 正是 skill 标准两趟跑法的第一趟。**
→ 自检与被检同源就是查不出来。

⚠ **如果 NAS 上某份存量 `_report.csv` 已经被那一版 preview 洗过，证据找不回来了。**
上机看一眼有没有「只剩 PREVIEW成功、没有保留压缩版」的报表。

**② 引擎（本仓）与工人（vcat `contrib/mac_mini_worker`）分处两仓、无版本握手，
而这次的修复是配对的。** 🔴 **Mac mini 上那份 `~/vcompress/src/vcompress_abav1.py`
必须一起更新**，只更一边会退回旧行为。开跑前对一次哈希——真机上两台机器的引擎
曾长期对不上（md5 `14860d1f` vs `21ef2149`）。

**2026-09-04 查清**：`14860d1f` = 本仓 `73d3ca2`（2026-08-07），此后引擎又改了 4 次
（`51f819a` / `a4ea6f4` / `280e309` / `ac16bbc`）；`21ef2149` 在历史里根本没有，
是当时本机的未提交状态。**所以 Mac mini 很可能落后 4 个提交、缺 S28 全部六处修复**
（推断——那台机器当时够不到，未证实）。
🔴 **别往文档里抄裸哈希**：下次提交就静默过期，而它长得依然像个准确的值
（`fullset.py 26ab6420` 过期 12 天没人发现；同日我还把一个 sha256 记进 md5 的表，
两重错，由红线复检当场抓出）。**要对就两边现算 `md5 -q`，要记就记 git 提交号。**

### 6.3 单元测试套件（2026-09-04 建）

**背景**：本仓 4227 行代码、**零自动化测试**，上面那四条真缺陷没有任何东西在跑。

`tests/` 93 条，只锁「**账怎么记**」那一层的纯函数——不碰一个字节的真视频，
不调 ffmpeg/ab-av1/exiftool，毫秒级。覆盖：命名契约（跨仓，ADR-020）·
`json_status_for` 四类结论分派 · 报表清理两个方向（不清=重复追加 / 清过头=抹证据）·
`pix_column` · `_manifest.csv` 读写与搬运计划。

为此在 `vcompress_abav1.py` 做了**三处行为等价的提取**（把内联在 `main()` 里的
逻辑搬成模块级函数）：`pix_column` / `json_status_for` /
`report_prune_targets` + `prune_report_rows` + `prune_report_csv`。
⚠ 提取时曾把一处行为改掉（preview 下丢弃列数不足的畸形行），**由 3000 组随机
对照当场发现并改回**——旧行为保留畸形行是对的，静默丢掉又是一次「把数据丢失
说成排版瑕疵」。

**11 次变异验证 11 次全部当场被抓**（8 次引擎 + 3 次命名契约）。
跑法与那个字节码陷阱写在 `docs/REGRESSION.md` 开头。

---

## 7. 2026-09-05 复查五修：把「机器坏了」从「素材压不动」里拆出来

起因是一次「本项目还有什么可以优化的」的通读复查。结论不是性能，是**四条静默错账**，
外加一条需要你拍板的定位问题（§7.6）。测试 **91 → 152 passed**，全部做了变异验证。

⚠ **顺带更正**：§6.3 写的「`tests/` 93 条」与实测对不上——2026-09-05 在 HEAD（d0f295e）
上现跑是 **91 passed + 1 skipped + 3 xfailed = 95 collected**。数字本身不影响任何结论，
但它正是「抄下来就静默过期」的又一例：**要引就现跑一次 `pytest`，别抄。**

### 7.1 A：`ab-av1 跑挂了` 一直被记成 `素材压不动` —— 终态，工人永不重试

老 `run_ab_av1_search` 用 `subprocess.run(..., check=True)`，任何非零退出都抛
`CalledProcessError` → 返回 `None` → 调用方一律记 `quality_rejected`。
而工人对 `quality_rejected` 是「素材结论、终态、不重试」。

于是 **/tmp 满、ab-av1 没装、ffmpeg 崩、读卡器过热掉 USB 总线（§4.5 真机出过）**，
剩下整批都会被永久写成「未过质量门限(VMAF<93)」，而且没有任何人会去重跑它们。

🔴 **这是 S28 修复 #1 的同一形状，只是上移了一层。** 那次修的是
「`quality_rejected` 恒假、发不出去」；这次发现**上游根本没把两件事分开**。
红线 2 的原话：「结果枚举里没有笼统的 `failed`」——那条红线在生产引擎的入口处就漏了。

**实测（ab-av1 0.11.5，两种情形都 exit 1，判别器只在 stdout 里）**：

| 情形 | exit | stdout |
|---|---|---|
| 文件不存在（机器故障） | 1 | **一行 JSON 都没有** |
| 素材达不到目标（素材结论） | 1 | 末行 `{"type":"crf-search-error","message":"Failed to find a suitable crf"}` |

`check=True` 在看 stdout 之前就把这个判别器抛掉了。现在 `classify_search_output()`
归三类：`found` / `rejected`（终态）/ `broken`（可重试）。
⚠ **拿不准一律 `broken`**：白重跑一次 ≪ 永久静默写错账且永不重试。

端到端验证（拿 shell 桩替掉 ab-av1）：
```
崩溃桩 → {"status": "failed",           "detail": "质量搜索未跑成(机器故障)"}
拒绝桩 → {"status": "quality_rejected", "detail": "未过质量门限(VMAF<90.0)"}
```
修之前两者都是后者。

### 7.2 B：成功行的判据太松（修 A 时会当场引爆）

老判据是「反向扫第一个带 `crf` 的行」。**中间探测行 `sample-encode-done` 同样带 crf**，
实测一次搜索里混着 `vmaf 63.36` 和 `predicted_encode_percent 156%`（压完反而更大）
这种没过闸门的档位。老代码只因 `crf-search-done` 恰好排在最后才碰巧正确。

🔴 **修 A 的顺序陷阱**：若顺手去掉 `check=True` 而不先收紧判据，失败输出的倒数第二行
正是那个 156% 的探测点——会拿一个没过闸门的 CRF 去压全片，并把那个 VMAF 当实测值
写进台账。判据已改为 `type == "crf-search-done"`。

### 7.3 C：产物原子落位（解法本来就写在代码注释里）

ffmpeg 用 `-y` 直接写最终路径，失败靠一段 `unlink` 收尾——**可那段代码只在 python
还活着时执行得到**，而最需要它的恰恰是进程被杀 / 断电 / SMB 掉线连带 python 一起死
的时候。残片留在最终路径上，续压守卫（存在且非空）下一轮把它当成压好的产物。

改为：编码写隐藏临时名 `.X.hevc.partial.mp4` → 元数据 → 探像素格式 → `os.replace` 就位。
- ⚠ **标记必须插在扩展名之前**，扩展名原样留末尾——ffmpeg 按它挑 muxer（R5 那个
  `.VOB` 产物就靠这个走 MPEG-PS）。
- 前导点让半成品对 `fullset.walk_rel` 与 `_apply_replace.py` **结构性隐身**。
- **连带解决**注释里记的那 1010 个群晖回收站中间版（40GB）：exiftool 的 rename
  现在发生在隐藏临时名上。
- 🔴 **顺手补了 `uchg` 摘挂**：`shutil.copystat` 会连 `st_flags` 一起复制，源片带
  Finder「已锁定」时产物就继承了不可变标志，**之后任何改名都 EPERM**。
  `fullset.apply_moves` 的注释记着同一个坑（真机 133 个微信导出视频）。做法与那边一致：
  临时摘掉、落位后原样贴回。**这一条不补，带锁的源片会在就位这一步全批失败。**

### 7.4 D：红线 2「元数据无损」以前只是一条日志

`preserve_metadata()` 的返回值**被丢弃了**。exiftool 失败只打一条 `logger.error`，
随后照样返回「保留压缩版」、照样记 success、照样进 `_manifest.csv`——而
`_apply_replace.py` 会据此**删掉源片**，那份 EXIF 就永久没了。

现在：失败即丢弃产物、原片不动、记**可重试**的 `failed`（决定 `元数据继承失败`）。
逃生口 `--allow-metadata-loss`（**默认关**），与 `vcompress.py` 的
`--allow-bitdepth-downgrade` 同一个形状。

### 7.5 E：preview 把预估当实测记账

`--preview` 的内部 status 也是 `"success"`，`new_mb` 却是 ab-av1 的**预估值**。
老代码把两种 success 累进同一个 `total_saved_mb`，于是收尾打印「累计释放磁盘空间 X GB」
——一个**从没发生过的释放量**。而 skill 标准两趟跑法第一趟正是 `--preview`，
所以这个假数字每次评估都会出现，且长得跟真账一模一样（§5.5 同一形状）。

同一个判断还管着另外两处，都一并修了：
- `--json-out` 不再给一个不存在的产物报 `out_size`
- **`_manifest.csv` 不再被 preview 覆写**：preview 绕过续压守卫，对一棵**已压完**的树
  跑 preview 时 `out_path` 是存在的，老代码会拿本轮的**预估 VMAF** 覆写那一行，
  给真产物盖上一个从没实测过的分数。**清单是账，账上不许出现预估值。**

真机验证（对已压完的树跑 preview）：`保留压缩版` 行原样健在、清单一字未动、无残片。

### 7.6 🔴 待你拍板：`vcompress.py` 的定位可能一直是反的

它被叫「备份引擎」，TODO 里三项（修缺陷 2、修缺陷 3、给它铺测试）都按低优先级压着，
理由是「生产那条路免疫」。但实测三条红线守卫的归属：

| 守卫 | `vcompress.py` | `vcompress_abav1.py`（生产） |
|---|---|---|
| 红线 3 近无损对照 / `measurement_unreliable` | ✅ `:815` | ❌ **状态枚举里根本没有这一类** |
| 红线 10 aac 退避 | ✅ `:847` | ❌ 直接记「编码失败」 |
| 红线 11 位深降级默认拒绝 | ✅ `:688` | ❌ 只记录，不拒绝 |

公平地说：红线 4 明写「ab-av1 正是这么做的」（无损流拷贝取窗），所以红线 5「两把尺子」
的根因在生产路上**结构性地不存在**，红线 3 的兜底需求确实小得多。
但 10/11 跟测量路径无关，生产引擎是裸的——而 `--skip-hdr` 默认 `False`、
HEVC 只在 ≤30Mbps 时跳过，**10bit 高码率素材是能走进去的**。

真正该决策的不是「要不要给备份引擎铺测试」，而是**「这两条守卫要不要移植进生产引擎」**。

### 7.7 测试：93 → 152 条，四个新文件

`test_search_classify.py`（15）· `test_atomic_placement.py`（16）·
`test_metadata_gate.py`（8）· `test_preview_accounting.py`（22）= 61 条新增。

- 夹具 `STDOUT_*` 是 **ab-av1 0.11.5 真机实跑抓下来的原样输出**，不是手写的形状。
- `conftest.py` 新增 `bench` / `make_args` / `leftovers`：把四个外部调用 monkeypatch 掉，
  `process_file_abav1` 的**编排逻辑**（谁失败了丢弃什么、记哪个状态、盘上留不留东西）
  就成了毫秒级可测的纯逻辑——这轮修的四件事全在这一层。
- 决定字符串改为**引擎导出常量**（`DECISION_KEEP` / `DECISION_SEARCH_BROKEN` /
  `DECISION_METADATA_LOST` / `DECISION_PLACE_FAILED`），测试 import 它而不是抄副本。
  抄一份写死的字符串去比对**等于自己跟自己核对**——d0f295e 打回过两条这样的测试。

> 🔴 **下面这段自评已被 2026-09-06 的红线复检推翻，按 §8 的数字算。**
> 当时的 14 个变异体**全部打在判据层**（提取出来的纯函数），
> 接线层一个都没打——而 bug 恰恰住在接线里。**别照抄这个数字。**

**14 个变异体，13 个当场被抓**：非零退出一律判素材结论 · 判据放宽到「带 crf 就算」 ·
无视退出码 · preview 也算真产物 · 临时名把扩展名甩到末尾 · 临时名不带前导点 ·
删半成品时不查是不是原片 · 就位时不摘 uchg · 丢弃 `preserve_metadata` 返回值 ·
元数据失败不丢弃半成品 · 逃生口默认放行 · 编码失败不清残片 · 就位失败不清残片。

⚠ 其中「编码失败不清残片」**第一轮是漏网的**——补了
`test_encode_failure_leaves_nothing_behind` 才抓住。变异验证的价值正在这里：
不做这一步，那条失败路径会一直没人管着。

⚠ **第 14 个至今漏网，如实记下**：去掉「开压前先清上一轮遗留半成品」那行，整套测试照样绿。
因为 ffmpeg 带 `-y`、夹具的 fake_encode 也一样，临时名本来就会被覆写。
**那行是冗余防御，不是闸门**，引擎里已如实标注。别以为它锁着什么。

### 7.8 这轮**没做**的

- **§7.6 的移植** —— 等你拍板
- **报表行键 `.replace(',', ' ')`** —— 跨仓契约（vcat `lineage.load_report` 按这个键配对），
  两边一起改才安全。已进 TODO
- **`--json-out` 的 JSON 与日志混在同一个 stdout** —— `logging.basicConfig` 挂的是
  `StreamHandler(sys.stdout)`，而代码注释写「与 logger 写 stderr/文件分离」，**注释与实现不符**。
  09-05 实测：工人得从 stdout 里捞那行 JSON。改动会动跨仓契约，先确认 vcat 侧怎么解析的
- **日志散落无轮转 / `check_dependencies()` 在 argparse 之前 / `--temp-dir /tmp` 硬编码**
  —— 三件小毛病，已进 TODO
- **真机 R1~R8 未重跑** —— 这轮全部验证都在本机（152 条单元 + 一次真编码冒烟 +
  ab-av1 桩的端到端）。⚠ **单元套件验「记得对不对」，R1~R8 验「测得准不准」，
  顶替不了**。C 改动了产物落盘路径，下次上群晖建议重跑一遍 R6/R8

---

## 8. 2026-09-06 红线复检打回：判据锁住了，接线没有

§7 那轮改完，交给 `redline-reviewer`（独立上下文，**没参与那轮对话**）复检。
它当场演示了五个「变异全绿」，其中一个**精确还原了 §7.1 声称修好的那个 bug**：

```python
if kind == "broken":        →    if False:        # 152 条测试全绿
```

### 8.1 🔴 这轮最该记住的一条

**给一个纯函数写测试，锁住的只是那个纯函数。**

§7 为了可测性做了三处提取（`classify_search_output` / `counts_as_real_product` /
`pix_column` 那一族），然后给每个提取物配了十几条测试，14 个变异体全打在它们身上，
13 个被抓，于是宣称锁住了。**但提取这个动作本身制造了一段没人看的接线**——
判据从此住在纯函数里，而「判据有没有被用上」住在 `main()` 和 `process_file_abav1` 里，
那两处零覆盖。

原来的 bug 从来不在「分不分得出来」，在**「分出来之后用不用」**。
所有五条漏网都在这一层：

| 漏网的变异 | 后果 |
|---|---|
| `if kind == "broken":` → `if False:` | 机器故障重新被记成素材结论、永不重试（**就是要修的那个 bug**）|
| `--allow-metadata-loss` 默认改 `True` | 红线 2 的闸门退回成一条日志 |
| `DECISION_KEEP` 改名 | vcat 一条血缘都收不到；preview 反过来把成功行**全删光** |
| `main()` 的 preview 分支折掉 | 预估重新计入实测 |
| `REPORT_HEADER` 前六列换序 | vcat `backfill` 按位解包，静默错位 |

### 8.2 我写的两条假测试（同一个形状，仓里刚打回过一次）

- `test_the_escape_hatch_is_off_by_default`：**自建一个 argparse** 再断言它的默认值。
  `action='store_true'` 的默认永远是 False——**那条断言物理上不可能失败**，
  且从没碰过 `main()` 的 parser。
- `test_failure_decisions_are_all_retryable(decision)`：参数化了三次，
  **函数体一次都没用 `decision`**，只重复断言一个常量。

d0f295e 打回的就是「写死一份副本跟自己核对」，我隔一天又犯了两次。
🔴 **判据：一条测试若不能指出「哪一行生产代码改了它会红」，它就不是测试。**

### 8.3 🔴 待拍板：preview 在跨仓契约上没有落脚点

vcat `src/client/vcat/compress_worker.py:264` 会同时下发 `--json-out --preview`，
而 `:539` 是 `if out_size is None or vmaf_mean is None ... → result: "encode_failed"`。

| | 本仓发什么 | vcat 记成 |
|---|---|---|
| §7 改之前 | `success` + **预估** out_size/VMAF | 假 `done`（给不存在的文件记了体积和分数）|
| §7 改之后 | `success` + `out_size: null` | 假 `encode_failed`（把评估成功说成机器坏了，理由还是编的）|

**两个都错。** 🔴 改的是**本仓发什么**，vcat 侧别动（红线 5：台账是 vcat 的）。
两个方向：给 preview 一个独立 status（要 vcat 配合分派），或保留 out_size
另加 `preview: true` 标志（vcat 不改也能先不崩）。**没定之前别用 `--preview --json-out` 跑批。**

### 8.4 修了什么（195 passed，23 个变异体抓住 22）

- **接线层测试** `tests/test_dispatch_wiring.py`（10 条）：三类判据真的分头处理 ·
  `main()` 的 preview 分支 · `--json-out` 的产物体积 · 收尾日志的**数字**
  （⚠ 初版只断言那句话在不在，变异体打「预估可释放 **0.00 GB**」照样绿）
- **两条假测试**换成打真 parser / 真调用链的
- `DECISION_KEEP` 真正做到单一出处（`prune_report_rows` 原先还硬编码着字面量），
  并加断言把它钉死在跨仓字面量 `保留压缩版` 上
- `REPORT_HEADER` 改钉**全序**，不是抽查 `[6]`/`[7]`
- **带 uchg 的半成品删得掉了**：`discard_partial` 先摘锁再删。原先就位失败后
  会把 `UF_IMMUTABLE` 贴回 tmp，紧接着的 unlink 必然 EPERM，**隐藏残片永久留下**
  ——而继承 uchg 的正是「133 个微信导出视频」那批人群
- `place_atomically` 补 samefile 守卫（`os.replace` 覆写比 unlink 更不可逆，
  那边设防这边不设是双重标准）
- **孤儿半成品清扫**：原子落位把残渣挪成了隐藏文件，**三本账都看不见它**。
  若只在编码步里清，一个几 GB 的残片会因为「下一轮这个文件走了小文件/低码率/
  已是HEVC/闸门拒绝任一条提前返回」而永久留下。现在挪到每文件循环最前面
- **台账三色**：`export_report.py` 原先是 `保留压缩版 ? succ : skip` 二分，
  于是三个机器故障决定与「已是高效HEVC(跳过)」**同色同标签**——引擎里刚把
  红线 2 拆开，台账里又并回去。新增 `tag-fail`
- `discard_partial` 那条完全静默的 `except OSError: return` 加了日志
- `partial_path` 的 docstring 收窄：前导点只对 `walk_rel` 与 `_apply_replace.py`
  隐身，**引擎自己的 `rglob('*')` 不跳点文件**（实测）

### 8.5 顺带查清的两件事

**① `vcompress.py` 是 Linux 测量基准，不是「备份」。** `docs/REGRESSION.md:171`——
R1~R8 真机套件跑的就是它（群晖 Debian 容器，`-e vaapi`）。两个引擎是**按平台分工**，
和 `CLAUDE.md` 里已经立过的 `src/` vs `win/` 完全一样的形状。**退役它会把回归套件弄死。**

**② 红线 3/10/11 的守卫实测都不需要移植进生产引擎**，但三条成立的理由**都是"碰巧"**：

| 红线 | 为什么撞不上 | 靠什么撑着（改了就塌）|
|---|---|---|
| 3 近无损对照 | 根因（两把尺子）在 ab-av1 上结构性不存在 | **ab-av1 的取窗设计**（R10 盯着）|
| 10 aac 退避 | 产物沿用源容器，`-c:a copy` 装得下 | **ADR-020 的命名约定**（`test_container_contract.py` 盯着）|
| 11 位深守卫 | `hevc_videotoolbox` 实测保住 10bit | **ffmpeg 的格式协商**（R9 盯着）|

已加 `docs/REGRESSION.md` 的 **R9**（生产引擎不降位深，附 09-06 实测表）与
**R10**（ab-av1 取窗是无损流拷贝），以及 `tests/test_container_contract.py`（23 条）。

🔴 **`RED-LINES.md §11` 那句「10bit 现在撞不上，只因为 HEVC 默认跳过」推理是错的**
——HEVC 只在 ≤30Mbps 时跳过，`--skip-hdr` 默认还是 False，10bit 高码率素材照样走进去。
结论碰巧对（videotoolbox 保住了），但理由要改。**没动那份文件，等你拍板。**

**③ `-e vaapi` 从生产引擎摘掉了**：`run_ab_av1_encode` 对 `hevc_vaapi` 落进 `else`
分支，只给 `-q:v`，**没有 `-vaapi_device`**，硬件编码器根本起不来。
一个「看起来支持、其实跑不了」的选项比没有更坏。vaapi 那条路 `vcompress.py` 已正确实现。

### 8.6 又踩了一次字节码陷阱

`docs/REGRESSION.md` 开头白纸黑字写着「跑变异验证前先删 `__pycache__`」，
我照样踩了：`REPORT_HEADER` 换序是**等长替换**，同秒内写入+还原让 Python 认为
`.pyc` 仍然有效，还原之后测试仍在报错。
🔴 **变异驱动脚本现在每次变异前后都清缓存**
（`scratchpad/mutate.py`，23 个变异体一次跑完并按层分类）。
⚠ 同一个陷阱的另一个方向更危险：**旧 `.pyc` 被服用会让变异不生效，制造假绿**。

### 8.7 剩下没做的

- **§8.3 的 preview 契约** —— 等拍板
- **`RED-LINES.md §11` 的错误推理** —— 那份文件你标了「比代码重要」，没动
- **真机 R1~R8 未重跑**：本轮全部验证在本机（195 单元 + 真编码冒烟 + ab-av1 桩端到端 +
  孤儿清扫冒烟）。C 改了产物落盘路径，**下次上群晖务必重跑 R5/R6/R8**
- 一个**等价变异体**漏网（`prune_report_rows` 里用字面量替 `DECISION_KEEP`）——
  两者语义完全相同，不是覆盖漏洞；「改名」那条变异是被抓住的


---

## 9. 2026-09-06 下半场：preview 独立 status · 红线 §11 更正 · 群晖核实

### 9.1 preview 有自己的 status 了（跨仓契约，**vcat 侧待改**）

`--json-out` 现在发第五类 `preview`，实测字段一律 null，预测值另放 `predicted` 块：

```json
{"status":"preview","out_path":null,"out_size":null,
 "vmaf_mean":null,"vmaf_min":null,"crf_val":51.0,"detail":"PREVIEW成功",
 "predicted":{"out_size":4996609,"vmaf":90.52,"saving_pct":81.7}}
```

对照真跑完那条：`status:"success"` · `out_size:5264014` · `vmaf_mean:90.52` · **无 `predicted` 块**。

内部 status 也从 `"success"` 改成了 `"preview"`——让它顶着 success 走完全程，
下游每一层都要重新判一次「这个 success 是真的吗」，而漏判一层就多一处把预估记成实测的账。

🔴 **vcat 侧要加一支分派**（`src/client/vcat/compress_worker.py:539` 那个
`out_size is None → encode_failed` 之前）。**在 vcat 那个会话里改，别从这边动**
（红线 5：台账是 vcat 的；`~/Projects/vcat` 是 AGPL，本仓 MIT）。
没改之前 preview 会继续被记成 `encode_failed`——**响且安全**，不会再悄悄污染台账。

变异 5 个抓住 5 个。⚠ 其中「preview 内部状态退回 success」第一轮**漏网**：
`main()` 那批测试喂的是**罐装返回值**（`process_file_abav1` 被 monkeypatch 顶掉了），
锁不住产生罐头的那台机器。补了三条直接打 `process_file_abav1` 的才抓住。
**这是 §8.1 那个教训的又一个变种：夹具顶掉谁，谁就没被测。**

### 9.2 `RED-LINES.md §11` 补了 11.1（**上一轮我说「推理是错的」，说重了**）

那句「10bit 现在撞不上，只因为 HEVC 默认跳过」对 **`vcompress.py` 是成立的**：
它 `if codec in ['hevc','h265'] and not args.recompress_hevc` **无条件跳过**。
真正的问题是**那段话没说自己在讲哪个引擎**——`vcompress_abav1.py` 没有
`--recompress-hevc`，判据是码率（`bitrate_mbps <= --max-hevc-bitrate`，默认 30Mbps），
**10bit HEVC 高码率不需要任何开关就会被重压**。

已补 §11.1：分引擎讲清楚 + 09-06 的 10bit 实测表 + 指向 R9。**原文一字未删。**

### 9.3 群晖：连得上，但**这轮根本不需要跑 R1~R8**

- ✅ `ssh inas` 通（Tailscale 是**开着**的——§5.7 说「默认是停的」，至少今天不是）。
  DS918+，已连续运行 25 天。容器 `vcat-media` 在，样本 `IMG_2150/2151.MOV` 在，
  回归残留 **662M**（TODO 记的 ~1.2GB 已经小了）。
- ⚠ **Mac mini 在 Tailscale 上离线 36 天**（`7plus-mac-mini`，last seen 36d ago）。
  TODO 第一条「更新 Mac mini 引擎」暂时够不着那台机器。

🔴 **上一轮我说「务必重跑 R5/R6/R8」，那是错的。** 查清了：

| | 跑的是哪个引擎 | 我这两轮改了吗 |
|---|---|---|
| R1~R8（群晖容器，`-e vaapi`） | **`vcompress.py`** + `diag_vmaf_window.py` | ❌ 一个字没动 |
| 我的全部改动 | `vcompress_abav1.py`（Mac）+ `export_report.py` | —— |

而且 **`vcompress.py` 根本不 import `export_report`**（它自己拼 CSV，那正是缺陷 2）——
推送清单里的 `export_report.py` 是**残留项**，容器里连 openpyxl 都没装，
09-06 实测 `import openpyxl` 直接 ModuleNotFoundError。所以那份文件在群晖上从来没跑过。

**结论：本轮改动在群晖侧零暴露，不需要重跑真机套件。**
（已把最新 5 个文件推上去了，下次跑 R1~R8 时是新的。容器 python 3.13.5。）

### 9.4 剩下的

- **vcat 侧加 preview 分派**（§9.1）—— 在那个会话里做
- **清 662M 回归残留** —— 在 TODO 上，删是不可逆的，等你点头
- **Mac mini 离线 36 天** —— 引擎更新这件事得等它上线
- 一个**等价变异体**漏网（`prune_report_rows` 用字面量替 `DECISION_KEEP`，语义相同）

---

## 10. 2026-09-14 T7/9.home 346GB 批处理实战与三项核心工程经验

在针对移动硬盘超大规模真实家庭视频库（`/Volumes/T7/9.home`，共 2,445 个视频、346.14 GB）的通宵全量压制实战中，沉淀了以下核心经验与防御规则：

### 10.1 小文件门限性价比（ROI）与真实数据分布规律

用户疑问：**10MB~20MB 的视频是否有必要压缩？**
通过对 346.14 GB 真实家庭影像做全量分档统计，得出客观数据支撑：

| 档位 | 文件数 | 占总数量 | 总容量 | 占总容量比 | 潜在释放极限 | 运算成本 (CPU) | 性价比结论 |
|---|---|---|---|---|---|---|---|
| `< 10MB` | 465 | 19.0% | 1.97 GB | 0.6% | ~1.4 GB | ~12 小时 | **坚决不压**（大多是微信超低码率废片） |
| `10~20MB` | 249 | 10.2% | 3.58 GB | 1.0% | ~2.68 GB | ~7 小时 | **首轮默认跳过**（ROI < 1%，边际递减） |
| `>= 20MB` (主力) | 1,731 | 70.8% | 340.59 GB | **98.4%** | **~260 GB+** | 主力耗时 | **核心战场**（空间贡献率 98.4%） |

**实战规则与结论**：
1. **20MB 黄金门限坚不可摧**：
   - 20MB 以上视频占据了 98.4% 的存储空间。
   - 若下调门限至 10MB 压制那 249 个小视频，最多只能多挤出 **2.68 GB**（仅占总释放空间的 1%），却需要多烧 **6~8 小时** 满负荷 CPU，且极易因微信二次重压欠码率而跌破 VMAF 门限导致无效计算。
2. **两阶段最佳作业范式**：
   - **第一阶段（主战场）**：统一使用 `--min-file-mb 20.0` 快速拿下 98.4% 的核心红利（释放 260GB+）；
   - **第二阶段（按需补漏）**：若用户有绝对归档强迫症，首轮结束后执行 `--min-file-mb 10.0`，依靠**断点续压**秒级跳过已有产物，单独耗时几小时定向收割小文件。

### 10.2 长耗时跑批必须加 `caffeinate -dims` 防掉盘

- **真机事故**：Mac 闲置时会自动进入空闲休眠（Sleep）。macOS 在休眠状态下会切断或降低外接 USB 端口供电，导致 T7 等外接移动固态硬盘（SSD）从总线瞬时卸载/掉盘。
- **后果**：任务在处理第 690 个文件时，因 `_report.csv` 写入时盘已离线，抛出 `FileNotFoundError: [Errno 2] No such file or directory` 中断。
- **红线规则**：**凡在 macOS 上针对外接硬盘进行的大规模跑批任务，严禁用裸 python 命令启动，必须包裹系统原生防休眠工具**：
  ```bash
  caffeinate -dims .venv/bin/python src/vcompress_abav1.py ...
  ```
  （`-d` 防屏幕休眠，`-i` 防系统闲置休眠，`-m` 防磁盘空闲休眠，`-s` 接交流电时保持系统苏醒）。

### 10.3 动态源文件防御与 AppleDouble (`._*`) 虚假分支文件过滤

- **动态文件守卫**：
  - 跨天长任务启动时，`files = [p for p in rglob(...)]` 将数千个文件一次性载入内存；
  - 若在长达 3 天的运行过程中，用户在 Finder 中整理、重命名或删除了某个源文件，后续循环到该文件时 `stat()` 会抛出 `FileNotFoundError` 导致整个跑批崩毁。
  - **修复**：在 `process_file_abav1` 入口及 `main()` 文件循环首部均加入 `if not file_path.exists():` 守卫，遇缺失文件打 WARNING 并安全跳过，坚决不让任务崩退。
- **AppleDouble 过滤**：
  - macOS 在 ExFAT/FAT32 卷上写入文件时，会自动创建 4KB 的 `._*` 属性分支文件（如 `._video.mp4`）；
  - 这些文件扩展名也是 `.mp4`，会被 `rglob` 误当成视频读取而引发探测失败。
  - **修复**：文件探测增加 `and not p.name.startswith('._')` 强过滤。
- **测试覆盖**：
  - 补全 `test_missing_src_file_returns_failed_safely` 单元测试，全套套件升至 **206 passed**。

### 10.4 存量 HEVC 定向二次重压与 `--file-list` 精准作业

- **实战背景**：首轮跑批中跳过了 278 个已是 HEVC 的视频（默认 `--max-hevc-bitrate 30.0`）。排查发现 iPhone 录制的 1080p HEVC 码率常在 8~14Mbps，存在显著压缩空间。
- **功能增强**：引擎新增 `--file-list <path>` 参数，支持从外部相对路径文本清单精准下发任务，完全绕过全树重新扫描，且 `prune_report_csv` 仅精准替换清单内旧行，不误伤其他已处理文件。
- **方案 A 战果**：
  - 提取 132 个 >50MB 的大体积 HEVC 视频（总容量 12.96 GB），以 `--max-hevc-bitrate 5.0 --vmaf-min 93.0` 定向压制；
  - **132 个中成功压缩 122 个（成功率高达 92.4%）**，仅 10 个因画质门限保护跳过，0 失败；
  - 单轮**额外多释放磁盘空间 `8.22 GB`**；
  - 整体目录累计释放空间升至 **`117.24 GB`**，保留优质压缩版达到 **928 个**。

### 10.5 全量转码真实节省率落地兜底闸门（防切片抽样失真与低效产物清理）

- **实战坏例与痛点**：
  - 用户在审视压缩结果时发现部分视频（如 `win/1.bd-good/韩国演艺圈卖淫偷拍悲惨事件1-15系列/14/1.wmv`）压缩后与原片体积相差无几（如仅省 12.9%），甚至部分样本体积反而反向膨胀（增大 10%~28%）。
  - **根本成因**：`ab-av1` 默认仅抽取 3 段 15s 切片进行 VMAF/CRF 二分评估。若切片片段动态度低，预估节省率可能踩线达标（如预估 20.0%）；但全片动态场景复杂、耗码率高，全量编码落盘后真实节省率严重跌破门限。
  - **引擎历史漏洞**：`src/vcompress_abav1.py` 此前在全量编码完成后直接原子就位，**缺失对真实落盘体积的二次门限校验**，导致劣质/反膨胀产物混入目标盘。
- **治理与工程防线落地**：
  1. **盘上清理**：从 `/Volumes/T7/9.home-压缩后` 中剔除了全部 15 个实际节省率 < 20%（含 3 个体积反向膨胀）的鸡肋产物，释放目标盘 12.01 GB 空间。台账同步修正为 `空间节省不足(跳过 <20%)`，合格有效产物收敛至 **913 个**（净省空间 105.23 GB）。
  2. **核心引擎兜底（`src/vcompress_abav1.py`）**：
     - 在 `place_atomically` 之前增加实测体积比对：`actual_saving = round(((orig_bytes - tmp_bytes) / orig_bytes) * 100, 1)`；
     - 若 `actual_saving < args.min_saving_pct`（默认 20%），触发 `discard_partial` 彻底删除临时半成品，返回状态 `"skipped_saving"`，决定记录为 `DECISION_SAVING_INSUFFICIENT ("空间节省不足(跳过 <20%)")`，坚决不落盘。
  3. **合并工具双重守卫（`src/merge_fullset.py`）**：
     - 在全集计划制定 `plan_merge` 时，比对目标目录产物与源文件体积。若发现产物节省率不足 20%，自动报警并视为无效替代，确保原片作为未压缩素材并入全集，绝对不丢失原片。
  4. **测试覆盖**：新增接线单元测试与合并保护测试，套件通过数提升至 **211 passed** ✅。

---

## 11. 2026-10-12 吸收一代目实战经验：大疆相机工作流移植与 U 盘反向同步删除

### 11.1 相机低清代理与大 RAW 文件智能过滤
- **实战背景**：在早期一代目（`video-compress-gen1`）与大疆 Osmo Nano 卡实测中，机身会产生大量 `.LRF` 低分辨率预览代理视频（如 `DJI_xxx_LRF.mov`，占用 7.6GB）以及小底相机的 `.DNG` RAW 原片（占用 4.5GB），这些文件进入 VMAF 搜索会浪费算力且毫无压缩价值。
- **改进落地**：
  1. `src/vcompress_abav1.py`：新增 `--exclude-pattern` 参数（默认 `*._*,*_LRF.*,*.lrf,*.dng`），在文件扫描阶段自动排除相机代理、大 RAW 及 AppleDouble 残渣。强制采用大小写归一化匹配，跨平台（Mac/Linux）完全免疫大小写问题。
  2. `src/merge_fullset.py`：引入模式排除机制，默认同步排除这些文件，防止生成最终全集目录时将相机垃圾代理重新拷入。
  3. 配套测试：`test_exclude_pattern_filters_dji_lrf_and_dng` 与 `test_plan_merge_excludes_camera_proxies_and_raw`。

### 11.2 U 盘 / 相机卡反向同步删除工具（`src/sync_udisk_deletions.py`）
- **实战背景**：用户将相机卡/U 盘素材传输至 Mac 本地并在本地进行筛选、剪辑删片或转码后，需要一键反向清理卡内已废弃素材以释放物理硬件容量。
- **安全防线与工程化重构**：
  1. **Dry-Run 预演优先**：默认仅输出分析报告与释放容量预估，必须附加 `--apply` 并二次输入 `yes` 确认方才执行 `unlink`。
  2. **相机关键文件绝对保护**：自动识别机内元数据与缩略图文件（`.thm`、`.scr`、`.db`、`.dat`、`.ctg` 等），默认跳过保护；默认保护 `.LRF` 文件供机身回放（可通过 `--purge-lrf` 显式清除）。
  3. **产物标记与大小写鲁棒识别**：本地无论是原名、大小写不敏感匹配，还是二代带有 `.hevc` 产物标记（如 `DJI_001.hevc.MP4`），均判定为“本地已保留”，绝不误删。
  4. **路径层级容错**：自动对齐 `DCIM/` 等相机标准父目录层级偏差。
  5. **安全熔断机制**：若检测到本地基准目录为空或文件数为 0，强制中止执行，彻底防止因配置路径错误导致卡内文件被误清空。
  6. 配套测试：`test_sync_udisk_deletions.py`（4 个测试用例，覆盖预演、产物匹配、DCIM 对齐与物理删除）。

### 11.3 macOS 独立弹窗监控工具（`scripts/pop_monitor.sh`）
- 移植一代目 AppleScript 终端自动调起逻辑，默认优先选择 `iTerm2`（备选 `Terminal.app`），自动将日志路径解析为绝对路径，防止新开终端因当前目录在 `$HOME` 找不到日志。

### 11.4 单元测试升级
- 全套单元测试升至 **217 passed, 1 skipped, 3 xfailed** ✅。



