# vcompress - 项目待办看板 (Kanban / TODO)

## 🎯 待办 (To Do)

> 🔴 **下次开工先读 `memory/ACTIVE_MEMORY.md` §9 与 §8**，再看 §7、§6。
> ⚠ §7.7 的自评数字已被 §8 推翻，别照抄。

### 需要你拍板（不动手就一直悬着）
- [ ] 🔴 **在 vcat 那个会话里给 preview 加一支分派**（ACTIVE_MEMORY §9.1）。
      本仓已发 `status: "preview"`（实测字段全 null，预测值在 `predicted` 块里），
      但 vcat `compress_worker.py:539` 那个 `out_size is None → encode_failed`
      还没有这一支——**每条 preview 都会被记一次假失败**（响且安全，不污染台账）。
      🔴 **在 vcat 会话里改**，本仓这边已完事（红线 5：台账是 vcat 的）。
- [x] ~~**红线 10/11 的守卫要不要移植进生产引擎**~~ —— **09-06 实测：都不需要移植** —— 2026-09-05 复查发现三条红线守卫
      **只在 `src/vcompress.py` 里实现，生产的 `vcompress_abav1.py` 一条都没有**：

      | 守卫 | `vcompress.py` | `vcompress_abav1.py`（生产） |
      |---|---|---|
      | 红线 3 近无损对照 / `measurement_unreliable` | ✅ `:815` | ❌ 状态枚举里没有这一类 |
      | 红线 10 aac 退避（MP4 装不下 MP2/PCM） | ✅ `:847` | ❌ 直接记「编码失败」 |
      | 红线 11 位深降级默认拒绝 | ✅ `:688` | ❌ 只记录，不拒绝 |

      公平地说，红线 4 明写「ab-av1 正是这么做的」（无损流拷贝取窗），所以红线 5
      「两把尺子」的根因在生产路上**结构性地不存在**，红线 3 的兜底需求小得多。
      但 10/11 跟测量路径无关，生产引擎确实是裸的——而 `--skip-hdr` 默认 `False`、
      HEVC 只在 ≤30Mbps 时跳过，10bit 高码率素材是能走进去的。
      **09-06 结论**：三条守卫在生产引擎上**都撞不上**，但理由全是「碰巧」——
      红线 3 靠 ab-av1 的取窗设计、红线 10 靠 ADR-020 的命名约定、红线 11 靠 ffmpeg
      的格式协商。已改为**把这三个事实钉住**而不是加守卫：
      `docs/REGRESSION.md` 新增 **R9**（不降位深，附实测表）与 **R10**（无损流拷贝取窗），
      新增 `tests/test_container_contract.py`（23 条，钉容器沿用）。
      🔴 另查清：`vcompress.py` 是 **Linux 测量基准**（R1~R8 跑的就是它），不是「备份」，
      **退役它会把回归套件弄死**。下面三项相关 TODO 的优先级理由据此重写。

### 运维（不改代码，下次上机顺手做）
- [ ] 🔴 **把 Mac mini 上的引擎更到最新**（开下一批压缩前的硬前提）。
      ⚠ **2026-09-06：那台机器在 Tailscale 上已离线 36 天**（`7plus-mac-mini`），
      暂时够不着。它上线之前这条做不了，也就意味着**暂时不能开新的批压缩**。
      本仓现在是 `ac16bbc`；Mac mini 上次留痕的 md5 `14860d1f` = 本仓 **`73d3ca2`（2026-08-07）**，
      **中间隔着 4 个提交，含 S28 全部六处修复**。若那台没更新过，它跑的是
      「闸门拒绝发不出去、编码失败不删残片、续压不发 JSON」的旧行为 —— 而它正是**真正在产出文件**的那台。
      🔴 **S28 的修复是引擎与工人（vcat `contrib/mac_mini_worker`）配对的，只更一边会退回旧行为。**
      在家走 NAS 中转：`ssh -o ProxyCommand="ssh inas python3 /var/services/homes/sunhiro/pipe_mac.py" mac`
      ⚠ **别从文档里抄哈希去比对** —— 裸哈希下次提交就静默过期（`fullset.py 26ab6420`
      过期了 12 天没人发现；2026-09-04 我还往 md5 的表里记了个 sha256，两重错，由红线复检抓出）。
      **两边现算**：`md5 -q src/vcompress_abav1.py`。要记就记 git 提交号。
