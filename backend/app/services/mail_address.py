from email.utils import getaddresses
from typing import Any


def normalize_recipient_address(value: Any) -> str:
    """将邮件收件人字段转换为纯邮箱地址。"""

    if isinstance(value, (list, tuple)):
        return ", ".join(filter(None, (normalize_recipient_address(item) for item in value)))
    if isinstance(value, dict):
        for key in ("address", "email", "value"):
            if value.get(key):
                return normalize_recipient_address(value[key])
        return ""
    raw = str(value or "").strip()
    if not raw:
        return ""
    addresses = [address.strip() for _, address in getaddresses([raw]) if address.strip()]
    return ", ".join(addresses) if addresses else raw.strip("<> ")
