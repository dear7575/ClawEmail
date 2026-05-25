import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.claw_repository import LEGACY_CONNECTION_ID
from app.db.settings_repository import create_settings_engine


def ensure_mail_tables(engine: Engine) -> None:
    """创建邮箱、邮件和附件表及索引。"""

    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS mailboxes (
              id TEXT PRIMARY KEY,
              connection_id TEXT,
              provider_mailbox_id TEXT,
              email TEXT NOT NULL UNIQUE,
              prefix TEXT NOT NULL,
              display_name TEXT,
              account_id TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              openclaw_status TEXT,
              install_command TEXT,
              auth_url TEXT,
              comm_level INTEGER,
              ext_receive_type INTEGER,
              ext_send_type INTEGER,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_mailboxes_email ON mailboxes(email)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_mailboxes_status ON mailboxes(status)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_mailboxes_connection_id ON mailboxes(connection_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_mailboxes_connection_email ON mailboxes(connection_id, email)"))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS mails (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              connection_id TEXT,
              provider_mail_id TEXT NOT NULL,
              mailbox_email TEXT NOT NULL,
              source TEXT,
              address TEXT,
              subject TEXT,
              text TEXT,
              html TEXT,
              raw_json TEXT NOT NULL,
              header_raw TEXT,
              has_attachments INTEGER NOT NULL DEFAULT 0,
              read_at TEXT,
              received_at TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(mailbox_email, provider_mail_id)
            )
        """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_mails_mailbox_email ON mails(mailbox_email)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_mails_created_at ON mails(created_at)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_mails_connection_id ON mails(connection_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_mails_connection_mailbox ON mails(connection_id, mailbox_email)"))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS attachments (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              mail_id INTEGER NOT NULL,
              provider_part_id TEXT NOT NULL,
              filename TEXT,
              content_type TEXT,
              size INTEGER,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(mail_id) REFERENCES mails(id) ON DELETE CASCADE
            )
        """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_attachments_mail_id ON attachments(mail_id)"))


class MailRepository:
    """旧版 mailboxes/mails/attachments 表的迁移兼容仓储。"""

    def __init__(self, engine: Engine | None = None) -> None:
        """初始化邮件仓储并确保表结构存在。"""

        self.engine = engine or create_settings_engine()
        ensure_mail_tables(self.engine)

    def upsert_mailbox(self, input_value: dict[str, Any]) -> dict[str, Any]:
        """新增或更新本地邮箱记录。"""

        connection_id = input_value.get("connection_id") or LEGACY_CONNECTION_ID
        provider_mailbox_id = input_value.get("provider_mailbox_id") or input_value["id"]
        local_id = f"{connection_id}:{provider_mailbox_id}" if input_value.get("connection_id") else input_value["id"]
        payload = {
            "id": local_id,
            "connection_id": connection_id,
            "provider_mailbox_id": provider_mailbox_id,
            "email": input_value["email"],
            "prefix": input_value["prefix"],
            "display_name": input_value.get("display_name"),
            "account_id": input_value.get("account_id"),
            "status": input_value.get("status") or "active",
            "openclaw_status": input_value.get("openclaw_status"),
            "install_command": input_value.get("install_command"),
            "auth_url": input_value.get("auth_url"),
            "comm_level": input_value.get("comm_level"),
            "ext_receive_type": input_value.get("ext_receive_type"),
            "ext_send_type": input_value.get("ext_send_type"),
        }
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO mailboxes
                  (
                    id, connection_id, provider_mailbox_id, email, prefix, display_name, account_id, status,
                    openclaw_status, install_command, auth_url, comm_level, ext_receive_type, ext_send_type
                  )
                VALUES
                  (
                    :id, :connection_id, :provider_mailbox_id, :email, :prefix, :display_name, :account_id, :status,
                    :openclaw_status, :install_command, :auth_url, :comm_level, :ext_receive_type, :ext_send_type
                  )
                ON CONFLICT(id) DO UPDATE SET
                  connection_id = excluded.connection_id,
                  provider_mailbox_id = excluded.provider_mailbox_id,
                  email = excluded.email,
                  prefix = excluded.prefix,
                  display_name = excluded.display_name,
                  account_id = excluded.account_id,
                  status = excluded.status,
                  openclaw_status = excluded.openclaw_status,
                  install_command = excluded.install_command,
                  auth_url = excluded.auth_url,
                  comm_level = excluded.comm_level,
                  ext_receive_type = excluded.ext_receive_type,
                  ext_send_type = excluded.ext_send_type,
                  updated_at = CURRENT_TIMESTAMP
                ON CONFLICT(email) DO UPDATE SET
                  id = excluded.id,
                  connection_id = excluded.connection_id,
                  provider_mailbox_id = excluded.provider_mailbox_id,
                  prefix = excluded.prefix,
                  display_name = excluded.display_name,
                  account_id = excluded.account_id,
                  status = excluded.status,
                  openclaw_status = excluded.openclaw_status,
                  install_command = excluded.install_command,
                  auth_url = excluded.auth_url,
                  comm_level = excluded.comm_level,
                  ext_receive_type = excluded.ext_receive_type,
                  ext_send_type = excluded.ext_send_type,
                  updated_at = CURRENT_TIMESTAMP
            """), payload)
        return self.get_mailbox(local_id) or {}

    def list_mailboxes(self, connection_id: str | None = None, include_deleted: bool = False) -> list[dict[str, Any]]:
        """列出本地邮箱，可按连接过滤。"""

        where: list[str] = []
        params: dict[str, Any] = {}
        if not include_deleted:
            where.append("status != 'deleted'")
        if connection_id:
            where.append("connection_id = :connection_id")
            params["connection_id"] = connection_id
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(f"""
                SELECT * FROM mailboxes
                {where_sql}
                ORDER BY created_at DESC, email ASC
            """), params).mappings().all()]

    def list_active_mailboxes(self, connection_id: str | None = None) -> list[dict[str, Any]]:
        """列出 active 状态邮箱，用于远端收件箱同步。"""

        params: dict[str, Any] = {}
        where = "WHERE status = 'active'"
        if connection_id:
            where += " AND connection_id = :connection_id"
            params["connection_id"] = connection_id
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(f"""
                SELECT * FROM mailboxes
                {where}
                ORDER BY email ASC
            """), params).mappings().all()]

    def get_mailbox(self, mailbox_id: str) -> dict[str, Any] | None:
        """按本地邮箱 ID 读取邮箱。"""

        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM mailboxes WHERE id = :id"), {"id": mailbox_id}).mappings().first()
        return dict(row) if row else None

    def get_mailbox_by_email(self, email: str, connection_id: str | None = None) -> dict[str, Any] | None:
        """按邮箱地址读取未删除邮箱。"""

        params: dict[str, Any] = {"email": email.strip().lower()}
        where = "email = :email AND status != 'deleted'"
        if connection_id:
            where += " AND connection_id = :connection_id"
            params["connection_id"] = connection_id
        with self.engine.connect() as connection:
            row = connection.execute(text(f"SELECT * FROM mailboxes WHERE {where}"), params).mappings().first()
        return dict(row) if row else None

    def mark_mailbox_deleted(self, mailbox_id: str) -> bool:
        """将本地邮箱标记为 deleted。"""

        with self.engine.begin() as connection:
            result = connection.execute(text("""
                UPDATE mailboxes
                SET status = 'deleted', updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {"id": mailbox_id})
        return result.rowcount > 0

    def update_mailbox_comm_settings(self, mailbox_id: str, input_value: dict[str, Any]) -> dict[str, Any] | None:
        """更新本地邮箱通信设置缓存。"""

        with self.engine.begin() as connection:
            connection.execute(text("""
                UPDATE mailboxes
                SET comm_level = :comm_level,
                    ext_receive_type = :ext_receive_type,
                    ext_send_type = :ext_send_type,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {
                "id": mailbox_id,
                "comm_level": input_value["comm_level"],
                "ext_receive_type": input_value.get("ext_receive_type"),
                "ext_send_type": input_value.get("ext_send_type"),
            })
        return self.get_mailbox(mailbox_id)

    def save_mail(self, input_value: dict[str, Any]) -> dict[str, Any]:
        """保存或更新本地邮件及其附件。"""

        connection_id = input_value.get("connection_id") or LEGACY_CONNECTION_ID
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO mails
                  (
                    connection_id, provider_mail_id, mailbox_email, source, address, subject, text,
                    html, raw_json, header_raw, has_attachments, received_at
                  )
                VALUES
                  (
                    :connection_id, :provider_mail_id, :mailbox_email, :source, :address, :subject, :text,
                    :html, :raw_json, :header_raw, :has_attachments, :received_at
                  )
                ON CONFLICT(mailbox_email, provider_mail_id) DO UPDATE SET
                  connection_id = excluded.connection_id,
                  source = excluded.source,
                  address = excluded.address,
                  subject = excluded.subject,
                  text = excluded.text,
                  html = excluded.html,
                  raw_json = excluded.raw_json,
                  header_raw = excluded.header_raw,
                  has_attachments = excluded.has_attachments,
                  received_at = excluded.received_at
            """), {
                "connection_id": connection_id,
                "provider_mail_id": input_value["provider_mail_id"],
                "mailbox_email": input_value["mailbox_email"],
                "source": input_value.get("source"),
                "address": input_value.get("address"),
                "subject": input_value.get("subject"),
                "text": input_value.get("text"),
                "html": input_value.get("html"),
                "raw_json": input_value["raw_json"],
                "header_raw": input_value.get("header_raw"),
                "has_attachments": 1 if input_value.get("has_attachments") else 0,
                "received_at": input_value.get("received_at"),
            })
            row = connection.execute(text("""
                SELECT * FROM mails
                WHERE connection_id = :connection_id
                  AND mailbox_email = :mailbox_email
                  AND provider_mail_id = :provider_mail_id
            """), {
                "connection_id": connection_id,
                "mailbox_email": input_value["mailbox_email"],
                "provider_mail_id": input_value["provider_mail_id"],
            }).mappings().first()
            mail = dict(row) if row else {}
            connection.execute(text("DELETE FROM attachments WHERE mail_id = :mail_id"), {"mail_id": mail["id"]})
            for attachment in input_value.get("attachments") or []:
                connection.execute(text("""
                    INSERT INTO attachments (mail_id, provider_part_id, filename, content_type, size)
                    VALUES (:mail_id, :provider_part_id, :filename, :content_type, :size)
                """), {
                    "mail_id": mail["id"],
                    "provider_part_id": attachment["provider_part_id"],
                    "filename": attachment.get("filename"),
                    "content_type": attachment.get("content_type"),
                    "size": attachment.get("size"),
                })
        return self.get_mail(mail["id"]) or {}

    def list_mails(
        self,
        connection_id: str | None = None,
        mailbox_email: str | None = None,
        limit: int = 50,
        offset: int = 0,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        """分页查询本地邮件列表。"""

        where: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if connection_id:
            where.append("connection_id = :connection_id")
            params["connection_id"] = connection_id
        if mailbox_email:
            where.append("mailbox_email = :mailbox_email")
            params["mailbox_email"] = mailbox_email
        if keyword:
            where.append("""(
                mailbox_email LIKE :keyword
                OR source LIKE :keyword
                OR address LIKE :keyword
                OR subject LIKE :keyword
                OR text LIKE :keyword
            )""")
            params["keyword"] = f"%{keyword}%"
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self.engine.connect() as connection:
            items = [dict(row) for row in connection.execute(text(f"""
                SELECT * FROM mails
                {where_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT :limit OFFSET :offset
            """), params).mappings().all()]
            count = connection.execute(text(f"SELECT COUNT(*) AS count FROM mails {where_sql}"), {
                key: value for key, value in params.items() if key not in {"limit", "offset"}
            }).mappings().first()
        total = count["count"] if count else 0
        return {"items": items, "count": total, "total": total, "limit": limit, "offset": offset}

    def list_mail_provider_ids(self, mailbox_email: str, connection_id: str | None = None) -> list[str]:
        """列出指定邮箱已缓存的远端邮件 ID。"""

        params: dict[str, Any] = {"mailbox_email": mailbox_email}
        where = "mailbox_email = :mailbox_email"
        if connection_id:
            where += " AND connection_id = :connection_id"
            params["connection_id"] = connection_id
        with self.engine.connect() as connection:
            rows = connection.execute(text(f"""
                SELECT provider_mail_id FROM mails
                WHERE {where}
            """), params).mappings().all()
        return [str(row["provider_mail_id"]) for row in rows]

    def get_mail_by_provider_id(
        self,
        mailbox_email: str,
        provider_mail_id: str,
        connection_id: str | None = None,
    ) -> dict[str, Any] | None:
        """按邮箱和远端邮件 ID 读取本地邮件。"""

        params: dict[str, Any] = {
            "mailbox_email": mailbox_email,
            "provider_mail_id": provider_mail_id,
        }
        where = "mailbox_email = :mailbox_email AND provider_mail_id = :provider_mail_id"
        if connection_id:
            where += " AND connection_id = :connection_id"
            params["connection_id"] = connection_id
        with self.engine.connect() as connection:
            row = connection.execute(text(f"SELECT * FROM mails WHERE {where}"), params).mappings().first()
        return dict(row) if row else None

    def delete_mails_by_provider_ids(
        self,
        mailbox_email: str,
        provider_mail_ids: list[str],
        connection_id: str | None = None,
    ) -> int:
        """按远端邮件 ID 批量删除本地邮件缓存。"""

        if not provider_mail_ids:
            return 0
        deleted = 0
        with self.engine.begin() as connection:
            for provider_mail_id in provider_mail_ids:
                params: dict[str, Any] = {
                    "mailbox_email": mailbox_email,
                    "provider_mail_id": provider_mail_id,
                }
                where = "mailbox_email = :mailbox_email AND provider_mail_id = :provider_mail_id"
                if connection_id:
                    where += " AND connection_id = :connection_id"
                    params["connection_id"] = connection_id
                result = connection.execute(text(f"DELETE FROM mails WHERE {where}"), params)
                deleted += result.rowcount
        return deleted

    def list_mails_for_deletion(self, connection_id: str | None = None, mailbox_email: str | None = None) -> list[dict[str, Any]]:
        """列出准备执行远端删除的本地邮件。"""

        where: list[str] = []
        params: dict[str, Any] = {}
        if connection_id:
            where.append("connection_id = :connection_id")
            params["connection_id"] = connection_id
        if mailbox_email:
            where.append("mailbox_email = :mailbox_email")
            params["mailbox_email"] = mailbox_email
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(f"""
                SELECT * FROM mails
                {where_sql}
                ORDER BY created_at DESC, id DESC
            """), params).mappings().all()]

    def get_mail(self, mail_id: int) -> dict[str, Any] | None:
        """按本地邮件 ID 读取邮件。"""

        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM mails WHERE id = :id"), {"id": mail_id}).mappings().first()
        return dict(row) if row else None

    def mark_mail_read(self, mail_id: int) -> dict[str, Any] | None:
        """将本地邮件标记为已读并返回最新记录。"""

        with self.engine.begin() as connection:
            connection.execute(text("""
                UPDATE mails
                SET read_at = COALESCE(read_at, CURRENT_TIMESTAMP)
                WHERE id = :id
            """), {"id": mail_id})
        return self.get_mail(mail_id)

    def mark_mails_read(self, connection_id: str | None = None, mailbox_email: str | None = None) -> int:
        """批量将匹配范围内的未读邮件标记为已读。"""

        where = ["read_at IS NULL"]
        params: dict[str, Any] = {}
        if connection_id:
            where.append("connection_id = :connection_id")
            params["connection_id"] = connection_id
        if mailbox_email:
            where.append("mailbox_email = :mailbox_email")
            params["mailbox_email"] = mailbox_email
        with self.engine.begin() as connection:
            result = connection.execute(text(f"""
                UPDATE mails
                SET read_at = CURRENT_TIMESTAMP
                WHERE {" AND ".join(where)}
            """), params)
        return result.rowcount if result.rowcount >= 0 else 0

    def list_attachments(self, mail_id: int) -> list[dict[str, Any]]:
        """列出本地邮件附件元数据。"""

        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(text("""
                SELECT * FROM attachments
                WHERE mail_id = :mail_id
                ORDER BY id ASC
            """), {"mail_id": mail_id}).mappings().all()]

    def delete_mail(self, mail_id: int) -> bool:
        """删除本地邮件缓存。"""

        with self.engine.begin() as connection:
            result = connection.execute(text("DELETE FROM mails WHERE id = :id"), {"id": mail_id})
        return result.rowcount > 0

    def delete_mails_locally(self, connection_id: str | None = None, mailbox_email: str | None = None) -> dict[str, Any]:
        """仅删除本地邮件缓存，不调用远端。"""

        mails = self.list_mails_for_deletion(connection_id=connection_id, mailbox_email=mailbox_email)
        deleted = 0
        for mail in mails:
            if self.delete_mail(mail["id"]):
                deleted += 1
        return {"success": True, "deleted": deleted, "failed": 0, "errors": []}

    def delete_connection_cache(self, connection_id: str) -> dict[str, int]:
        """删除指定连接关联的本地邮箱、邮件和附件缓存。"""

        mails = self.list_mails_for_deletion(connection_id=connection_id)
        mailbox_count = len(self.list_mailboxes(connection_id=connection_id, include_deleted=True))
        with self.engine.begin() as connection:
            for mail in mails:
                connection.execute(text("DELETE FROM attachments WHERE mail_id = :mail_id"), {"mail_id": mail["id"]})
            mail_result = connection.execute(
                text("DELETE FROM mails WHERE connection_id = :connection_id"),
                {"connection_id": connection_id},
            )
            mailbox_result = connection.execute(
                text("DELETE FROM mailboxes WHERE connection_id = :connection_id"),
                {"connection_id": connection_id},
            )
        return {
            "mailboxes": mailbox_result.rowcount if mailbox_result.rowcount >= 0 else mailbox_count,
            "mails": mail_result.rowcount if mail_result.rowcount >= 0 else len(mails),
        }


def parse_mail_raw_json(mail: dict[str, Any]) -> Any:
    """解析本地邮件保存的原始 JSON。"""

    try:
        return json.loads(mail["raw_json"])
    except (TypeError, ValueError):
        return None


mail_repository = MailRepository()
