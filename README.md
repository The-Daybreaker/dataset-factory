# Dataset Factory

面向 LoRA 训练数据集生产的**打标流水线工具**：用 AI 把切好、归好类的图片 / 视频素材打成能直接送训练的描述（caption），并最终整理成目标模型要的格式。

> **开发状态**：早期开发中。一期聚焦「AI 打标能力」——发图 → 模型出描述 → 多轮迭代改写，配套结构化预置提示词库、Skill 管理、OpenAI 兼容 API 接入；批量打标、入库、质检、复核、交付等在后续阶段。

## 一期目标特性

- **AI 打标**：发一张图 + 指令，模型输出 caption，支持基于历史的多轮迭代改写。
- **结构化预置提示词库**：可增删改查，每轮打标必选一条基础提示词。
- **Skill 管理**：导入 [agentskills.io](https://agentskills.io/) 标准的 Skill 包，打标时可选启用。
- **OpenAI 兼容接入**：配置驱动切换端点，不绑定厂商 / 模型。
- **双入口对等**：Web 测试界面 + CLI（`dsf`）复用同一套打标核心。

## 架构

核心是**纯 Python 库**、与界面分离；CLI 与 HTTP 两个入口层复用它。

```
backend/
└── src/dataset_factory/
    ├── llm/        # OpenAI 兼容客户端（唯一与模型端点通信）
    ├── prompts/    # 预置提示词库
    ├── skills/     # Skill 包导入与启用
    ├── sessions/   # 会话持久化
    ├── labeling/   # 打标编排（引擎）
    ├── cli/        # CLI 入口层（dsf）
    └── api/        # HTTP 入口层（FastAPI）
```

分层依赖单向（入口层 → 编排 → 能力 / 数据域），核心库不反向依赖入口层，由 [import-linter](https://import-linter.readthedocs.io/) 在 CI 中守护。

## 开发

依赖：Python 3.12、[uv](https://docs.astral.sh/uv/)（包与环境管理）。

```bash
cd backend
uv sync                 # 按 uv.lock 安装依赖（含开发依赖）
uv run pytest --cov     # 测试 + 覆盖率
uv run lint-imports     # 架构分层契约检查
```

## CI

GitHub Actions 在 **Windows + Linux** 矩阵上自动运行测试、覆盖率与架构契约检查（见 `.github/workflows/ci.yml`）。
