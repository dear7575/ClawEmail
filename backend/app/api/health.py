from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, object]:
    """返回后端健康检查状态。"""

    return {
        "ok": True,
        "runtime": "python"
    }
