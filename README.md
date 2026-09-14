# Dataset Factory

面向 LoRA 训练数据集的**打标流水线工具**：把预先切好、归好类的图片 / 视频素材，打成能直接送训练的描述（caption），最终整理成目标模型要的格式。

当前版本（一期）交付 **AI 打标核心能力**：发一张图或一段视频 + 指令，模型输出 caption，回复流式呈现（含思考过程），多轮迭代改写；配套结构化提示词库、Skill 扩展（agentskills.io 标准）、OpenAI 兼容 API 接入与 Web / CLI 双入口。批量打标与流水线界面在后续版本提供。

## 安装

要求：Python 3.12 + [uv](https://docs.astral.sh/uv/)；Web 界面构建需要 Node.js。

**一键方式（推荐）**：仓库根目录的启动脚本会自动同步依赖、按需构建前端、拉起服务并打开浏览器——Windows 双击 `start.bat`，Linux / macOS 运行 `./start.sh`（设 `DSF_NO_BROWSER=1` 跳过自动开浏览器，`DSF_PORT` 改端口，默认 8000）。服务以**无窗口后台进程**运行，脚本窗口打开浏览器后即关闭；重复运行不会起第二个服务（端口已被本工具的服务监听就直接打开界面）；端口被无关程序占用时会提示并退出，不会误判成「服务已在运行」。

**关闭服务**：浏览器界面右上角的电源按钮（会先确认），或运行 `stop.bat` / `./stop.sh`（按端口找到监听进程、确认是本工具的服务后结束进程树，含无窗口运行的实例；不是本工具的进程一律拒绝结束，防误杀）。

**运行日志**：服务日志写 `~/.dataset_factory/logs/server.log`（滚动保留 3 份、每份 5 MiB），设置页「服务运行」子页可直接查看尾部日志。

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
dsf label -p h3 -v 素材.mp4 -m "给这段视频打个标"   # 视频（与 -i 互斥）；--video-fps / --video-max-frames 调抽帧
```

首次 `dsf label` 返回会话 id；带 `--session <id>` 再调即携带历史的迭代改写（`改成两句话`）。`--json` 输出结构化结果，供外部 agent 解析。

## 核心功能

- **提示词工作台（Web 主界面）**：三栏布局——左侧提示词卡片列表（选中的即本轮基础提示词）、中间编辑器（Markdown 正文带行号槽与 32 KiB 字节计量）、右侧调试对话（端点切换、Skill 勾选、消息流带模型 / 耗时 / 复制 / 时间戳）。
- **聊天打标（Web + CLI 对等）**：发图或视频 + 指令产出 caption（单轮一个附件，图与视频互斥）；多轮迭代改写（`dsf chat` 终端多轮，输入 `@文件路径 指令` 附图 / 附视频——按扩展名识别，`--video-fps` / `--video-max-frames` 调抽帧；`-s` 挂 skill，可多次）；会话自动落盘、重启可恢复。
- **流式回复与思考过程**：Web 端回复逐字呈现，思考型模型的思考内容单列可折叠区，生成结束后折叠区保留在该轮回复上可展开回看（思考只用于呈现、不写入 caption 与会话记录，页面刷新后不保留）——等待时能看出「在动」还是「卡住」。
- **视频输入**：视频以 base64 data URL 的 `video_url` 内容块发送，抽帧参数（fps、帧数上限）随附件可调，默认 2 fps / 16 帧；实际是否接受由端点与模型决定，不支持时给分类错误提示。
- **提示词库**：每轮打标必选一个基础提示词（进 system 消息，会话内选定后每轮自动携带、可中途切换）。`dsf prompt list / show / save / rm`；保存旧版自动进 `_history/` 滚动备份。
- **Skill 扩展**：导入 agentskills.io 标准 skill 包（CLI 传目录，Web 技能页可点「选择文件夹…」调系统选择器），打标时可选启用（SKILL.md 与 references/ 全部文件以 `<skill>` 标记注入，references 每份带路径标记）；技能页可只读预览包内文件（SKILL.md 与 references/ 可看，assets / scripts 不参与注入）。`dsf skill import / list / files / read / enable / disable / rm`。
- **端点多配置**：多套「名称 + Base URL + 模型名 + 密钥」并存、一键切换当前使用（切换对新请求立即生效）。Web 设置页增删改查并可**测试连接**（用表单当前值发一次极小请求，密钥留空则沿用已存密钥），CLI 用 `dsf config list / add / remove / use / set / show`，`dsf config test [配置名]` 发极小请求测连通性（缺省测当前使用配置）。每套配置带**高级参数**（默认折叠）：模型通用参数（temperature / top_p / max_tokens，表单与 JSON 双向同步、可直接粘贴厂商文档示例、暂不支持的键提示并忽略）与本项目传输参数（超时 / 重试），随配置存取；CLI 侧用 `dsf config params [配置名]` 查看、`--set '<JSON>'` 整体替换（与设置页同一份配置、同一套校验，`'{}'` 清空）。密钥随配置独立存放（credentials 文件，Unix 0600），或用环境变量 `DSF_API_KEY`（程序 / 外部 agent 用）。
- **服务运行管理（Web 设置页）**：查看服务状态与运行日志尾部，页头电源按钮可关闭服务（另见 `stop.bat` / `stop.sh`）。
- **可复盘**：每轮实际发出的完整请求（请求信封）先落盘再调模型，失败也有「当时喂了什么」可查。

## CLI 退出码

`0` 成功（stdout 只承载结果正文）；`1` 运行失败（stderr 给可操作中文消息）；`2` 命令用法错误。

## 数据位置

工具自身数据存 `~/.dataset_factory/`（环境变量 `DATASET_FACTORY_HOME` 可覆盖）：`endpoints/<配置名>/`（每套端点配置的 config.json 与 credentials 密钥）+ `endpoints/active`（当前使用指针）、`prompts/`、`skills/`、`sessions/`、`logs/`（服务运行日志，滚动保留）。

## 界面

Web 端是产品正式 UI：亮 / 暗双主题（跟随系统，可手动切换并记忆）、可折叠侧栏，主导航两项——「提示词」工作台与「设置」容器页（设置内含端点配置 / 技能 / 服务运行三个子页）。流水线各环节（导入、归一、质检、数据集、复核、导出）在后续版本作为顶级页面长入同一底座，布局不推倒重做。

## 架构

核心为纯 Python 库（llm / prompts / skills / sessions / labeling 五模块，分层规则由 import-linter 在 CI 守护），CLI（Typer）与 HTTP（FastAPI）是两个薄入口层。Web 端为 Vite + React + TypeScript 工程（`frontend/`，`npm run dev` 开发 / `npm run build` 产出 `dist/` 由后端托管）。详见 `backend/docs/`（API 参考由 docstring 自动生成）。

## 开发：改了 API 要更新契约快照

`backend/openapi.json` 是前后端 API 契约的单一事实来源（前端类型生成与 CI 破坏性变更检测都基于它）。改动任何路由后运行：

```
cd backend && uv run python scripts/export_openapi.py
```

并把新快照一并提交；CI 的漂移检查（生成的 spec ≠ 快照即红）和 oasdiff 破坏性变更检查（ERR 级变更即红）都在守这条线。
