"""dsf CLI 入口层——把命令行参数翻译成核心库调用，不含业务逻辑。

命令一览（``dsf --help`` / ``dsf <命令> --help`` 看细节）：

- 打标：``dsf label``（单发，支持会话续接）、``dsf chat``（终端多轮）；
- 配置：``dsf config set`` / ``dsf config show``（密钥交互输入不回显）；
- 提示词库：``dsf prompt list / show / save / rm``；
- Skill 库：``dsf skill import / list / enable / disable / rm``；
- 会话：``dsf session list / show``。

退出码约定（外部 agent 按此解析）：

- ``0`` 成功（stdout 只承载结果正文，保持干净）；
- ``1`` 运行失败（用户错：配置缺失 / 输入非法 / 找不到资源 / 模型调用失败，stderr 给
  可操作中文消息；程序 bug 同样非 0，stderr 带原始 traceback 便于定位）；
- ``2`` 命令用法错误（参数缺失 / 拼错，由 Typer/click 默认行为给出）。
"""

from __future__ import annotations

import logging
import sys

import typer

from . import config, label, prompt, session, skill

app = typer.Typer(
    help="Dataset Factory —— AI 打标工具（发图 + 指令产出 caption，支持迭代改写）。",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(config.app, name="config")
app.add_typer(prompt.app, name="prompt")
app.add_typer(skill.app, name="skill")
app.add_typer(session.app, name="session")
app.command(name="label")(label.label)
app.command(name="chat")(label.chat)

# 核心库不配日志（只挂 NullHandler 的约定在库侧首次发日志时落地）；本文件是应用层，
# 在最早期配置日志到 stderr——stdout 留给结果正文（退出码约定见模块 docstring）。
logging.basicConfig(
    stream=sys.stderr,
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
