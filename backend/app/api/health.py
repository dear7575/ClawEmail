import os

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, object]:
    """返回后端健康检查状态，避免同步线程池拥塞影响容器探活。"""

    return {
        "ok": True,
        "runtime": "python",
        "revision": os.getenv("IMAGE_REVISION", "unknown")
    }


@router.get("/health/sync")
def sync_health() -> dict[str, object]:
    """通过同步路由校验业务线程池仍可调度请求。"""

    return {
        "ok": True,
        "runtime": "python-sync",
        "revision": os.getenv("IMAGE_REVISION", "unknown")
    }
