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

CREATE INDEX IF NOT EXISTS idx_mailboxes_connection_id ON mailboxes(connection_id);
CREATE INDEX IF NOT EXISTS idx_mailboxes_connection_email ON mailboxes(connection_id, email);
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

CREATE INDEX IF NOT EXISTS idx_mails_connection_id ON mails(connection_id);
CREATE INDEX IF NOT EXISTS idx_mails_connection_mailbox ON mails(connection_id, mailbox_email);
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
  status TEXT NOT NULL DEFAULT 'active',
  raw_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(account_id) REFERENCES duck_accounts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_duck_addresses_account_id ON duck_addresses(account_id);
CREATE INDEX IF NOT EXISTS idx_duck_addresses_status ON duck_addresses(status);
