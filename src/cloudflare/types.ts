export type D1Value = string | number | boolean | null | ArrayBuffer | Uint8Array;

export type D1Result<T = unknown> = {
  results?: T[];
  success: boolean;
  meta?: {
    changes?: number;
    duration?: number;
    last_row_id?: number;
    rows_read?: number;
    rows_written?: number;
  };
  error?: string;
};

export type D1PreparedStatement = {
  bind(...values: D1Value[]): D1PreparedStatement;
  first<T = unknown>(column?: string): Promise<T | null>;
  all<T = unknown>(): Promise<D1Result<T>>;
  run<T = unknown>(): Promise<D1Result<T>>;
};

export type D1Database = {
  prepare(query: string): D1PreparedStatement;
  batch<T = unknown>(statements: D1PreparedStatement[]): Promise<D1Result<T>[]>;
};

export type Fetcher = {
  fetch(request: Request): Promise<Response>;
};

export type Env = {
  DB: D1Database;
  ASSETS: Fetcher;
  ADMIN_PASSWORD?: string;
  CLAW_API_KEY?: string;
  CLAW_DASHBOARD_COOKIE?: string;
  CLAW_WORKSPACE_ID?: string;
  CLAW_PARENT_MAILBOX_ID?: string;
  CLAW_ROOT_PREFIX?: string;
  CLAW_DOMAIN?: string;
  SYSTEM_PROXY_URL?: string;
  SYSTEM_REQUEST_TIMEOUT_MS?: string;
  DUCK_PROXY_URL?: string;
  DUCK_REQUEST_TIMEOUT_MS?: string;
  TELEGRAM_BOT_TOKEN?: string;
  TELEGRAM_CHAT_ID?: string;
  SUB2_API_URL?: string;
  SUB2_API_KEY?: string;
  SUB2_PROXY_TEMPLATE_JSON?: string;
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

export type DuckAccountPublic = Omit<DuckAccountRow, "token"> & {
  token_prefix: string | null;
  token_suffix: string | null;
};

export type DuckAddressRow = {
  id: number;
  account_id: string;
  address: string;
  local_part: string;
  forwarding_mailbox_email: string | null;
  note: string | null;
  status: string;
  raw_json: string;
  created_at: string;
  updated_at: string;
};

