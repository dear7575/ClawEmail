import Database from "better-sqlite3";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { config } from "./config";

mkdirSync(dirname(config.DATABASE_PATH), { recursive: true });

export const db = new Database(config.DATABASE_PATH);
db.pragma("journal_mode = WAL");
db.pragma("foreign_keys = ON");

db.exec(`
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
);

CREATE INDEX IF NOT EXISTS idx_connections_status ON connections(status);
CREATE INDEX IF NOT EXISTS idx_connections_user_email ON connections(user_email);

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
);

CREATE INDEX IF NOT EXISTS idx_mailboxes_email ON mailboxes(email);
CREATE INDEX IF NOT EXISTS idx_mailboxes_status ON mailboxes(status);

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
);

CREATE INDEX IF NOT EXISTS idx_mails_mailbox_email ON mails(mailbox_email);
CREATE INDEX IF NOT EXISTS idx_mails_created_at ON mails(created_at);

CREATE TABLE IF NOT EXISTS attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mail_id INTEGER NOT NULL,
  provider_part_id TEXT NOT NULL,
  filename TEXT,
  content_type TEXT,
  size INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(mail_id) REFERENCES mails(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_attachments_mail_id ON attachments(mail_id);

CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS duck_accounts (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  token TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  last_error TEXT,
  last_used_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_duck_accounts_status ON duck_accounts(status);

CREATE TABLE IF NOT EXISTS duck_addresses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL,
  address TEXT NOT NULL UNIQUE,
  local_part TEXT NOT NULL,
  forwarding_mailbox_email TEXT,
  note TEXT,
  openai_password TEXT,
  openai_auth_json TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  raw_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(account_id) REFERENCES duck_accounts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_duck_addresses_account_id ON duck_addresses(account_id);
CREATE INDEX IF NOT EXISTS idx_duck_addresses_status ON duck_addresses(status);
`);

