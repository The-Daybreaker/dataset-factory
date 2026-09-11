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
import os
import sys
from typing import Annotated

import typer

from .._obs import RequestIdFilter
from . import config, label, prompt, session, skill

# 日志级别与格式：级别优先级 = `dsf serve --log-level` > 环境变量 DSF_LOG_LEVEL > 默认 INFO。
# 默认给 INFO 而不是 WARNING，是为了让「服务收到了什么请求、各段花多久」开箱可见——可观测性
# 的目的就是出了问题能查，默认静音等于白做（这也是 T10 要修的原始缺口）。
_LOG_FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
_LOG_LEVEL_ENV = "DSF_LOG_LEVEL"
_DEFAULT_LOG_LEVEL = "INFO"


def _configure_logging(level_name: str) -> None:
    """配置应用日志输出到 stderr（stdout 留给结果正文，供外部 agent 干净解析）。

    应用层负责日志配置、核心库只发记录（见 design「可观测性与日志」分工）；请求 id 由
    `RequestIdFilter` 自动注入每行，非请求上下文（如 CLI 单发）显示为 `-`。

    Args:
        level_name: 日志级别名（DEBUG / INFO / WARNING / ERROR，大小写不敏感）；无法识别时
            回落到 INFO。
    """
    level = getattr(logging, level_name.upper(), None)
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logging.basicConfig(
        level=level if isinstance(level, int) else logging.INFO,
        handlers=[handler],
        force=True,  # 覆盖已有配置：uvicorn 等库可能已配过 root，这里以应用为准
    )


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


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option("--host", help="监听地址")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="监听端口")] = 8000,
    log_level: Annotated[
        str | None,
        typer.Option(
            "--log-level",
            help=(
                "日志级别（DEBUG / INFO / WARNING / ERROR）；"
                f"缺省读环境变量 {_LOG_LEVEL_ENV}，再缺省 {_DEFAULT_LOG_LEVEL}"
            ),
        ),
    ] = None,
) -> None:
    """启动本地 Web 服务（HTTP API + 前端界面），Ctrl+C 停止。"""
    import uvicorn

    from ..api import create_app

    if log_level is not None:
        _configure_logging(log_level)
    typer.secho(
        f"Web 服务启动：http://{host}:{port}（Ctrl+C 停止）", fg=typer.colors.YELLOW
    )
    # log_config=None：不让 uvicorn 覆盖应用刚配好的日志（否则级别与格式会被打回它的默认）。
    # access_log=False：访问日志由 RequestLogMiddleware 接管——uvicorn 自带那条不含耗时。
    uvicorn.run(create_app(), host=host, port=port, log_config=None, access_log=False)


# 应用层在最早期配置日志（stdout 留给结果正文，退出码约定见模块 docstring）。
_configure_logging(os.environ.get(_LOG_LEVEL_ENV, _DEFAULT_LOG_LEVEL))
