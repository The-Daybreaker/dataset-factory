# Dataset Factory

面向 LoRA 训练数据集的**打标流水线工具**：把预先切好、归好类的图片素材，打成能直接送训练的描述（caption），最终整理成目标模型要的格式。

当前版本（一期）交付 **AI 打标核心能力**：发一张图 + 指令，模型输出 caption，多轮迭代改写；配套结构化提示词库、Skill 扩展（agentskills.io 标准）、OpenAI 兼容 API 接入与 Web / CLI 双入口。批量打标与流水线界面在后续版本提供。

## 安装

要求：Python 3.12 + [uv](https://docs.astral.sh/uv/)；Web 界面构建需要 Node.js。

**一键方式（推荐）**：仓库根目录的启动脚本会自动同步依赖、按需构建前端、拉起服务并打开浏览器——Windows 双击 `start.bat`，Linux / macOS 运行 `./start.sh`（设 `DSF_NO_BROWSER=1` 跳过自动开浏览器）。

**手动方式**：

```bash
cd backend && uv sync          # 后端依赖
cd frontend && npm install && npm run build   # 前端构建产物 dist/（由后端托管）

# 可选：把 dsf 命令装成全局工具（任意目录可用，代码改动即时生效）
cd backend && uv tool install --editable .
```

## 快速上手

```bash
# 1. 配置 OpenAI 兼容端点（多套配置按名称保存，密钥交互输入不回显）
dsf config add siliconflow --base-url https://api.siliconflow.cn/v1 --model Qwen/Qwen3.5-4B

# 2a. 起 Web 界面（提示词工作台 + 设置；浏览器打开 http://127.0.0.1:8000）
dsf serve

# 2b. 或直接用 CLI 单发打标
dsf prompt save h3 -d "视频打标" -f 提示词正文.md
dsf label -p h3 -i 素材.jpg -m "给这张图打个标"
```

首次 `dsf label` 返回会话 id；带 `--session <id>` 再调即携带历史的迭代改写（`改成两句话`）。`--json` 输出结构化结果，供外部 agent 解析。

## 核心功能

- **提示词工作台（Web 主界面）**：三栏布局——左侧提示词卡片列表（选中的即本轮基础提示词）、中间编辑器（Markdown 正文带行号槽与 32 KiB 字节计量）、右侧调试对话（端点切换、Skill 勾选、消息流带模型 / 耗时 / 复制 / 时间戳）。
- **聊天打标（Web + CLI 对等）**：发图 + 指令产出 caption；多轮迭代改写（`dsf chat` 终端多轮，输入 `@图片路径 指令` 附图）；会话自动落盘、重启可恢复。
- **提示词库**：每轮打标必选一个基础提示词（进 system 消息，会话内选定后每轮自动携带、可中途切换）。`dsf prompt list / show / save / rm`；保存旧版自动进 `_history/` 滚动备份。
- **Skill 扩展**：导入 agentskills.io 标准 skill 包，打标时可选启用（全文以 `<skill>` 标记注入）；技能页可只读预览包内文件（SKILL.md 与 references/ 可看，assets / scripts 不参与注入）。`dsf skill import / list / enable / disable / rm`。
- **端点多配置**：多套「名称 + Base URL + 模型名 + 密钥」并存、一键切换当前使用（切换对新请求立即生效）。Web 设置页增删改查，CLI 用 `dsf config list / add / remove / use / set / show`。密钥随配置独立存放（credentials 文件，Unix 0600），或用环境变量 `DSF_API_KEY`（程序 / 外部 agent 用）。
- **可复盘**：每轮实际发出的完整请求（请求信封）先落盘再调模型，失败也有「当时喂了什么」可查。

## CLI 退出码

`0` 成功（stdout 只承载结果正文）；`1` 运行失败（stderr 给可操作中文消息）；`2` 命令用法错误。

## 数据位置

工具自身数据存 `~/.dataset_factory/`（环境变量 `DATASET_FACTORY_HOME` 可覆盖）：`endpoints/<配置名>/`（每套端点配置的 config.json 与 credentials 密钥）+ `endpoints/active`（当前使用指针）、`prompts/`、`skills/`、`sessions/`。

## 界面

Web 端是产品正式 UI 底座：亮 / 暗双主题（跟随系统，可手动切换并记忆）、可折叠侧栏、「提示词 / 设置」两个工作区页面。流水线各环节（导入、归一、质检、数据集、复核、导出）以低保真占位挂在侧栏导航上，随二、三期长入，布局不推倒重做。

## 架构

核心为纯 Python 库（llm / prompts / skills / sessions / labeling 五模块，分层规则由 import-linter 在 CI 守护），CLI（Typer）与 HTTP（FastAPI）是两个薄入口层。Web 测试界面为 Vite + React + TypeScript 工程（`frontend/`，`npm run dev` 开发 / `npm run build` 产出 `dist/` 由后端托管）。详见 `backend/docs/`（API 参考由 docstring 自动生成）。

## 开发：改了 API 要更新契约快照

`backend/openapi.json` 是前后端 API 契约的单一事实来源（前端类型生成与 CI 破坏性变更检测都基于它）。改动任何路由后运行：

```
cd backend && uv run python scripts/export_openapi.py
```

并把新快照一并提交；CI 的漂移检查（生成的 spec ≠ 快照即红）和 oasdiff 破坏性变更检查（ERR 级变更即红）都在守这条线。
