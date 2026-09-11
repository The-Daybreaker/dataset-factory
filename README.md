# Dataset Factory

面向 LoRA 训练数据集的**打标流水线工具**：把预先切好、归好类的图片素材，打成能直接送训练的描述（caption），最终整理成目标模型要的格式。

当前版本（一期）交付 **AI 打标核心能力**：发一张图 + 指令，模型输出 caption，多轮迭代改写；配套结构化提示词库、Skill 扩展（agentskills.io 标准）、OpenAI 兼容 API 接入与 Web / CLI 双入口。批量打标与流水线界面在后续版本提供。

## 安装

要求：Python 3.12 + [uv](https://docs.astral.sh/uv/)。

```bash
cd backend
uv sync
```

## 快速上手

```bash
# 1. 配置 OpenAI 兼容端点（base_url / 模型名 / 密钥，密钥输入不回显）
dsf config set --base-url https://opencode.ai/zen/go/v1 --model deepseek-v4-flash-vision-exp

# 2a. 起 Web 测试界面（浏览器打开 http://127.0.0.1:8000）
dsf serve

# 2b. 或直接用 CLI 单发打标
dsf prompt save h3 -d "视频打标" -f 提示词正文.md
dsf label -p h3 -i 素材.jpg -m "给这张图打个标"
```

首次 `dsf label` 返回会话 id；带 `--session <id>` 再调即携带历史的迭代改写（`改成两句话`）。`--json` 输出结构化结果，供外部 agent 解析。

## 核心功能

- **聊天打标（Web + CLI 对等）**：发图 + 指令产出 caption；多轮迭代改写（`dsf chat` 终端多轮，输入 `@图片路径 指令` 附图）；会话自动落盘、重启可恢复。
- **提示词库**：每轮打标必选一个基础提示词（进 system 消息，会话内选定后每轮自动携带、可中途切换）。`dsf prompt list / show / save / rm`；保存旧版自动进 `_history/` 滚动备份。
- **Skill 扩展**：导入 agentskills.io 标准 skill 包，打标时可选启用（全文以 `<skill>` 标记注入）。`dsf skill import / list / enable / disable / rm`。
- **配置驱动切换端点**：任何 OpenAI 兼容端点，改 `dsf config set` 即换；密钥存本地 credentials 文件（Unix 0600），或用环境变量 `DSF_API_KEY`（程序 / 外部 agent 用）。
- **可复盘**：每轮实际发出的完整请求（请求信封）先落盘再调模型，失败也有「当时喂了什么」可查。

## CLI 退出码

`0` 成功（stdout 只承载结果正文）；`1` 运行失败（stderr 给可操作中文消息）；`2` 命令用法错误。

## 数据位置

工具自身数据存 `~/.dataset_factory/`（环境变量 `DATASET_FACTORY_HOME` 可覆盖）：`config.json`（端点配置）、`credentials`（密钥）、`prompts/`、`skills/`、`sessions/`。

## 架构

核心为纯 Python 库（llm / prompts / skills / sessions / labeling 五模块，分层规则由 import-linter 在 CI 守护），CLI（Typer）与 HTTP（FastAPI）是两个薄入口层，Web 测试界面为 Preact + vendored 免构建静态页。详见 `backend/docs/`（API 参考由 docstring 自动生成）。
