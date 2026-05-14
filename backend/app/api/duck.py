import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from app.services.duck import (
    DuckAccountCreate,
    DuckAccountTokenUpdate,
    DuckAddressCreate,
    DuckAddressUpdate,
    OpenAiCredentialsUpdate,
    duck_service,
)

router = APIRouter(tags=["duck"])
logger = logging.getLogger(__name__)


@router.get("/api/duck/accounts")
def list_duck_accounts() -> dict[str, list[dict[str, Any]]]:
    """列出 Duck 账号。"""

    return {"items": duck_service.list_accounts()}


@router.post("/api/duck/accounts", status_code=status.HTTP_201_CREATED)
def create_duck_account(body: DuckAccountCreate) -> dict[str, Any]:
    """创建 Duck 账号。"""

    logger.info("API 创建 Duck 账号：label=%s", body.label)
    return duck_service.create_account(body)


@router.patch("/api/duck/accounts/{account_id}")
def update_duck_account(account_id: str, body: DuckAccountTokenUpdate) -> dict[str, Any]:
    """更新 Duck 账号 Token。"""

    logger.info("API 更新 Duck 账号 Token：accountId=%s", account_id)
    account = duck_service.update_account_token(account_id, body)
    if not account:
        raise HTTPException(status_code=404, detail="Duck account not found")
    return account


@router.delete("/api/duck/accounts/{account_id}")
def delete_duck_account(account_id: str) -> dict[str, bool]:
    """删除 Duck 账号。"""

    logger.info("API 删除 Duck 账号：accountId=%s", account_id)
    if not duck_service.delete_account(account_id):
        raise HTTPException(status_code=404, detail="Duck account not found")
    return {"success": True}


@router.get("/api/duck/addresses")
def list_duck_addresses(
    accountId: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    keyword: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    """列出 Duck 私有地址。"""

    return duck_service.list_addresses(account_id=accountId, limit=limit, offset=offset, keyword=keyword)


@router.post("/api/duck/accounts/{account_id}/addresses", status_code=status.HTTP_201_CREATED)
def create_duck_address(account_id: str, body: DuckAddressCreate) -> dict[str, Any]:
    """生成 Duck 私有地址。"""

    logger.info("API 生成 Duck 地址：accountId=%s forwarding=%s", account_id, body.forwarding_mailbox_email)
    address = duck_service.generate_address(account_id, body)
    if not address:
        raise HTTPException(status_code=404, detail="Duck account not found or disabled")
    return address


@router.patch("/api/duck/addresses/{address_id}")
def update_duck_address(address_id: int, body: DuckAddressUpdate) -> dict[str, Any]:
    """更新 Duck 私有地址元信息。"""

    logger.info("API 更新 Duck 地址：addressId=%s", address_id)
    address = duck_service.update_address(address_id, body)
    if not address:
        raise HTTPException(status_code=404, detail="Duck address not found")
    return address


@router.delete("/api/duck/addresses/{address_id}")
def delete_duck_address(address_id: int) -> dict[str, bool]:
    """删除 Duck 私有地址。"""

    logger.info("API 删除 Duck 地址：addressId=%s", address_id)
    if not duck_service.delete_address(address_id):
        raise HTTPException(status_code=404, detail="Duck address not found")
    return {"success": True}


@router.get("/api/duck/addresses/{address_id}/openai-password")
def get_duck_openai_password(address_id: int) -> dict[str, str]:
    """读取 Duck 地址保存的 OpenAI 密码。"""

    password = duck_service.get_openai_password(address_id)
    if not password:
        raise HTTPException(status_code=404, detail="该 Duck 邮箱没有保存 OpenAI 密码")
    return {"password": password}


@router.get("/api/duck/addresses/{address_id}/openai-auth-json")
def get_duck_openai_auth_json(address_id: int) -> dict[str, str]:
    """读取 Duck 地址保存的 OpenAI OAuth JSON。"""

    auth_json = duck_service.get_openai_auth_json(address_id)
    if not auth_json:
        raise HTTPException(status_code=404, detail="该 Duck 邮箱没有保存 OpenAI 授权信息")
    return {"authJson": auth_json}


@router.patch("/api/duck/addresses/{address_id}/openai-credentials")
def update_duck_openai_credentials(address_id: int, body: OpenAiCredentialsUpdate) -> dict[str, Any]:
    """更新 Duck 地址绑定的 OpenAI 凭据。"""

    logger.info("API 更新 Duck 地址 OpenAI 凭据：addressId=%s", address_id)
    address = duck_service.update_openai_credentials(address_id, body)
    if not address:
        raise HTTPException(status_code=404, detail="Duck address not found")
    return address
