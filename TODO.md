# vcompress - 项目待办看板 (Kanban / TODO)

## 🎯 待办 (To Do)
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
