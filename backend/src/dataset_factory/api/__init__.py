"""api 入口层：HTTP 翻译——把网页请求翻译成核心库调用，不含业务逻辑。

对外接口：app（FastAPI 应用实例，uvicorn / TestClient 直接用）；create_app(frontend_dir)
可指定静态页目录（测试用）。端点清单与错误映射见 app 模块。
"""

from .app import app, create_app

__all__ = ["app", "create_app"]
