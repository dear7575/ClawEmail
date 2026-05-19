from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.config import get_settings
from app.db.settings_repository import SettingsRepository, create_settings_engine, settings_repository


LEGACY_CONNECTION_ID = "legacy"


def ensure_connection_table(engine: Engine) -> None:
    """创建 Claw 连接表和索引。"""

    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS connections (
              id TEXT PRIMARY KEY,
              label TEXT,
              user_email TEXT,
              workspace_id TEXT,
              workspace_name TEXT,
              parent_mailbox_id TEXT,
              root_prefix TEXT,
              domain TEXT NOT NULL DEFAULT 'claw.163.com',
              api_key TEXT,
              dashboard_cookie TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              last_synced_at TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_connections_status ON connections(status)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_connections_user_email ON connections(user_email)"))


class ClawRepository:
    """读写 Claw 连接表的迁移兼容仓储。"""

    def __init__(
        self,
        engine: Engine | None = None,
        settings_repo: SettingsRepository = settings_repository,
    ) -> None:
        """初始化 Claw 连接仓储并确保表结构存在。"""

        self.engine = engine or create_settings_engine()
        self.settings_repo = settings_repo
        ensure_connection_table(self.engine)

    def list_connections(self, include_disconnected: bool = False) -> list[dict[str, Any]]:
        """列出 Claw 连接记录。"""

        where = "" if include_disconnected else "WHERE status != 'disconnected'"
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(f"""
                SELECT * FROM connections
                {where}
                ORDER BY created_at ASC
            """)).mappings().all()]

    def list_display_connections(self) -> list[dict[str, Any]]:
        """列出用于前端展示的连接记录，同一账号和 workspace 只保留一条。"""

        rows = self.list_connections(include_disconnected=True)
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            key = (
                str(row.get("user_email") or row.get("id") or "").strip().lower(),
                str(row.get("workspace_id") or row.get("id") or "").strip().lower(),
            )
            current = grouped.get(key)
            if current is None or self._display_sort_key(row) > self._display_sort_key(current):
                grouped[key] = row
        return sorted(grouped.values(), key=lambda item: str(item.get("created_at") or ""))

    @staticmethod
    def _display_sort_key(connection: dict[str, Any]) -> tuple[int, str, str]:
        """计算展示连接的优先级，优先展示未断开的非 legacy 新记录。"""

        active_score = 1 if connection.get("status") != "disconnected" else 0
        canonical_score = 1 if connection.get("id") != LEGACY_CONNECTION_ID else 0
        updated_at = str(connection.get("updated_at") or connection.get("created_at") or "")
        return active_score, canonical_score, updated_at

    def get_connection(self, connection_id: str) -> dict[str, Any] | None:
        """按连接 ID 读取 Claw 连接。"""

        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM connections WHERE id = :id"),
                {"id": connection_id},
            ).mappings().first()
        return dict(row) if row else None

    def get_default_connection(self) -> dict[str, Any] | None:
        """读取最早创建的未断开连接。"""

        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT * FROM connections
                WHERE status != 'disconnected'
                ORDER BY created_at ASC
                LIMIT 1
            """)).mappings().first()
        return dict(row) if row else None

    def upsert_connection(self, input_value: dict[str, Any]) -> dict[str, Any]:
        """新增或更新 Claw 连接记录。"""

        settings = get_settings()
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO connections
                  (
                    id, label, user_email, workspace_id, workspace_name, parent_mailbox_id,
                    root_prefix, domain, api_key, dashboard_cookie, status, last_synced_at
                  )
                VALUES
                  (
                    :id, :label, :user_email, :workspace_id, :workspace_name, :parent_mailbox_id,
                    :root_prefix, :domain, :api_key, :dashboard_cookie, :status, :last_synced_at
                  )
                ON CONFLICT(id) DO UPDATE SET
                  label = excluded.label,
                  user_email = excluded.user_email,
                  workspace_id = excluded.workspace_id,
                  workspace_name = excluded.workspace_name,
                  parent_mailbox_id = excluded.parent_mailbox_id,
                  root_prefix = excluded.root_prefix,
                  domain = excluded.domain,
                  api_key = excluded.api_key,
                  dashboard_cookie = excluded.dashboard_cookie,
                  status = excluded.status,
                  last_synced_at = excluded.last_synced_at,
                  updated_at = CURRENT_TIMESTAMP
            """), {
                "id": input_value["id"],
                "label": input_value.get("label") or input_value.get("user_email") or input_value.get("workspace_name") or input_value["id"],
                "user_email": input_value.get("user_email"),
                "workspace_id": input_value.get("workspace_id"),
                "workspace_name": input_value.get("workspace_name"),
                "parent_mailbox_id": input_value.get("parent_mailbox_id"),
                "root_prefix": input_value.get("root_prefix"),
                "domain": input_value.get("domain") or settings.claw_domain,
                "api_key": input_value.get("api_key"),
                "dashboard_cookie": input_value.get("dashboard_cookie"),
                "status": input_value.get("status") or "active",
                "last_synced_at": input_value.get("last_synced_at"),
            })
        return self.get_connection(input_value["id"]) or {}

    def mark_disconnected(self, connection_id: str) -> None:
        """将指定 Claw 连接标记为 disconnected。"""

        with self.engine.begin() as connection:
            connection.execute(text("""
                UPDATE connections
                SET status = 'disconnected', updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {"id": connection_id})

    def delete_connection(self, connection_id: str) -> bool:
        """删除指定 Claw 连接本地记录。"""

        with self.engine.begin() as connection:
            result = connection.execute(text("DELETE FROM connections WHERE id = :id"), {"id": connection_id})
        return result.rowcount > 0

    def mark_duplicate_identity_disconnected(
        self,
        user_email: str | None,
        workspace_id: str | None,
        keep_connection_id: str,
    ) -> list[str]:
        """断开同一账号和 workspace 下除当前连接外的旧连接。"""

        if not user_email or not workspace_id:
            return []
        with self.engine.begin() as connection:
            rows = connection.execute(text("""
                SELECT id FROM connections
                WHERE lower(coalesce(user_email, '')) = lower(:user_email)
                  AND workspace_id = :workspace_id
                  AND id != :keep_connection_id
                  AND status != 'disconnected'
            """), {
                "user_email": user_email,
                "workspace_id": workspace_id,
                "keep_connection_id": keep_connection_id,
            }).mappings().all()
            duplicate_ids = [str(row["id"]) for row in rows]
            if duplicate_ids:
                connection.execute(text("""
                    UPDATE connections
                    SET status = 'disconnected', updated_at = CURRENT_TIMESTAMP
                    WHERE lower(coalesce(user_email, '')) = lower(:user_email)
                      AND workspace_id = :workspace_id
                      AND id != :keep_connection_id
                """), {
                    "user_email": user_email,
                    "workspace_id": workspace_id,
                    "keep_connection_id": keep_connection_id,
                })
        return duplicate_ids

    def fallback_connection(self) -> dict[str, Any] | None:
        """从旧 app_settings 或环境变量恢复 legacy Claw 连接。"""

        existing = self.get_connection(LEGACY_CONNECTION_ID)
        if existing:
            return existing
        settings = get_settings()
        api_key = self.settings_repo.get("claw.apiKey") or settings.claw_api_key
        dashboard_cookie = self.settings_repo.get("claw.dashboardCookie") or settings.claw_dashboard_cookie
        if not api_key and not dashboard_cookie:
            return None
        return self.upsert_connection({
            "id": LEGACY_CONNECTION_ID,
            "label": self.settings_repo.get("claw.userEmail") or "默认连接",
            "user_email": self.settings_repo.get("claw.userEmail"),
            "workspace_id": self.settings_repo.get("claw.workspaceId") or settings.claw_workspace_id,
            "workspace_name": self.settings_repo.get("claw.workspaceName"),
            "parent_mailbox_id": self.settings_repo.get("claw.parentMailboxId") or settings.claw_parent_mailbox_id,
            "root_prefix": self.settings_repo.get("claw.rootPrefix") or settings.claw_root_prefix,
            "domain": self.settings_repo.get("claw.domain") or settings.claw_domain,
            "api_key": api_key,
            "dashboard_cookie": dashboard_cookie,
            "status": "active",
        })

    def resolve_connection(self, connection_id: str | None = None) -> dict[str, Any] | None:
        """解析当前要使用的 Claw 连接。"""

        if connection_id:
            return self.get_connection(connection_id)
        return self.get_default_connection() or self.fallback_connection()


claw_repository = ClawRepository()