- [ ] **清理 NAS 上的回归测试产物**：`/volume1/docker/vcat/regression/out-R*/`
      —— 09-06 实测现在是 **662M**（不是记的 ~1.2GB）。删是不可逆的，等点头再动。
      ⚠ `vcompress-repo/` **别删**：09-06 刚把最新 5 个文件推上去了，下次跑 R1~R8 要用。

### 代码
- [ ] **报表行键把逗号换成空格**（`vcompress_abav1.py` 里 `report_prune_targets()` 与
      `main()` 写行那两处 `.replace(',', ' ')`；⚠ 按符号找，别记行号——本仓 09-05
      加了 ~280 行，原先记的 `:735`/`:267` 当场就过期了）——可报表本来就是
      `csv.writer` 写的、天然能转义逗号。这是裸 f-string 时代的遗留补丁，现在只剩副作用：
      `a,b.mp4` 与 `a b.mp4` 撞成同一个键，清旧行时会误删。
      🔴 **跨仓契约**（vcat `lineage.load_report` 按这个键配对），要改两边一起改
- [ ] **小毛病三件**：① 日志 `FileHandler` 写 cwd 且无轮转（仓库根已积 5.4MB、`src/` 150KB）；
      ② `check_dependencies()` 在 argparse 之前，缺依赖时 `--help` 也 exit 1；
      ③ `run_ab_av1_search` 的 `--temp-dir /tmp` 硬编码
- [ ] **`--json-out` 的 JSON 与日志混在同一个 stdout**：`logging.basicConfig` 挂的是
      `StreamHandler(sys.stdout)`，而代码注释写的是「与 logger 写 stderr/文件分离」——
      注释与实现不符。2026-09-05 实测：工人要从 stdout 里捞那行 JSON 才拿得到结果。
      改（日志改走 stderr）会动跨仓契约，先确认 vcat 侧怎么解析的
- [ ] **修缺陷 2**：`probe_pix_fmt()` 逗号污染 CSV 列数，`export_report` 静默丢半列（§5.3）。
      🔴 **只在备份引擎 `vcompress.py` 里**（生产的 `vcompress_abav1.py` 走 `-of json` + `csv.writer`，免疫）。
      修法：`probe_pix_fmt` 取第一个非空 token；`vcompress.py:1116` 那行裸 f-string 改 `csv.writer`。
      **它破坏的正是 ADR-027 刚补上的第 8 列**（像素格式变迁），所以不是纯洁癖
- [ ] **修缺陷 3**：`model_hd`/`model_4k` 死参数 + 日志报不存在的 macOS 路径（§5.4，同样只在 `vcompress.py`）
- [ ] **改 R5 的期望值**：产物沿用源扩展名走 MPEG-PS，`aac` 退避路径没被触发（§5.6）；顺带评估 HEVC 封进 `.VOB` 的播放兼容性
- [ ] **RED-LINES §5「两把尺子」**——文档自标「当前已知未修」，修法方向是 §4 的流拷贝方案
- [ ] **给 `vcompress.py` 铺单元测试**（1140 行，当前 0 条）。
      ⚠ 它**不是备份**，是 R1~R8 真机套件跑的那个 Linux 测量基准（`docs/REGRESSION.md:171`），
      而且红线 3/10/11 的守卫只有它实现了——**动它要比动生产引擎更谨慎**
- [ ] **方案 C：Web/API 任务分发集成 (vcat)**
  - 在 `vcat` Web 台账面板增加「下发压缩」按钮。
  - 在 Mac Worker 中增加对 `vcat` 任务队列 (如 JSON / SQLite) 的轮询支持。
  - 实现进度双向同步与 Web 进度条展示。
- [ ] **方案 B：局域网一键远程 SSH 脚本封装**
  - 提供标准的 SSH 一键调用脚本：`vcompress-remote <input_nas_dir> <output_nas_dir>`，在局域网内任意 PC 一键下发任务至 Mac。

## 🏃 进行中 (In Progress)
- [x] **方案 A：热文件夹监控自动压缩 (Hot Folder Worker)**
  - 实现基于 `watchdog` 的文件夹监控守护进程。
  - 支持将视频拖入特定接收箱即可触发硬件压缩并输出。

