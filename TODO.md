# vcompress - 项目待办看板 (Kanban / TODO)

## 🎯 待办 (To Do)

> 🔴 **下次开工先读 `memory/ACTIVE_MEMORY.md` §6**（2026-09-03 那轮跨两仓修复 + 09-04 的单元套件）。

### 运维（不改代码，下次上机顺手做）
- [ ] 🔴 **把 Mac mini 上的引擎更到最新**（开下一批压缩前的硬前提）。
      本仓现在是 `ac16bbc`；Mac mini 上次留痕的 md5 `14860d1f` = 本仓 **`73d3ca2`（2026-08-07）**，
      **中间隔着 4 个提交，含 S28 全部六处修复**。若那台没更新过，它跑的是
      「闸门拒绝发不出去、编码失败不删残片、续压不发 JSON」的旧行为 —— 而它正是**真正在产出文件**的那台。
      🔴 **S28 的修复是引擎与工人（vcat `contrib/mac_mini_worker`）配对的，只更一边会退回旧行为。**
      在家走 NAS 中转：`ssh -o ProxyCommand="ssh inas python3 /var/services/homes/sunhiro/pipe_mac.py" mac`
      ⚠ **别从文档里抄哈希去比对** —— 裸哈希下次提交就静默过期（`fullset.py 26ab6420`
      过期了 12 天没人发现；2026-09-04 我还往 md5 的表里记了个 sha256，两重错，由红线复检抓出）。
      **两边现算**：`md5 -q src/vcompress_abav1.py`。要记就记 git 提交号。
- [ ] **清理 NAS 上的回归测试产物**：`/volume1/docker/vcat/regression/out-R*/`（~1.2GB）与 `vcompress-repo/`

### 代码
- [ ] **修缺陷 2**：`probe_pix_fmt()` 逗号污染 CSV 列数，`export_report` 静默丢半列（§5.3）。
      🔴 **只在备份引擎 `vcompress.py` 里**（生产的 `vcompress_abav1.py` 走 `-of json` + `csv.writer`，免疫）。
      修法：`probe_pix_fmt` 取第一个非空 token；`vcompress.py:1116` 那行裸 f-string 改 `csv.writer`。
      **它破坏的正是 ADR-027 刚补上的第 8 列**（像素格式变迁），所以不是纯洁癖
- [ ] **修缺陷 3**：`model_hd`/`model_4k` 死参数 + 日志报不存在的 macOS 路径（§5.4，同样只在 `vcompress.py`）
- [ ] **改 R5 的期望值**：产物沿用源扩展名走 MPEG-PS，`aac` 退避路径没被触发（§5.6）；顺带评估 HEVC 封进 `.VOB` 的播放兼容性
- [ ] **RED-LINES §5「两把尺子」**——文档自标「当前已知未修」，修法方向是 §4 的流拷贝方案
- [ ] **给备份引擎 `vcompress.py` 也铺上单元测试**（1140 行，当前 0 条）。
      2026-09-04 建的 `tests/` 只覆盖生产引擎那条路
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
- [x] **单元测试套件 `tests/`**（2026-09-04）：93 条，锁命名契约 / 状态分派 / 报表清理 / 内容清单。
      11 次变异验证全部当场被抓。跑法与「两套测试的分工」见 `docs/REGRESSION.md` 开头
- [x] **修缺陷 1**：`_report.csv` 跳过类行每轮重复追加（2026-09-03，vcat P2-58/P2-59）。
      ⚠ 第一版修过头了——preview 把「保留压缩版」行也清掉，而那是 vcat 血缘唯一认的行，
      由红线复检当场打回。现有 `test_report_prune.py` 两个方向都锁着
- [x] Mac Mini Apple Silicon `hevc_videotoolbox` 核心压制与 VMAF 搜寻引擎 (`vcompress_abav1.py`)
- [x] HTML / CSV / Excel 三重报告输出集成
- [x] 人眼验证一键替换脚本生成 (`_apply_replace.sh`)
- [x] 修复 `yggit-derp` 宿主机套接字重启导致的 `no-derp-connection` 并恢复 Mac Mini 远程穿透管道。
