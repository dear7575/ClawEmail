import logging

from app.services.listener_settings import (
    ListenerSettings,
    ListenerSettingsUpdate,
    listener_settings_service,
)
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.listeners import listener_manager
from app.services.sse import sse_hub

router = APIRouter(tags=["events"])
logger = logging.getLogger(__name__)


@router.get("/api/listeners")
def list_listeners() -> dict[str, list]:
    """列出当前进程内邮件监听器状态。"""

    return {"items": listener_manager.snapshot()}


@router.get("/api/events")
async def stream_events() -> StreamingResponse:
    """建立 SSE 连接，用于向前端推送实时事件。"""

    logger.info("API 建立 SSE 事件流")
    queue = sse_hub.add()

    async def event_generator():
        """持续从队列读取 SSE 消息，连接结束时自动注销客户端。"""

        try:
            while True:
                message = await queue.get()
                if message is None:
                    break
                yield message
        finally:
            sse_hub.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache, no-transform",
            "x-accel-buffering": "no",
        },
    )


@router.get("/api/listener-settings", response_model=ListenerSettings)
def get_listener_settings() -> ListenerSettings:
    """读取邮件监听器偏好设置。"""

    return listener_settings_service.get()


@router.put("/api/listener-settings", response_model=ListenerSettings)
def update_listener_settings(body: ListenerSettingsUpdate) -> ListenerSettings:
    """更新邮件监听器偏好设置。"""

    logger.info("API 更新监听器设置：logMode=%s reconnectMode=%s", body.logMode, body.reconnectMode)
    return listener_settings_service.save(body)
