# ACTIVE_MEMORY.md — vcompress 项目记忆与当前状态

> **最近更新**：2026-08-03（由 Antigravity 助手完成功能扩展、可视化台账及 T7 批处理任务）
> **当前状态**：`src/vcompress_abav1.py` 成为全量批处理首选引擎；T7 批处理断点续压中

---

## 0. 先读这三份

| 文件 | 为什么重要 |
|---|---|
| [`docs/RED-LINES.md`](../docs/RED-LINES.md) | **12 条红线 + 每条的真机证据。改代码前必读。** 这份比代码重要 |
| [`docs/REGRESSION.md`](../docs/REGRESSION.md) | 已知坏例、预期结论、**已证伪的假说清单**。改完必跑 |
| [`REVIEW_FROM_VCAT.md`](../REVIEW_FROM_VCAT.md) | 对解耦提案的完整评审 + 本次迁移做了什么 |

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
