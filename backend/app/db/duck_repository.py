from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.settings_repository import create_settings_engine


def ensure_duck_tables(engine: Engine) -> None:
    """创建 Duck 账号、地址表和兼容迁移列。"""

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys = ON"))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS duck_accounts (
              id TEXT PRIMARY KEY,
              label TEXT NOT NULL,
              token TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              last_error TEXT,
              last_used_at TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_duck_accounts_status ON duck_accounts(status)"))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS duck_addresses (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              account_id TEXT NOT NULL,
              address TEXT NOT NULL UNIQUE,
              local_part TEXT NOT NULL,
              forwarding_mailbox_email TEXT,
              note TEXT,
              openai_password TEXT,
              openai_auth_json TEXT,
              sub2_pushed_at TEXT,
              sub2_push_mode TEXT,
              sub2_push_email TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              raw_json TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(account_id) REFERENCES duck_accounts(id) ON DELETE CASCADE
            )
        """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_duck_addresses_account_id ON duck_addresses(account_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_duck_addresses_status ON duck_addresses(status)"))
        ensure_column(connection, "duck_addresses", "openai_password", "TEXT")
        ensure_column(connection, "duck_addresses", "openai_auth_json", "TEXT")
        ensure_column(connection, "duck_addresses", "sub2_pushed_at", "TEXT")
        ensure_column(connection, "duck_addresses", "sub2_push_mode", "TEXT")
        ensure_column(connection, "duck_addresses", "sub2_push_email", "TEXT")


def ensure_column(connection, table: str, column: str, definition: str) -> None:
    """在 SQLite 表中补齐兼容旧数据所需的列。"""

    rows = connection.execute(text(f"PRAGMA table_info({table})")).mappings().all()
    if any(row["name"] == column for row in rows):
        return
    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


class DuckRepository:
    """读写旧版 Duck 账号与地址表，保持 FastAPI 迁移期间的数据兼容。"""

    def __init__(self, engine: Engine | None = None) -> None:
        """初始化 Duck 仓储并确保表结构存在。"""

        self.engine = engine or create_settings_engine()
        ensure_duck_tables(self.engine)

    def list_accounts(self) -> list[dict[str, Any]]:
        """列出未禁用的 Duck 账号。"""

        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(text("""
                SELECT * FROM duck_accounts
                WHERE status != 'disabled'
                ORDER BY created_at DESC
            """)).mappings().all()]

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        """按账号 ID 读取 Duck 账号。"""

        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM duck_accounts WHERE id = :id"),
                {"id": account_id},
            ).mappings().first()
        return dict(row) if row else None

    def create_account(self, account_id: str, label: str, token: str) -> dict[str, Any]:
        """创建 Duck 账号记录。"""

        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO duck_accounts (id, label, token, status)
                VALUES (:id, :label, :token, 'active')
            """), {"id": account_id, "label": label, "token": token})
        return self.get_account(account_id) or {}

    def update_account_token(self, account_id: str, token: str) -> dict[str, Any] | None:
        """更新 Duck 账号 Token 并恢复 active 状态。"""

        with self.engine.begin() as connection:
            connection.execute(text("""
                UPDATE duck_accounts
                SET token = :token, status = 'active', last_error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {"id": account_id, "token": token})
        return self.get_account(account_id)

    def delete_account(self, account_id: str) -> bool:
        """删除 Duck 账号及其私有地址。"""

        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM duck_addresses WHERE account_id = :id"), {"id": account_id})
            result = connection.execute(text("DELETE FROM duck_accounts WHERE id = :id"), {"id": account_id})
        return result.rowcount > 0

    def mark_account_used(self, account_id: str) -> None:
        """记录 Duck 账号最近一次成功使用时间。"""

        with self.engine.begin() as connection:
            connection.execute(text("""
                UPDATE duck_accounts
                SET last_error = NULL, last_used_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {"id": account_id})

    def mark_account_error(self, account_id: str, error: str) -> None:
        """记录 Duck 账号最近一次失败信息。"""

        with self.engine.begin() as connection:
            connection.execute(text("""
                UPDATE duck_accounts
                SET last_error = :error, updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {"id": account_id, "error": error})

    def list_addresses(
        self,
        account_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        """分页列出 Duck 私有地址，可按账号和关键词过滤。"""

        where_parts: list[str] = []
        params: dict[str, Any] = {}
        if account_id:
            where_parts.append("account_id = :account_id")
            params["account_id"] = account_id
        if keyword:
            where_parts.append("""(
                address LIKE :keyword
                OR local_part LIKE :keyword
                OR forwarding_mailbox_email LIKE :keyword
                OR note LIKE :keyword
                OR sub2_push_email LIKE :keyword
            )""")
            params["keyword"] = f"%{keyword}%"
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        limit_sql = ""
        query_params = dict(params)
        if limit is not None:
            limit_sql = "LIMIT :limit OFFSET :offset"
            query_params["limit"] = limit
            query_params["offset"] = offset
        with self.engine.connect() as connection:
            items = [dict(row) for row in connection.execute(text(f"""
                SELECT * FROM duck_addresses
                {where}
                ORDER BY created_at DESC, id DESC
                {limit_sql}
            """), query_params).mappings().all()]
            count = connection.execute(
                text(f"SELECT COUNT(*) AS count FROM duck_addresses {where}"),
                params,
            ).mappings().first()
        total = count["count"] if count else 0
        return {"items": items, "count": total, "total": total, "limit": limit, "offset": offset}

    def get_address(self, address_id: int) -> dict[str, Any] | None:
        """按本地 ID 读取 Duck 私有地址。"""

        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM duck_addresses WHERE id = :id"),
                {"id": address_id},
            ).mappings().first()
        return dict(row) if row else None

    def get_address_by_address(self, address: str) -> dict[str, Any] | None:
        """按完整 Duck 地址读取记录。"""

        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM duck_addresses WHERE address = :address"),
                {"address": address.strip().lower()},
            ).mappings().first()
        return dict(row) if row else None

    def save_address(self, input_value: dict[str, Any]) -> dict[str, Any]:
        """保存 Duck 私有地址；地址重复时更新元信息并恢复 active。"""

        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO duck_addresses
                  (account_id, address, local_part, forwarding_mailbox_email, note, openai_password, openai_auth_json, status, raw_json)
                VALUES
                  (:account_id, :address, :local_part, :forwarding_mailbox_email, :note, NULL, NULL, 'active', :raw_json)
                ON CONFLICT(address) DO UPDATE SET
                  account_id = excluded.account_id,
                  local_part = excluded.local_part,
                  forwarding_mailbox_email = excluded.forwarding_mailbox_email,
                  note = excluded.note,
                  status = 'active',
                  raw_json = excluded.raw_json,
                  updated_at = CURRENT_TIMESTAMP
            """), input_value)
        return self.get_address_by_address(input_value["address"]) or {}

    def update_address(self, address_id: int, input_value: dict[str, Any]) -> dict[str, Any] | None:
        """更新 Duck 私有地址元信息。"""

        existing = self.get_address(address_id)
        if not existing:
            return None
        with self.engine.begin() as connection:
            connection.execute(text("""
                UPDATE duck_addresses
                SET forwarding_mailbox_email = :forwarding_mailbox_email,
                    note = :note,
                    status = :status,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {
                "id": address_id,
                "forwarding_mailbox_email": input_value.get("forwarding_mailbox_email", existing["forwarding_mailbox_email"]),
                "note": input_value.get("note", existing["note"]),
                "status": input_value.get("status", existing["status"]),
            })
        return self.get_address(address_id)

    def update_openai_credentials(self, address_id: int, input_value: dict[str, Any]) -> dict[str, Any] | None:
        """更新 Duck 地址绑定的 OpenAI 密码或 OAuth JSON。"""

        existing = self.get_address(address_id)
        if not existing:
            return None
        with self.engine.begin() as connection:
            connection.execute(text("""
                UPDATE duck_addresses
                SET openai_password = :openai_password,
                    openai_auth_json = :openai_auth_json,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {
                "id": address_id,
                "openai_password": input_value.get("password", existing["openai_password"]),
                "openai_auth_json": input_value.get("auth_json", existing["openai_auth_json"]),
            })
        return self.get_address(address_id)

    def mark_sub2_pushed(self, address_id: int, mode: str, email: str) -> dict[str, Any] | None:
        """记录 Duck 地址最近一次成功推送到 Sub2 的状态。"""

        with self.engine.begin() as connection:
            connection.execute(text("""
                UPDATE duck_addresses
                SET sub2_pushed_at = CURRENT_TIMESTAMP,
                    sub2_push_mode = :mode,
                    sub2_push_email = :email,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {"id": address_id, "mode": mode, "email": email})
        return self.get_address(address_id)

    def delete_address(self, address_id: int) -> bool:
        """删除 Duck 私有地址。"""

        with self.engine.begin() as connection:
            result = connection.execute(text("DELETE FROM duck_addresses WHERE id = :id"), {"id": address_id})
        return result.rowcount > 0


duck_repository = DuckRepository()
