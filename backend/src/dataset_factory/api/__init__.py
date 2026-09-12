"""api 入口层：HTTP 翻译——把网页请求翻译成核心库调用，不含业务逻辑。

对外接口：create_app(frontend_dir)——应用工厂（组装会探测磁盘，frontend/dist 存在才挂载
静态页；每个入口在各自启动点显式调用）。端点清单与错误映射见 app 模块。
"""

from .app import create_app

__all__ = ["create_app"]
