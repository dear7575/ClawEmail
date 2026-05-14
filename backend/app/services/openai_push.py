import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from app.db.duck_repository import DuckRepository, duck_repository
from app.services.sub2 import convert_openai_oauth_to_sub2, sub2_service


logger = logging.getLogger(__name__)


def detect_openai_authorization_state(token: dict[str, Any]) -> str:
    """从已保存的 OpenAI 授权数据中提取可写入 Sub2 备注的登录状态。"""

    serialized = json.dumps(token, ensure_ascii=False).lower()
    if "add-phone" in serialized or "add_phone" in serialized:
        return "出现 add-phone"
    if token.get("accessToken") or token.get("access_token"):
        return "授权登录成功"
    return "授权状态未知"


def build_sub2_push_notes(address: dict[str, Any], token: dict[str, Any], push_mode: str) -> str:
    """生成推送到 Sub2 账号备注字段的状态说明。"""

    items = [
        f"ClawEmail：{detect_openai_authorization_state(token)}",
        f"推送模式：{push_mode}",
        f"Duck 邮箱：{address['address']}",
    ]
    forwarding_mailbox = address.get("forwarding_mailbox_email")
    if forwarding_mailbox:
        items.append(f"接收邮箱：{forwarding_mailbox}")
    return "；".join(items)


def attach_sub2_account_notes(data: dict[str, Any], notes: str) -> dict[str, Any]:
    """给 Sub2 导入数据中的账号附加备注，保留可能已有的备注内容。"""

    accounts = data.get("accounts") if isinstance(data.get("accounts"), list) else []
    return {
        **data,
        "accounts": [
            {
                **account,
                "notes": "\n".join(
                    item for item in [str(account.get("notes") or "").strip(), notes] if item
                ),
            }
            for account in accounts
            if isinstance(account, dict)
        ],
    }


class OpenAiDuckPushBody(BaseModel):
    """Duck 地址推送到 Sub2 的请求体。"""

    duck_address_id: int = Field(alias="duckAddressId", gt=0)
    group_id: int | None = Field(default=None, alias="groupId", gt=0)


class OpenAiPushService:
    """OpenAI Duck 地址推送服务；优先使用已保存 OAuth 授权信息推送 Sub2。"""

    def __init__(self, repository: DuckRepository = duck_repository) -> None:
        """初始化 OpenAI 推送服务。"""

        self.repository = repository

    def push_duck_address_to_sub2(self, duck_address_id: int, group_id: int | None = None) -> dict[str, Any]:
        """将 Duck 地址保存的 OpenAI OAuth JSON 转换并推送到 Sub2。

        参数:
            duck_address_id: 本地 Duck 地址 ID。
            group_id: 可选 Sub2 分组 ID；为空时使用默认分组。

        返回:
            推送结果、推送模式和目标邮箱。
        """

        logger.info("开始推送 Duck OpenAI 凭据到 Sub2：duckAddressId=%s groupId=%s", duck_address_id, group_id)
        address = self.repository.get_address(duck_address_id)
        if not address or address["status"] != "active":
            logger.warning("Duck 地址推送失败，记录不存在或不可用：duckAddressId=%s", duck_address_id)
            raise ValueError("Duck 邮箱记录不存在或已不可用")
        raw_auth_json = address.get("openai_auth_json")
        if not raw_auth_json:
            logger.warning("Duck 地址推送失败，缺少 OpenAI OAuth JSON：duckAddressId=%s address=%s", duck_address_id, address.get("address"))
            raise RuntimeError(
                "该 Duck 邮箱还没有保存 OpenAI 授权信息。当前 FastAPI 迁移版支持推送已保存 OAuth JSON；"
                "自动注册/登录 OpenAI 的完整 OTP 流程仍保留在旧 Node 后端，迁移完成前不要关闭旧服务。"
            )
        try:
            token = json.loads(raw_auth_json)
        except ValueError as exc:
            logger.warning("Duck 地址推送失败，OpenAI OAuth JSON 非法：duckAddressId=%s", duck_address_id)
            raise ValueError("该 Duck 邮箱保存的 OpenAI 授权信息不是合法 JSON") from exc
        if not isinstance(token, dict):
            logger.warning("Duck 地址推送失败，OpenAI OAuth JSON 不是对象：duckAddressId=%s", duck_address_id)
            raise ValueError("该 Duck 邮箱保存的 OpenAI 授权信息必须是 JSON 对象")
        token.setdefault("email", address["address"])
        push_mode = "oauth_token"
        data = attach_sub2_account_notes(
            convert_openai_oauth_to_sub2(token),
            build_sub2_push_notes(address, token, push_mode),
        )
        result = sub2_service.push_data(data, group_id)
        push_email = token.get("email") or address["address"]
        self.repository.mark_sub2_pushed(duck_address_id, push_mode, str(push_email))
        logger.info("Duck OpenAI 凭据推送完成：duckAddressId=%s email=%s", duck_address_id, push_email)
        return {
            "email": push_email,
            "pushMode": push_mode,
            "telegram": {"sent": False},
            **result,
        }


openai_push_service = OpenAiPushService()
