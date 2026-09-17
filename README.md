# Dataset Factory

面向 LoRA 训练数据集的**打标流水线工具**：把预先切好、归好类的图片 / 视频素材，打成能直接送训练的描述（caption），最终整理成目标模型要的格式。

工具提供 **AI 打标核心能力**：发一张图或一段视频 + 指令，模型输出 caption，回复流式呈现（含思考过程），多轮迭代改写；配套提示词库、Skill 扩展（agentskills.io 标准）、OpenAI 兼容 API 接入与 Web / CLI 双入口。Web 打标页与 CLI 都支持批量打标、失败重试和 ZIP 导出。

## 安装

要求：Python 3.12 + [uv](https://docs.astral.sh/uv/)；Web 界面构建需要 Node.js。

**一键方式（推荐）**：仓库根目录的启动脚本会自动同步依赖、按需构建前端、拉起服务并打开浏览器——Windows 双击 `start.bat`，Linux / macOS 运行 `./start.sh`（设 `DSF_NO_BROWSER=1` 跳过自动开浏览器，`DSF_PORT` 改端口，默认 8000）。服务以**无窗口后台进程**运行，脚本窗口打开浏览器后即关闭；重复运行不会起第二个服务（端口已被本工具的服务监听就直接打开界面）；端口被无关程序占用时会提示并退出，不会误判成「服务已在运行」。

**关闭服务**：浏览器界面侧栏底部的电源按钮（会先确认），或运行 `stop.bat` / `./stop.sh`（按端口找到监听进程、确认是本工具的服务后结束进程树，含无窗口运行的实例；不是本工具的进程一律拒绝结束，防误杀）。

**运行日志**：服务日志写 `~/.dataset_factory/logs/server.log`（滚动保留 3 份、每份 5 MiB），设置页「服务运行」子页可直接查看尾部日志。

**手动方式**：

```bash
cd backend
uv sync --locked              # 后端依赖
cd ../frontend
npm ci
npm run build                 # 前端构建产物 dist/（由后端托管）

# 可选：把 dsf 命令装成全局工具（任意目录可用，代码改动即时生效）
cd ../backend
uv tool install --editable .
```

## 快速上手

```bash
# 1. 配置 OpenAI 兼容端点（多套配置按名称保存，密钥交互输入不回显）
dsf config add siliconflow --base-url https://api.siliconflow.cn/v1 --model Qwen/Qwen3.5-4B

# 2a. 起 Web 界面（策略工作台 + 打标 + 设置；浏览器打开 http://127.0.0.1:8000）
dsf serve

# 2b. 或直接用 CLI 单发打标
dsf prompt save h3 -d "视频打标" -f 提示词正文.md
dsf label -p h3 -i 素材.jpg -m "给这张图打个标"
dsf label -p h3 -v 素材.mp4 -m "给这段视频打个标"   # 视频（与 -i 互斥）；--video-fps / --video-max-frames 调抽帧
```

首次 `dsf label` 返回会话 id；带 `--session <id>` 再调即携带历史的迭代改写（`改成两句话`）。`--json` 输出结构化结果，供外部 agent 解析。

## 核心功能

- **策略工作台**：顶行管理可复用的策略组合，下方左侧编辑基础提示词，右侧用图片或视频调试对话。提示词名称与描述可直接编辑，下拉切换其他提示词；未保存草稿会锁定切换，避免丢失修改。右侧选择端点和 Skill，试标满意后把组合保存到策略库。
- **聊天打标（Web + CLI 对等）**：发图或视频 + 指令产出 caption（单轮一个附件，图与视频互斥）；多轮迭代改写（`dsf chat` 终端多轮，输入 `@文件路径 指令` 附图 / 附视频——按扩展名识别，`--video-fps` / `--video-max-frames` 调抽帧；`-s` 挂 skill，可多次）；会话自动落盘、重启可恢复。
- **流式回复与思考过程**：Web 端回复逐字呈现，思考型模型的思考内容单列可折叠区，生成结束后折叠区保留在该轮回复上可展开回看（思考只用于呈现、不写入 caption 与会话记录，页面刷新后不保留）——等待时能看出「在动」还是「卡住」。
- **视频输入**：视频以 base64 data URL 的 `video_url` 内容块发送，抽帧参数（fps、帧数上限）随附件可调，默认 2 fps / 16 帧；实际是否接受由端点与模型决定，不支持时给分类错误提示。
- **提示词库**：每轮打标必选一个基础提示词（进 system 消息，会话内选定后每轮自动携带、可中途切换）。`dsf prompt list / show / save / rm`；保存旧版自动进 `_history/` 滚动备份。
- **Skill 扩展**：导入 agentskills.io 标准 skill 包（CLI 传目录，Web 技能页支持选择或拖入文件夹），打标时可选启用（SKILL.md 与 references/ 全部文件以 `<skill>` 标记注入，references 每份带路径标记）。技能页可编辑包内文本和描述；保存时检查原文是否被其他写者修改，避免覆盖对方的更新。assets / scripts 不参与注入。CLI 提供 `dsf skill import / list / files / read / enable / disable / rm`。
- **端点多配置**：多套「名称 + Base URL + 模型名 + 密钥」并存、一键切换当前使用（切换对新请求立即生效）。Web 设置页增删改查并可**测试连接**（用表单当前值发一次极小请求，密钥留空则沿用已存密钥），CLI 用 `dsf config list / add / remove / use / set / show`，`dsf config test [配置名]` 发极小请求测连通性（缺省测当前使用配置）。每套配置带**高级参数**（默认折叠）：模型通用参数（temperature / top_p / max_tokens，表单与 JSON 双向同步、可直接粘贴厂商文档示例、暂不支持的键提示并忽略）与本项目传输参数（超时 / 重试），随配置存取；CLI 侧用 `dsf config params [配置名]` 查看、`--set '<JSON>'` 整体替换（与设置页同一份配置、同一套校验，`'{}'` 清空）。密钥随配置独立存放（credentials 文件，Unix 0600），或用环境变量 `DSF_API_KEY`（程序 / 外部 agent 用）。
- **服务运行管理（Web 设置页）**：查看服务状态与运行日志尾部，侧栏电源按钮可关闭服务（另见 `stop.bat` / `stop.sh`）。
- **可复盘**：每轮实际发出的完整请求（请求信封）先落盘再调模型，失败也有「当时喂了什么」可查。

## 批量打标

**工作目录**保存素材、各批次 caption 和 `.dsf/` 元数据。一个目录可以创建多个批次，每个批次持有独立策略快照，库策略后续修改不会影响它。目录里的散文件须先经导入登记才参与打标；手动拷入的文件可用 `workdir import <路径>` 就地补登记。

### 使用 Web 打标页

先在「策略」页试标并保存组合，再进入「打标」点击加号新建跑批。选择「复制导入」时，来源保持不动，素材复制到工作目录；选择「就地采用」时，所选目录本身就是工作目录，后续维护会作用于其中的原始素材。策略可从库中应用，也可以从零选择端点、基础提示词与 Skill。

顶部选择目录与批次，左列按排队中、已完成、未完成、重试列表、缺失和未导入分组。点条目查看素材与 caption，也可以对照同一素材的其他批次结果。选择已完成或可重试的未完成条目加入重试列表，再点击「开始重试」；停止在当前条目收尾后生效，已完成产物保留。

批次概览可查看本次运行统计、日志、策略快照和素材完整性。打包区列出将入包与被排除的条目，支持排除和撤销，默认顺序重命名。点击「导出当前策略」，完成后下载 ZIP，训练时使用包内的同名素材与 txt，不直接使用工作目录里的批次前缀文件。

目录下拉中的设置按钮进入工作目录设置，可补充导入、管理批次、清理产物或搬迁目录。素材缺失时优先按记录的来源重新导入；来源不可用时从其他位置补入。删除整个工作目录会包含素材，请仔细阅读二次确认。

### 使用 CLI

**workdir：导入与维护。** 先准备空工作目录，再从来源复制导入；不带 `--source` 的 `add` 表示就地采用原始目录，会提示维护操作作用于原始素材的风险。`reimport` 根据已记录来源补回缺失文件，`--name` 可重复以限定文件；来源不可用会逐条报告。`verify` 校验素材完整性，`cleanup --list` 与 `cleanup-runs --list` 只看清单，真正清理须用 `--name` 明确选择。

```bash
mkdir dataset-work
dsf workdir add ./dataset-work --source ./source-images
dsf workdir import ./dataset-work ./more-images
dsf workdir reimport ./dataset-work --name sample.jpg
dsf workdir verify ./dataset-work
```

**strategy：跨目录复用组合。** 库策略引用已配置的端点、基础提示词与可选 Skill；`list` / `show` 同时显示引用是否有效。可用 `edit` 修改、`copy` 派生、`rebind` 修复失效引用。下面的 `siliconflow` 和 `h3` 对应快速上手中创建的配置。

```bash
dsf strategy add detailed --endpoint siliconflow --prompt h3
dsf strategy list
```

**batch 与 run：创建、执行和重试。** `add` 从库策略创建批次，序号从 `s1` 开始且删除后不复用；`edit` 只改显示名与描述，换组合须创建新批次。`run` 前台执行，进度写 stderr、最终 JSON 写 stdout 并包含日志路径。再次运行会跳过素材未变且已有有效产物的条目；Ctrl-C 或另一个终端的 `batch stop` 请求安全停止，保留当前成功产物。重试分两步：先加入名单，再运行 retry 模式。

```bash
dsf batch add ./dataset-work --from-library detailed
dsf run ./dataset-work s1
dsf batch items ./dataset-work s1
dsf batch retry add ./dataset-work s1 sample
dsf run ./dataset-work s1 --mode retry
dsf batch status ./dataset-work s1
dsf batch stop ./dataset-work s1
```

**export：预览与交付。** `plan` 列出将入包和被排除的文件及原因；`run` 写 ZIP，不覆盖已有文件。默认将素材与 caption 顺序命名为 `001.jpg / 001.txt`，ZIP 内平铺、不含元数据目录；加 `--original-names` 保留素材原名并去掉 caption 的批次前缀。导出只针对指定批次，`batch exclude` 持久排除条目，`--undo` 撤销排除。

```bash
dsf export plan ./dataset-work s1
dsf export run ./dataset-work s1 -o ./training.zip
dsf batch exclude ./dataset-work s1 sample
dsf batch exclude ./dataset-work s1 sample --undo
```

危险操作默认交互确认，脚本调用须明确提供 `--yes`；运行前发现未导入文件时也会要求确认。`workdir rm` 删除整个目录，含就地采用的原始素材；`batch rm` 只删除该批次快照和 caption，保留素材与运行历史。各命令完整参数见 `dsf <组> <命令> --help`。

### 对接训练器

ZIP 解压后，把平铺目录直接指定为训练器的图片目录。以下为 [kohya sd-scripts 数据集配置](https://github.com/kohya-ss/sd-scripts/blob/main/docs/config_README-en.md) 的最小示例；显式设置 `.txt` caption 扩展名，使用配置文件方式时不需要 `10_dog` 式子目录。视频数据须使用支持相应输入格式的视频训练器。

```toml
[general]
caption_extension = '.txt'

[[datasets]]
resolution = 512
batch_size = 1

[[datasets.subsets]]
image_dir = './training'
num_repeats = 1
```

## CLI 退出码

`0` 成功（stdout 只承载结果正文）；`1` 运行失败（stderr 给可操作中文消息）；`2` 命令用法错误；`130` 用户取消长任务或跑批中断。

## 数据位置

工具自身数据存 `~/.dataset_factory/`（环境变量 `DATASET_FACTORY_HOME` 可覆盖）：`endpoints/<配置名>/`（每套端点配置的 config.json 与 credentials 密钥）+ `endpoints/active`（当前使用指针）、`prompts/`、`skills/`、`sessions/`、`logs/`（服务运行日志，滚动保留）。

## 界面

Web 端支持跟随系统、亮色、暗色三种主题，可折叠侧栏。可用页面为「策略」工作台、「打标」以及「设置」（端点配置、技能、服务运行）。其他流水线入口显示为规划占位；批次 ZIP 导出位于打标页的批次概览中。

## 架构

核心为纯 Python 库：llm、prompts、skills、sessions、labeling 承载单次打标，workdir、strategies、runs、export、tasks 承载批量生产与长任务。分层规则由 import-linter 在 CI 守护，CLI（Typer）与 HTTP（FastAPI）是两个薄入口层。Web 端为 Vite + React + TypeScript 工程（`frontend/`，`npm run dev` 开发 / `npm run build` 产出 `dist/` 由后端托管）。详见 `backend/docs/`（API 参考由 docstring 自动生成）。

## 开发：改了 API 要更新契约快照

`backend/openapi.json` 是前后端 API 契约的单一事实来源（前端类型生成与 CI 破坏性变更检测都基于它）。改动任何路由后运行：

```
cd backend && uv run python scripts/export_openapi.py
```

并把新快照一并提交；CI 的漂移检查（生成的 spec ≠ 快照即红）和 oasdiff 破坏性变更检查（ERR 级变更即红）都在守这条线。