function ensureColumn(table: string, column: string, definition: string): void {
  const rows = db.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>;
  if (rows.some((row) => row.name === column)) return;
  db.prepare(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition}`).run();
}

function ensureMailReadColumn(): void {
  const rows = db.prepare("PRAGMA table_info(mails)").all() as Array<{ name: string }>;
  const exists = rows.some((row) => row.name === "read_at");
  ensureColumn("mails", "read_at", "TEXT");
  if (!exists) {
    db.prepare("UPDATE mails SET read_at = CURRENT_TIMESTAMP WHERE read_at IS NULL").run();
  }
}

ensureColumn("mailboxes", "comm_level", "INTEGER");
ensureColumn("mailboxes", "ext_receive_type", "INTEGER");
ensureColumn("mailboxes", "ext_send_type", "INTEGER");
ensureColumn("mailboxes", "connection_id", "TEXT");
ensureColumn("mailboxes", "provider_mailbox_id", "TEXT");
ensureColumn("mails", "connection_id", "TEXT");
ensureColumn("duck_addresses", "openai_password", "TEXT");
ensureColumn("duck_addresses", "openai_auth_json", "TEXT");
ensureMailReadColumn();

db.exec(`
CREATE INDEX IF NOT EXISTS idx_mailboxes_connection_id ON mailboxes(connection_id);
CREATE INDEX IF NOT EXISTS idx_mailboxes_connection_email ON mailboxes(connection_id, email);
CREATE INDEX IF NOT EXISTS idx_mails_connection_id ON mails(connection_id);
CREATE INDEX IF NOT EXISTS idx_mails_connection_mailbox ON mails(connection_id, mailbox_email);
`);

export const LEGACY_CONNECTION_ID = "legacy";

export type ConnectionRow = {
  id: string;
  label: string | null;
  user_email: string | null;
  workspace_id: string | null;
  workspace_name: string | null;
  parent_mailbox_id: string | null;
  root_prefix: string | null;
  domain: string;
  api_key: string | null;
  dashboard_cookie: string | null;
  status: string;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
};

export type MailboxRow = {
  id: string;
  connection_id: string | null;
  provider_mailbox_id: string | null;
  email: string;
  prefix: string;
  display_name: string | null;
  account_id: string | null;
  status: string;
  openclaw_status: string | null;
  install_command: string | null;
  auth_url: string | null;
  comm_level: number | null;
  ext_receive_type: number | null;
  ext_send_type: number | null;
  created_at: string;
  updated_at: string;
};

export type MailRow = {
  id: number;
  connection_id: string | null;
  provider_mail_id: string;
  mailbox_email: string;
  source: string | null;
  address: string | null;
  subject: string | null;
  text: string | null;
  html: string | null;
  raw_json: string;
  header_raw: string | null;
  has_attachments: number;
  read_at: string | null;
  received_at: string | null;
  created_at: string;
};

export type AttachmentRow = {
  id: number;
  mail_id: number;
  provider_part_id: string;
  filename: string | null;
  content_type: string | null;
  size: number | null;
  created_at: string;
};

export type DuckAccountRow = {
  id: string;
  label: string;
  token: string;
  status: string;
  last_error: string | null;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
};

export type DuckAddressRow = {
  id: number;
  account_id: string;
  address: string;
  local_part: string;
  forwarding_mailbox_email: string | null;
  note: string | null;
  openai_password: string | null;
  openai_auth_json: string | null;
  status: string;
  raw_json: string;
  created_at: string;
  updated_at: string;
};

export type DuckAddressPublic = Omit<DuckAddressRow, "openai_password" | "openai_auth_json"> & {
  has_openai_password: boolean;
  has_openai_auth_json: boolean;
};

export type DuckAccountPublic = Omit<DuckAccountRow, "token"> & {
  token_prefix: string | null;
  token_suffix: string | null;
};

export function upsertConnection(input: {
  id: string;
  label?: string | null;
  userEmail?: string | null;
  workspaceId?: string | null;
  workspaceName?: string | null;
  parentMailboxId?: string | null;
  rootPrefix?: string | null;
  domain?: string | null;
  apiKey?: string | null;
  dashboardCookie?: string | null;
  status?: string | null;
  lastSyncedAt?: string | null;
}): ConnectionRow {
  db.prepare(`
    INSERT INTO connections
      (
        id, label, user_email, workspace_id, workspace_name, parent_mailbox_id,
        root_prefix, domain, api_key, dashboard_cookie, status, last_synced_at
      )
    VALUES
      (
        @id, @label, @userEmail, @workspaceId, @workspaceName, @parentMailboxId,
        @rootPrefix, @domain, @apiKey, @dashboardCookie, @status, @lastSyncedAt
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
  `).run({
    id: input.id,
    label: input.label ?? input.userEmail ?? input.workspaceName ?? input.id,
    userEmail: input.userEmail ?? null,
    workspaceId: input.workspaceId ?? null,
    workspaceName: input.workspaceName ?? null,
    parentMailboxId: input.parentMailboxId ?? null,
    rootPrefix: input.rootPrefix ?? null,
    domain: input.domain ?? config.CLAW_DOMAIN,
    apiKey: input.apiKey ?? null,
    dashboardCookie: input.dashboardCookie ?? null,
    status: input.status ?? "active",
    lastSyncedAt: input.lastSyncedAt ?? null
  });
  return getConnectionById(input.id)!;
}

export function listConnections(includeDisconnected = false): ConnectionRow[] {
  const sql = includeDisconnected
    ? "SELECT * FROM connections ORDER BY created_at ASC"
    : "SELECT * FROM connections WHERE status != 'disconnected' ORDER BY created_at ASC";
  return db.prepare(sql).all() as ConnectionRow[];
}

export function getConnectionById(id: string): ConnectionRow | undefined {
  return db.prepare("SELECT * FROM connections WHERE id = ?").get(id) as ConnectionRow | undefined;
}

export function getDefaultConnection(): ConnectionRow | undefined {
  return db.prepare(`
    SELECT * FROM connections
    WHERE status != 'disconnected'
    ORDER BY created_at ASC
    LIMIT 1
  `).get() as ConnectionRow | undefined;
}

export function markConnectionDisconnected(id: string): void {
  db.prepare(`
    UPDATE connections
    SET status = 'disconnected', updated_at = CURRENT_TIMESTAMP
    WHERE id = ?
  `).run(id);
}

export function upsertMailbox(input: {
  id: string;
  connectionId?: string | null;
  providerMailboxId?: string | null;
  email: string;
  prefix: string;
  displayName?: string | null;
  accountId?: string | null;
  status?: string | null;
  openclawStatus?: string | null;
  installCommand?: string | null;
  authUrl?: string | null;
  commLevel?: number | null;
  extReceiveType?: number | null;
  extSendType?: number | null;
}): MailboxRow {
  const connectionId = input.connectionId ?? LEGACY_CONNECTION_ID;
  const providerMailboxId = input.providerMailboxId ?? input.id;
  const localId = input.connectionId ? `${connectionId}:${providerMailboxId}` : input.id;
  db.prepare(`
    INSERT INTO mailboxes
      (
        id, connection_id, provider_mailbox_id, email, prefix, display_name, account_id, status, openclaw_status,
        install_command, auth_url, comm_level, ext_receive_type, ext_send_type
      )
    VALUES
      (
        @id, @connectionId, @providerMailboxId, @email, @prefix, @displayName, @accountId, @status, @openclawStatus,
        @installCommand, @authUrl, @commLevel, @extReceiveType, @extSendType
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
  `).run({
    ...input,
    id: localId,
    connectionId,
    providerMailboxId,
    status: input.status ?? "active",
    displayName: input.displayName ?? null,
    accountId: input.accountId ?? null,
    openclawStatus: input.openclawStatus ?? null,
    installCommand: input.installCommand ?? null,
    authUrl: input.authUrl ?? null,
    commLevel: input.commLevel ?? null,
    extReceiveType: input.extReceiveType ?? null,
    extSendType: input.extSendType ?? null
  });
  return getMailboxById(localId)!;
}

export function listMailboxes(input: {
  connectionId?: string;
  includeDeleted?: boolean;
} = {}): MailboxRow[] {
  const where: string[] = [];
  const params: unknown[] = [];
  if (!input.includeDeleted) {
    where.push("status != 'deleted'");
  }
  if (input.connectionId) {
    where.push("connection_id = ?");
    params.push(input.connectionId);
  }
  const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";
  return db.prepare(`
    SELECT * FROM mailboxes ${whereSql}
    ORDER BY created_at DESC, email ASC
  `).all(...params) as MailboxRow[];
}

export function listActiveMailboxes(connectionId?: string): MailboxRow[] {
  if (connectionId) {
    return db.prepare(`
      SELECT * FROM mailboxes
      WHERE status = 'active' AND connection_id = ?
      ORDER BY email ASC
    `).all(connectionId) as MailboxRow[];
  }
  return db.prepare("SELECT * FROM mailboxes WHERE status = 'active' ORDER BY email ASC").all() as MailboxRow[];
}

export function getMailboxById(id: string): MailboxRow | undefined {
  return db.prepare("SELECT * FROM mailboxes WHERE id = ?").get(id) as MailboxRow | undefined;
}

export function getMailboxByEmail(email: string, connectionId?: string): MailboxRow | undefined {
  if (connectionId) {
    return db.prepare(`
      SELECT * FROM mailboxes
      WHERE email = ? AND connection_id = ? AND status != 'deleted'
    `).get(email, connectionId) as MailboxRow | undefined;
  }
  return db.prepare("SELECT * FROM mailboxes WHERE email = ? AND status != 'deleted'").get(email) as MailboxRow | undefined;
}

export function markMailboxDeleted(id: string): void {
  db.prepare("UPDATE mailboxes SET status = 'deleted', updated_at = CURRENT_TIMESTAMP WHERE id = ?").run(id);
}

export function updateMailboxCommSettings(id: string, input: {
  commLevel: number;
  extReceiveType?: number | null;
  extSendType?: number | null;
}): MailboxRow | undefined {
  db.prepare(`
    UPDATE mailboxes
    SET
      comm_level = @commLevel,
      ext_receive_type = @extReceiveType,
      ext_send_type = @extSendType,
      updated_at = CURRENT_TIMESTAMP
    WHERE id = @id
  `).run({
    id,
    commLevel: input.commLevel,
    extReceiveType: input.extReceiveType ?? null,
    extSendType: input.extSendType ?? null
  });
  return getMailboxById(id);
}

export function markMailboxesMissingDeleted(remoteEmails: string[], connectionId?: string): MailboxRow[] {
  const remoteEmailSet = new Set(remoteEmails.map((email) => email.trim().toLowerCase()));
  const missing = listActiveMailboxes(connectionId).filter((mailbox) => !remoteEmailSet.has(mailbox.email.toLowerCase()));
  const transaction = db.transaction(() => {
    const statement = db.prepare("UPDATE mailboxes SET status = 'deleted', updated_at = CURRENT_TIMESTAMP WHERE id = ?");
    for (const mailbox of missing) {
      statement.run(mailbox.id);
    }
  });
  transaction();
  return missing;
}

export function saveMail(input: {
  connectionId?: string | null;
  providerMailId: string;
  mailboxEmail: string;
  source?: string | null;
  address?: string | null;
  subject?: string | null;
  text?: string | null;
  html?: string | null;
  rawJson: string;
  headerRaw?: string | null;
  hasAttachments?: boolean;
  receivedAt?: string | null;
  attachments?: Array<{
    providerPartId: string;
    filename?: string | null;
    contentType?: string | null;
    size?: number | null;
  }>;
}): MailRow {
  const connectionId = input.connectionId ?? LEGACY_CONNECTION_ID;
  const transaction = db.transaction(() => {
    db.prepare(`
      INSERT INTO mails
        (connection_id, provider_mail_id, mailbox_email, source, address, subject, text, html, raw_json, header_raw, has_attachments, received_at)
      VALUES
        (@connectionId, @providerMailId, @mailboxEmail, @source, @address, @subject, @text, @html, @rawJson, @headerRaw, @hasAttachments, @receivedAt)
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
    `).run({
      ...input,
      connectionId,
      source: input.source ?? null,
      address: input.address ?? null,
      subject: input.subject ?? null,
      text: input.text ?? null,
      html: input.html ?? null,
      headerRaw: input.headerRaw ?? null,
      hasAttachments: input.hasAttachments ? 1 : 0,
      receivedAt: input.receivedAt ?? null
    });

    const row = db.prepare(`
      SELECT * FROM mails WHERE connection_id = ? AND mailbox_email = ? AND provider_mail_id = ?
    `).get(connectionId, input.mailboxEmail, input.providerMailId) as MailRow;

    db.prepare("DELETE FROM attachments WHERE mail_id = ?").run(row.id);
    const insertAttachment = db.prepare(`
      INSERT INTO attachments (mail_id, provider_part_id, filename, content_type, size)
      VALUES (?, ?, ?, ?, ?)
    `);
    for (const attachment of input.attachments ?? []) {
      insertAttachment.run(
        row.id,
        attachment.providerPartId,
        attachment.filename ?? null,
        attachment.contentType ?? null,
        attachment.size ?? null
      );
    }

    return row;
  });

  return transaction();
}

export function listMails(input: {
  connectionId?: string;
  mailboxEmail?: string;
  limit: number;
  offset: number;
}): { items: MailRow[]; count: number } {
  const where: string[] = [];
  const params: unknown[] = [];
  if (input.connectionId) {
    where.push("connection_id = ?");
    params.push(input.connectionId);
  }
  if (input.mailboxEmail) {
    where.push("mailbox_email = ?");
    params.push(input.mailboxEmail);
  }
  const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";
  const items = db.prepare(`
    SELECT * FROM mails ${whereSql}
    ORDER BY created_at DESC, id DESC
    LIMIT ? OFFSET ?
  `).all(...params, input.limit, input.offset) as MailRow[];
  const count = db.prepare(`SELECT COUNT(*) AS count FROM mails ${whereSql}`).get(...params) as { count: number };
  return { items, count: count.count };
}

export function listMailsForDeletion(input: {
  connectionId?: string;
  mailboxEmail?: string;
}): MailRow[] {
  const where: string[] = [];
  const params: unknown[] = [];
  if (input.connectionId) {
    where.push("connection_id = ?");
    params.push(input.connectionId);
  }
  if (input.mailboxEmail) {
    where.push("mailbox_email = ?");
    params.push(input.mailboxEmail);
  }
  const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";
  return db.prepare(`
    SELECT * FROM mails ${whereSql}
    ORDER BY created_at DESC, id DESC
  `).all(...params) as MailRow[];
}

export function listMailProviderIds(mailboxEmail: string, connectionId?: string): string[] {
  const rows = connectionId
    ? db.prepare("SELECT provider_mail_id FROM mails WHERE connection_id = ? AND mailbox_email = ?")
      .all(connectionId, mailboxEmail) as Array<{ provider_mail_id: string }>
    : db.prepare("SELECT provider_mail_id FROM mails WHERE mailbox_email = ?")
      .all(mailboxEmail) as Array<{ provider_mail_id: string }>;
  return rows.map((row) => row.provider_mail_id);
}

export function getMailById(id: number): MailRow | undefined {
  return db.prepare("SELECT * FROM mails WHERE id = ?").get(id) as MailRow | undefined;
}

export function markMailRead(id: number): MailRow | undefined {
  db.prepare("UPDATE mails SET read_at = COALESCE(read_at, CURRENT_TIMESTAMP) WHERE id = ?").run(id);
  return getMailById(id);
}

export function getMailByProviderId(mailboxEmail: string, providerMailId: string, connectionId?: string): MailRow | undefined {
  if (connectionId) {
    return db.prepare("SELECT * FROM mails WHERE connection_id = ? AND mailbox_email = ? AND provider_mail_id = ?")
      .get(connectionId, mailboxEmail, providerMailId) as MailRow | undefined;
  }
  return db.prepare("SELECT * FROM mails WHERE mailbox_email = ? AND provider_mail_id = ?")
    .get(mailboxEmail, providerMailId) as MailRow | undefined;
}

export function deleteMailById(id: number): boolean {
  const result = db.prepare("DELETE FROM mails WHERE id = ?").run(id);
  return result.changes > 0;
}

export function deleteMailsByProviderIds(mailboxEmail: string, providerMailIds: string[], connectionId?: string): number {
  if (providerMailIds.length === 0) return 0;
  const transaction = db.transaction(() => {
    const statement = connectionId
      ? db.prepare("DELETE FROM mails WHERE connection_id = ? AND mailbox_email = ? AND provider_mail_id = ?")
      : db.prepare("DELETE FROM mails WHERE mailbox_email = ? AND provider_mail_id = ?");
    let count = 0;
    for (const providerMailId of providerMailIds) {
      count += connectionId
        ? statement.run(connectionId, mailboxEmail, providerMailId).changes
        : statement.run(mailboxEmail, providerMailId).changes;
    }
    return count;
  });
  return transaction();
}

export function listAttachments(mailId: number): AttachmentRow[] {
  return db.prepare("SELECT * FROM attachments WHERE mail_id = ? ORDER BY id ASC").all(mailId) as AttachmentRow[];
}

export function getSetting(key: string): string | undefined {
  const row = db.prepare("SELECT value FROM app_settings WHERE key = ?").get(key) as { value: string } | undefined;
  return row?.value;
}

export function setSetting(key: string, value: string): void {
  db.prepare(`
    INSERT INTO app_settings (key, value)
    VALUES (?, ?)
    ON CONFLICT(key) DO UPDATE SET
      value = excluded.value,
      updated_at = CURRENT_TIMESTAMP
  `).run(key, value);
}

export function deleteSettings(keys: string[]): void {
  const transaction = db.transaction(() => {
    const statement = db.prepare("DELETE FROM app_settings WHERE key = ?");
    for (const key of keys) {
      statement.run(key);
    }
  });
  transaction();
}

function maskDuckToken(row: DuckAccountRow): DuckAccountPublic {
  const token = row.token.trim();
  return {
    id: row.id,
    label: row.label,
    status: row.status,
    last_error: row.last_error,
    last_used_at: row.last_used_at,
    created_at: row.created_at,
    updated_at: row.updated_at,
    token_prefix: token ? token.slice(0, 8) : null,
    token_suffix: token ? token.slice(-4) : null
  };
}

export function listDuckAccounts(): DuckAccountPublic[] {
  return (db.prepare("SELECT * FROM duck_accounts WHERE status != 'disabled' ORDER BY created_at DESC").all() as DuckAccountRow[])
    .map(maskDuckToken);
}

export function getDuckAccountById(id: string): DuckAccountRow | undefined {
  return db.prepare("SELECT * FROM duck_accounts WHERE id = ?").get(id) as DuckAccountRow | undefined;
}

export function createDuckAccount(input: {
  id: string;
  label: string;
  token: string;
}): DuckAccountPublic {
  db.prepare(`
    INSERT INTO duck_accounts (id, label, token, status)
    VALUES (@id, @label, @token, 'active')
  `).run({
    id: input.id,
    label: input.label,
    token: input.token
  });
  return maskDuckToken(getDuckAccountById(input.id)!);
}

export function updateDuckAccountToken(id: string, token: string): DuckAccountPublic | undefined {
  db.prepare(`
    UPDATE duck_accounts
    SET token = ?, status = 'active', last_error = NULL, updated_at = CURRENT_TIMESTAMP
    WHERE id = ?
  `).run(token, id);
  const row = getDuckAccountById(id);
  return row ? maskDuckToken(row) : undefined;
}

export function deleteDuckAccount(id: string): boolean {
  const result = db.prepare("DELETE FROM duck_accounts WHERE id = ?").run(id);
  return result.changes > 0;
}

export function markDuckAccountUsed(id: string): void {
  db.prepare(`
    UPDATE duck_accounts
    SET last_error = NULL, last_used_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
    WHERE id = ?
  `).run(id);
}

export function markDuckAccountError(id: string, error: string): void {
  db.prepare(`
    UPDATE duck_accounts
    SET last_error = ?, updated_at = CURRENT_TIMESTAMP
    WHERE id = ?
  `).run(error, id);
}

export function listDuckAddresses(input: {
  accountId?: string;
} = {}): DuckAddressRow[] {
  const where: string[] = [];
  const params: unknown[] = [];
  if (input.accountId) {
    where.push("account_id = ?");
    params.push(input.accountId);
  }
  const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";
  return db.prepare(`
    SELECT * FROM duck_addresses ${whereSql}
    ORDER BY created_at DESC, id DESC
  `).all(...params) as DuckAddressRow[];
}

export function toPublicDuckAddress(row: DuckAddressRow): DuckAddressPublic {
  const {
    openai_password: openAiPassword,
    openai_auth_json: openAiAuthJson,
    ...rest
  } = row;
  return {
    ...rest,
    has_openai_password: Boolean(openAiPassword),
    has_openai_auth_json: Boolean(openAiAuthJson)
  };
}

export function getDuckAddressById(id: number): DuckAddressRow | undefined {
  return db.prepare("SELECT * FROM duck_addresses WHERE id = ?").get(id) as DuckAddressRow | undefined;
}

export function getDuckAddressByAddress(address: string): DuckAddressRow | undefined {
  return db.prepare("SELECT * FROM duck_addresses WHERE address = ?").get(address.trim().toLowerCase()) as DuckAddressRow | undefined;
}

export function setDuckAddressOpenAiPassword(id: number, password: string | null): DuckAddressRow | undefined {
  db.prepare(`
    UPDATE duck_addresses
    SET openai_password = ?, updated_at = CURRENT_TIMESTAMP
    WHERE id = ?
  `).run(password, id);
  return getDuckAddressById(id);
}

export function setDuckAddressOpenAiAuthJson(id: number, authJson: string | null): DuckAddressRow | undefined {
  db.prepare(`
    UPDATE duck_addresses
    SET openai_auth_json = ?, updated_at = CURRENT_TIMESTAMP
    WHERE id = ?
  `).run(authJson, id);
  return getDuckAddressById(id);
}

export function updateDuckAddressOpenAiCredentials(
  id: number,
  input: {
    password?: string | null;
    authJson?: string | null;
  }
): DuckAddressRow | undefined {
  const existing = getDuckAddressById(id);
  if (!existing) return undefined;
  db.prepare(`
    UPDATE duck_addresses
    SET openai_password = @password,
        openai_auth_json = @authJson,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = @id
  `).run({
    id,
    password: input.password === undefined ? existing.openai_password : input.password,
    authJson: input.authJson === undefined ? existing.openai_auth_json : input.authJson
  });
  return getDuckAddressById(id);
}

export function saveDuckAddress(input: {
  accountId: string;
  address: string;
  localPart: string;
  forwardingMailboxEmail?: string | null;
  note?: string | null;
  rawJson: string;
}): DuckAddressRow {
  db.prepare(`
    INSERT INTO duck_addresses
      (account_id, address, local_part, forwarding_mailbox_email, note, openai_password, openai_auth_json, status, raw_json)
    VALUES
      (@accountId, @address, @localPart, @forwardingMailboxEmail, @note, NULL, NULL, 'active', @rawJson)
    ON CONFLICT(address) DO UPDATE SET
      account_id = excluded.account_id,
      local_part = excluded.local_part,
      forwarding_mailbox_email = excluded.forwarding_mailbox_email,
      note = excluded.note,
      status = 'active',
      raw_json = excluded.raw_json,
      updated_at = CURRENT_TIMESTAMP
  `).run({
    accountId: input.accountId,
    address: input.address,
    localPart: input.localPart,
    forwardingMailboxEmail: input.forwardingMailboxEmail ?? null,
    note: input.note ?? null,
    rawJson: input.rawJson
  });
  return db.prepare("SELECT * FROM duck_addresses WHERE address = ?")
    .get(input.address) as DuckAddressRow;
}

export function updateDuckAddress(
  id: number,
  input: {
    forwardingMailboxEmail?: string | null;
    note?: string | null;
    status?: string | null;
  }
): DuckAddressRow | undefined {
  const existing = db.prepare("SELECT * FROM duck_addresses WHERE id = ?").get(id) as DuckAddressRow | undefined;
  if (!existing) return undefined;
  db.prepare(`
    UPDATE duck_addresses
    SET forwarding_mailbox_email = @forwardingMailboxEmail,
        note = @note,
        status = @status,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = @id
  `).run({
    id,
    forwardingMailboxEmail: input.forwardingMailboxEmail ?? existing.forwarding_mailbox_email,
    note: input.note ?? existing.note,
    status: input.status ?? existing.status
  });
  return db.prepare("SELECT * FROM duck_addresses WHERE id = ?").get(id) as DuckAddressRow | undefined;
}

export function deleteDuckAddress(id: number): boolean {
  const result = db.prepare("DELETE FROM duck_addresses WHERE id = ?").run(id);
  return result.changes > 0;
}

function backfillLegacyConnection(): void {
  const hasStoredAuth = Boolean(
    getSetting("claw.apiKey") ??
    getSetting("claw.dashboardCookie") ??
    config.CLAW_API_KEY ??
    config.CLAW_DASHBOARD_COOKIE
  );
  if (hasStoredAuth && !getConnectionById(LEGACY_CONNECTION_ID)) {
    upsertConnection({
      id: LEGACY_CONNECTION_ID,
      label: getSetting("claw.userEmail") ?? "默认连接",
      userEmail: getSetting("claw.userEmail") ?? null,
      workspaceId: getSetting("claw.workspaceId") ?? config.CLAW_WORKSPACE_ID ?? null,
      workspaceName: getSetting("claw.workspaceName") ?? null,
      parentMailboxId: getSetting("claw.parentMailboxId") ?? config.CLAW_PARENT_MAILBOX_ID ?? null,
      rootPrefix: getSetting("claw.rootPrefix") ?? config.CLAW_ROOT_PREFIX ?? null,
      domain: getSetting("claw.domain") ?? config.CLAW_DOMAIN,
      apiKey: getSetting("claw.apiKey") ?? config.CLAW_API_KEY ?? null,
      dashboardCookie: getSetting("claw.dashboardCookie") ?? config.CLAW_DASHBOARD_COOKIE ?? null,
      status: "active"
    });
  }
  db.prepare("UPDATE mailboxes SET connection_id = ? WHERE connection_id IS NULL").run(LEGACY_CONNECTION_ID);
  db.prepare("UPDATE mailboxes SET provider_mailbox_id = id WHERE provider_mailbox_id IS NULL").run();
  db.prepare("UPDATE mails SET connection_id = ? WHERE connection_id IS NULL").run(LEGACY_CONNECTION_ID);
}

backfillLegacyConnection();
