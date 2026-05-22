from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, object]:
    """返回后端健康检查状态，避免同步线程池拥塞影响容器探活。"""

    return {
        "ok": True,
        "runtime": "python"
    }
