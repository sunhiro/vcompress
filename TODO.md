# vcompress - 项目待办看板 (Kanban / TODO)

## 🎯 待办 (To Do)

> 🔴 **下次开工先读 `memory/ACTIVE_MEMORY.md` §7**（2026-10-13 GitHub 安装方式审计：三条路两条本来是废的）
> 与 §6（2026-09-03 那轮跨两仓修复 + 09-04 的单元套件）。

### 运维（发版 / 对外动作，需用户拍板）
- [ ] **提交并打 `v1.0.1`**：`v1.0.0` 的镜像仍带着「ffmpeg 无 libvmaf」的坏 Dockerfile，
      引擎也还缺 vaapi 全量转码的接线（§7.3/§7.4）。打 tag 会重跑 Docker workflow ——
      **那是新 Dockerfile 构建期断言的第一次真跑**。
      ⚠ 顺手把 `pyproject.toml` 的 `version` 从 `1.0.0` 改成 `1.0.1`：
      pip 那条路（`pip install git+...`）的版本号就是这个字段，不发版它永远显示 1.0.0。
      也可以先 `workflow_dispatch` 在 main 上手动重建一次 `:latest` 验构建（只推 latest，不动 semver）
- [ ] **更新并推送 tap 公式**（`~/homebrew-tap/Formula/vcompress.rb`，不在本仓）：
      url/sha256 换到新 tag。⚠ wrapper 那个修复对 v1.0.0 的 tarball **也生效**（安装期由公式生成），
      所以推 tap 不必等发版；但要让用户拿到引擎修复就得发版
- [ ] **给 Docker workflow 加冒烟**（可选，优先级已降低）：`build-push-action` 目前只
      build+push，**没有任何东西跑过这个镜像**。加一步 `docker run --rm <新镜像> --help`
      能让「镜像能构建但跑不起来」那类缺陷不再静默复发。
      ⚠ **§7.3 那一类已经被新 Dockerfile 兜住了**：构建期的
      `ffmpeg -filters | grep -qw libvmaf && ... grep -qw hevc_vaapi` 失败会让整个
      build 当场失败，而 build-push-action 一失败就不推镜像 —— 不需要额外步骤。
      剩下的只是「Python 侧跑不跑得起来」，价值不如从前。
      ⚠ 未做，因为本机没 docker，改完测不了；**别把没测过的 CI 步骤直接推进去**

### 运维（测试机同步）
- [ ] 🔴 **把 Worker 机器上的引擎更到最新**。
      开下一批批量任务前，确保 Worker 机器与本仓版本一致。
- [ ] **真机验一次 `-e vaapi` 全量转码**（§7.4-1）：本轮只验到「命令拼对了」，
      本机无核显、无硬编链路。NAS 上跑一次 R1 才算数。
- [ ] **清理远程测试产物**：临时测试目录与构建残片。

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
- [x] **GitHub 三种安装方式审计**（2026-10-13，§7）：pip 通；Homebrew wrapper 拿错解释器、
      Docker 镜像 ffmpeg 无 libvmaf —— 两条都已修。附带修了 `-e vaapi` 全量转码没接硬件、
      日志落点在 CWD、Windows 上 2 条假红灯、文档门限值/入口对不上代码
- [x] **红线 §11 位深守卫补齐**（2026-10-13 复核轮，§7.7）：主引擎此前**没有**这道守卫，
      且它的 HEVC 跳过判据是码率不是「是 HEVC 就跳」——40Mbps 的 10bit HEVC 会一路走到
      vaapi 全量转码、静默变 8bit。已按 `vcompress.py` 逐字同源补齐，`--skip-hdr` 的
      子串误判（`'12' in 'nv12'`）一并修掉
- [x] **单元测试套件 `tests/`**（2026-09-04 建，2026-10-13 转全绿）：201 条
      （197 passed / 1 skipped / 3 xfailed），锁命名契约 / 状态分派 / 报表清理 / 内容清单 /
      位深色度守卫（含跨引擎逐格式对撞）、全量编码命令的拼装、
      搜索收场的三分（素材结论 vs 机器故障）、aac 音轨退路的接线。21 次变异验证全部当场被抓。
      跑法与「两套测试的分工」见 `docs/REGRESSION.md` 开头
- [x] **搜索收场三分 + aac 退路 + §1 前置守卫**（2026-10-14 红线复检轮，§1/§2/§10）：
      `ab-av1 crf-search` 跑挂了不再伪装成 `quality_rejected`（抛 `AbAv1SearchError` → `failed`）；
      全量编码补上备份引擎早有的「老音轨退成 aac 重试一次」；`-i x -o x` 这种
      「产物路径 = 源片」的误用改为**编码前**拒绝（红线 §1，此前只有事后守卫）。
      详见 RED-LINES §1/§2/§9/§10 补记
- [x] **修缺陷 1**：`_report.csv` 跳过类行每轮重复追加（2026-09-03，vcat P2-58/P2-59）。
      ⚠ 第一版修过头了——preview 把「保留压缩版」行也清掉，而那是 vcat 血缘唯一认的行，
      由红线复检当场打回。现有 `test_report_prune.py` 两个方向都锁着
- [x] Mac Mini Apple Silicon `hevc_videotoolbox` 核心压制与 VMAF 搜寻引擎 (`vcompress_abav1.py`)
- [x] HTML / CSV / Excel 三重报告输出集成
- [x] 人眼验证一键替换脚本生成 (`_apply_replace.sh`)
- [x] 修复 `yggit-derp` 宿主机套接字重启导致的 `no-derp-connection` 并恢复 Mac Mini 远程穿透管道。
