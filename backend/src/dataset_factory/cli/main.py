"""dsf CLI 入口层——把命令行参数翻译成核心库调用，不含业务逻辑。

命令一览（``dsf --help`` / ``dsf <命令> --help`` 看细节）：

- 打标：``dsf label``（单发，支持会话续接）、``dsf chat``（终端多轮）；
- 配置：``dsf config set`` / ``dsf config show``（密钥交互输入不回显）；
- 提示词库：``dsf prompt list / show / save / rename / rm``；
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
import logging.handlers
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
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


def add_server_file_handler(path: Path) -> logging.handlers.RotatingFileHandler:
    """把日志同时写入指定文件（serve 专用：数据根 logs/server.log，滚动 5 MiB × 3 份）。

    无窗口后台运行时终端看不到输出，文件是唯一的完整日志来源；CLI 单发命令不挂
    （它们的输出就在终端，不留文件）。挂在 root logger 上，与 stderr handler 并行，
    请求 id 过滤器与格式同 stderr 侧一致。

    Args:
        path: 日志文件路径（父目录不存在则创建）。

    Returns:
        挂上的 handler（serve 生命周期内保留；测试用完移除）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logging.getLogger().addHandler(handler)
    return handler


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
    """启动本地 Web 服务（HTTP API + 前端界面）。

    前台运行 Ctrl+C 停止；无窗口后台运行（start 脚本默认）用界面电源按钮或 stop 脚本。
    """
    import uvicorn

    from ..api import create_app
    from ..api.routes_service import server_log_path

    if log_level is not None:
        _configure_logging(log_level)
    app = create_app()
    log_file = server_log_path()
    app.state.service_info = {
        "version": app.version,
        "host": host,
        "port": port,
        "started_at": datetime.now(UTC).isoformat(),
        "log_file": str(log_file),
    }
    add_server_file_handler(log_file)
    typer.secho(
        f"Web 服务启动：http://{host}:{port}（前台 Ctrl+C 停止；后台用界面电源按钮或 stop 脚本）",
        fg=typer.colors.YELLOW,
    )
    # log_config=None：不让 uvicorn 覆盖应用刚配好的日志（否则级别与格式会被打回它的默认）。
    # access_log=False：访问日志由 RequestLogMiddleware 接管——uvicorn 自带那条不含耗时。
    config = uvicorn.Config(
        app, host=host, port=port, log_config=None, access_log=False
    )
    server = uvicorn.Server(config)
    # 把 uvicorn 实例交给应用：/api/service/shutdown 置 should_exit 即「手头请求做完再退出」。
    app.state.uvicorn_server = server
    server.run()


# 应用层在最早期配置日志（stdout 留给结果正文，退出码约定见模块 docstring）。
_configure_logging(os.environ.get(_LOG_LEVEL_ENV, _DEFAULT_LOG_LEVEL))
