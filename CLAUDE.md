# CLAUDE.md — vcompress 项目宪法与 AI 协作规范

> 你是本项目的 AI 研发协作者。无论你是哪个模型 / 哪个终端 / 哪次会话，**先读完本文件，再读 `memory/ACTIVE_MEMORY.md`**，即可接手本项目的开发与评审。

## 这是什么项目

**vcompress** —— 硬件加速与 VMAF 画质拦截保障的专业视频压缩引擎。

- **一句话价值**：为大容量外接硬盘/NAS 提供**“高压缩比 + 视觉无损 + 自动撤销拦截”**的可靠视频压缩服务。
- **项目由来**：从 `nas` 仓库和 `vcat` 项目中解耦独立，作为**专一、高效、独立维护**的转码/评分核心引擎。
- **与 `vcat` 的关系**：`vcompress` 专注于视频底层转码、元数据保护与 VMAF 计算；`vcat` 为资产台账管理大脑，未来可调用 `vcompress` 作为 worker 模块。

---

## 核心技术原则与红线

1. **绝对不得损坏或静默覆写原始文件**：所有压缩产物必须输出到指定目标路径，在校验 VMAF 及体积之前，不得抹除源文件。
2. **元数据无损原则**：压缩产物必须继承原文件的 Exif、创建时间 (`FileCreateDate`)、修改时间与音轨布局。
3. **评分精准度高于一切**：VMAF 滤镜链必须严格保持 `trim -> setpts -> fps=30 -> format=yuv420p` 的时间轴对齐规则，绝不使用前置 `-ss` 做模糊 Seek 比对，防止串帧/假低分。
4. **小文件保护**：单文件体积低于阈值（如 `<100MB`）默认不强行重压，节约计算资源。

---

## 快速开发与测试命令

- 依赖检查：
  ```bash
  brew install ffmpeg exiftool
  ```
- 运行测试压缩：
  ```bash
  python3 src/vcompress.py -i <input> -o <output>
  ```
- 语法检查：
  ```bash
  python3 -m py_compile src/vcompress.py
  ```

---

## 记忆与变更规范

- `memory/ACTIVE_MEMORY.md` —— **必读必写**。记录当前代码版本、最新架构重构细节、已修正的 bug 以及留给下一个 AI 评审的要点。