## ✅ 已完成 (Done)
- [x] **2026-09-06 下半场**（详见 §9）：**preview 有了独立 status**
      （实测字段全 null + `predicted` 块，5 个变异体全抓住）· `RED-LINES.md` 补 **§11.1**
      分引擎讲清位深那条（原文一字未删；上一轮我说「推理是错的」说重了，
      那句话对 `vcompress.py` 是成立的，问题是没说自己在讲哪个引擎）·
      **群晖连通性核实**：连得上，但查清 **R1~R8 跑的是 `vcompress.py`，本轮改动零暴露**，
      不需要重跑（上一轮说「务必重跑 R5/R6/R8」是错的）。测试 195 → **205 passed**
- [x] **2026-09-06 红线复检整改**（详见 §8），测试 152 → **195 passed**，
      变异 **23 个抓住 22**（1 个等价变异体）。复检员当场演示五个「变异全绿」，
      其中一个**精确还原了 09-05 声称修好的那个 bug**（`if kind == "broken":` → `if False:`）。
      🔴 教训：**给纯函数写测试，锁住的只是那个纯函数**；提取本身会制造一段没人看的接线，
      而 bug 恰恰住在接线里。新增 `tests/test_dispatch_wiring.py` 专打接线层。
      顺带修：uchg 半成品删不掉 · `place_atomically` 缺 samefile 守卫 · 孤儿残片无人清扫 ·
      台账把机器故障渲染成「跳过」 · `DECISION_KEEP` 没真正做到单一出处 ·
      两条我自己写的假测试（自建 parser 测默认值 / 参数化了却不用参数）
- [x] **2026-09-06 摘掉 `-e vaapi`**：`hevc_vaapi` 落进 `else` 分支、没有 `-vaapi_device`，
      硬件编码器起不来。「看起来支持其实跑不了」比没有更坏。vaapi 走 `vcompress.py`
- [x] **2026-09-05 复查五修**（详见 `memory/ACTIVE_MEMORY.md` §7），测试 91 → 152 passed：
  - **A 搜索结果三类判别**：`ab-av1 跑挂了` 曾与 `素材真压不动` 一起被记成
    `quality_rejected`（终态、工人永不重试）。实测两者**都 exit 1**，判别器在 stdout 里
    （`crf-search-error` 那行），而 `check=True` 在看 stdout 之前就把它抛掉了
  - **B 成功行判据收紧**：`if "crf" in data` → `type == "crf-search-done"`。中间探测行
    同样带 crf，实测含 `vmaf 63.36` 与 `156%`（压完更大）那种没过闸门的档位
  - **C 产物原子落位**：先写隐藏临时名 `.X.hevc.partial.mp4`，元数据写完再 `os.replace`
    就位。连带解决群晖回收站 1010 个中间版占 40GB。顺手补了 `uchg` 摘挂（copystat
    会继承不可变标志，之后改名一律 EPERM）
  - **D 红线 2 成为真闸门**：`preserve_metadata` 的返回值不再被丢弃；失败即丢弃产物、
    原片不动、记可重试的 `failed`。逃生口 `--allow-metadata-loss`（默认关）
  - **E preview 不再把预估当实测**：单独计数并改口径打印「预估可释放」；`--json-out`
    不再给不存在的产物报体积；`_manifest.csv` 不再被 preview 的预估 VMAF 覆写
- [x] **单元测试套件 `tests/`**（2026-09-04）：93 条，锁命名契约 / 状态分派 / 报表清理 / 内容清单。
      11 次变异验证全部当场被抓。跑法与「两套测试的分工」见 `docs/REGRESSION.md` 开头
- [x] **修缺陷 1**：`_report.csv` 跳过类行每轮重复追加（2026-09-03，vcat P2-58/P2-59）。
      ⚠ 第一版修过头了——preview 把「保留压缩版」行也清掉，而那是 vcat 血缘唯一认的行，
      由红线复检当场打回。现有 `test_report_prune.py` 两个方向都锁着
- [x] Mac Mini Apple Silicon `hevc_videotoolbox` 核心压制与 VMAF 搜寻引擎 (`vcompress_abav1.py`)
- [x] HTML / CSV / Excel 三重报告输出集成
- [x] 人眼验证一键替换脚本生成 (`_apply_replace.sh`)
- [x] 修复 `yggit-derp` 宿主机套接字重启导致的 `no-derp-connection` 并恢复 Mac Mini 远程穿透管道。
