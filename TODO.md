# vcompress - 项目待办看板 (Kanban / TODO)

## 🎯 待办 (To Do)
- [ ] **改 R5 的期望值**：产物沿用源扩展名走 MPEG-PS，`aac` 退避路径没被触发（§5.6）；顺带评估 HEVC 封进 `.VOB` 的播放兼容性
- [ ] **清理 NAS 上的回归测试产物**：`/volume1/docker/vcat/regression/out-R*/`（~1.2GB）与 `vcompress-repo/`
- [ ] **修缺陷 1**：`_report.csv` 跳过类行每轮重复追加（§5.2，`--preview` 下会整棵树重复）
- [ ] **修缺陷 2**：`probe_pix_fmt()` 逗号污染 CSV 列数，`export_report` 静默丢半列（§5.3）
- [ ] **修缺陷 3**：`model_hd`/`model_4k` 死参数 + 日志报不存在的 macOS 路径（§5.4）
- [ ] **RED-LINES §5「两把尺子」**——文档自标「当前已知未修」，修法方向是 §4 的流拷贝方案
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
- [x] Mac Mini Apple Silicon `hevc_videotoolbox` 核心压制与 VMAF 搜寻引擎 (`vcompress_abav1.py`)
- [x] HTML / CSV / Excel 三重报告输出集成
- [x] 人眼验证一键替换脚本生成 (`_apply_replace.sh`)
- [x] 修复 `yggit-derp` 宿主机套接字重启导致的 `no-derp-connection` 并恢复 Mac Mini 远程穿透管道。
