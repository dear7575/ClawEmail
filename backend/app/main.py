import logging
import sys
import time
from pathlib import Path


if __package__ in {None, ""}:
    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.claw_auth import router as claw_auth_router
from app.api.duck import router as duck_router
from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.mailboxes import router as mailboxes_router
from app.api.mails import router as mails_router
from app.api.openai_push import router as openai_push_router
from app.api.send import router as send_router
from app.api.sub2 import router as sub2_router
from app.api.system import router as system_router
from app.api.telegram import router as telegram_router
from app.core.config import get_settings
from app.core.logging import configure_logging


settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("app.main")


def create_app() -> FastAPI:
    """创建 FastAPI 应用并注册路由、中间件和异常处理器。"""

    app = FastAPI(title=settings.app_name)
    app.include_router(health_router)
    app.include_router(system_router)
    app.include_router(sub2_router)
    app.include_router(duck_router)
    app.include_router(telegram_router)
    app.include_router(events_router)
    app.include_router(claw_auth_router)
    app.include_router(mailboxes_router)
    app.include_router(mails_router)
    app.include_router(send_router)
    app.include_router(openai_push_router)

    @app.middleware("http")
    async def admin_authentication(request: Request, call_next):
        """对 /api/ 路径执行后台密码校验。"""

        if request.url.path.startswith("/api/"):
            header_password = request.headers.get("x-admin-password")
            query_password = request.query_params.get("token")
            if header_password != settings.admin_password and query_password != settings.admin_password:
                logger.warning("%s %s -> 401 unauthorized", request.method, request.url.path)
                return JSONResponse(status_code=401, content={"error": "unauthorized"})
        return await call_next(request)

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        """记录请求入口、响应状态和耗时。"""

        started_at = time.perf_counter()
        logger.info("%s %s <- incoming", request.method, request.url.path)
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info("%s %s -> %s %.2fms", request.method, request.url.path, response.status_code, elapsed_ms)
        return response

    @app.exception_handler(ValidationError)
    async def validation_error_handler(_request: Request, exc: ValidationError):
        """将 Pydantic 校验错误转换为统一 400 响应。"""

        logger.warning("validation error: %s", exc)
        return JSONResponse(
            status_code=400,
            content={"error": "invalid input", "details": exc.errors()}
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, exc: HTTPException):
        """将 FastAPI HTTPException 转换为统一错误响应。"""

        logger.warning("http error: %s", exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail}
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError):
        """将业务参数错误转换为 JSON 响应。"""

        logger.warning("value error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)}
        )

    @app.exception_handler(RuntimeError)
    async def runtime_error_handler(_request: Request, exc: RuntimeError):
        """将外部服务或运行时失败转换为 JSON 响应。"""

        logger.error("runtime error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)}
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, exc: Exception):
        """兜底捕获未处理异常，避免返回非结构化错误。"""

        logger.exception("unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)}
        )

    return app


app = create_app()


def run_server() -> None:
    """从 IDE 或 python app/main.py 直接启动 FastAPI 开发服务器。"""

    import uvicorn

    logger.info("启动 ClawEmail API：host=%s port=%s", settings.host, settings.port)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    run_server()
