export type Mailbox = {
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

export type MailSummary = {
  id: number;
  connection_id: string | null;
  provider_mail_id: string;
  mailbox_email: string;
  source: string | null;
  address: string | null;
  subject: string | null;
  text: string | null;
  html: string | null;
  has_attachments: number;
  received_at: string | null;
  created_at: string;
};

export type MailDetail = MailSummary & {
  parsed: any;
  attachments: Array<{
    id: number;
    mail_id: number;
    provider_part_id: string;
    filename: string | null;
    content_type: string | null;
    size: number | null;
  }>;
};

export type ClawAuthStatus = {
  id: string | null;
  connected: boolean;
  hasApiKey: boolean;
  hasDashboardCookie: boolean;
  userEmail: string | null;
  workspaceId: string | null;
  workspaceName: string | null;
  parentMailboxId: string | null;
  rootPrefix: string | null;
  domain: string | null;
  apiKeyPrefix: string | null;
  apiKeySuffix: string | null;
  status: string | null;
  label: string | null;
};

export type ListenerSnapshot = {
  connectionId?: string;
  email: string;
  status: string;
  startedAt?: string | null;
  lastEventAt?: string | null;
  error?: string | null;
};

export type RuntimeMode = "node" | "cloudflare" | "unknown";

let runtimeMode: RuntimeMode = "unknown";

let adminPassword = localStorage.getItem("adminPassword") ?? "";

export function getAdminPassword() {
  return adminPassword;
}

export function setAdminPassword(value: string) {
  adminPassword = value;
  if (value) {
    localStorage.setItem("adminPassword", value);
  } else {
    localStorage.removeItem("adminPassword");
  }
}

async function requestJson<T>(
  path: string,
  init: RequestInit = {},
  adminPasswordOverride = adminPassword
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("x-admin-password", adminPasswordOverride);
  if (init.body !== undefined && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }

  const response = await fetch(path, {
    ...init,
    headers
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const details = Array.isArray(data?.details)
      ? data.details
        .map((item: any) => {
          const path = Array.isArray(item?.path) ? item.path.join(".") : "";
          return path ? `${path}: ${item?.message ?? "invalid"}` : item?.message ?? "invalid";
        })
        .join("; ")
      : "";
    throw new Error(details ? `${data?.error ?? `HTTP ${response.status}`}: ${details}` : data?.error ?? `HTTP ${response.status}`);
  }
  return data as T;
}

export async function verifyAdminPassword(value: string): Promise<ClawAuthStatus> {
  return requestJson<ClawAuthStatus>("/api/auth/claw/status", {}, value);
}

export async function fetchConnections(): Promise<ClawAuthStatus[]> {
  const data = await requestJson<{ items: ClawAuthStatus[] }>("/api/connections");
  return data.items;
}

export async function sendConnectionLoginCode(email: string): Promise<void> {
  await requestJson<{ success: boolean }>("/api/connections/send-code", {
    method: "POST",
    body: JSON.stringify({ email })
  });
}

export async function verifyConnectionLoginCode(email: string, code: string): Promise<{
  connection: ClawAuthStatus;
  auth: ClawAuthStatus;
  syncedMailboxes: number;
}> {
  return requestJson("/api/connections/verify-code", {
    method: "POST",
    body: JSON.stringify({ email, code })
  });
}

export async function refreshConnection(connectionId: string): Promise<{
  connection: ClawAuthStatus;
  auth: ClawAuthStatus;
  syncedMailboxes: number;
}> {
  return requestJson(`/api/connections/${encodeURIComponent(connectionId)}/refresh`, {
    method: "POST"
  });
}

export async function disconnectConnection(connectionId: string): Promise<ClawAuthStatus> {
  return requestJson(`/api/connections/${encodeURIComponent(connectionId)}/logout`, {
    method: "POST"
  });
}

export async function fetchMailboxes(sync = false, connectionId?: string): Promise<Mailbox[]> {
  const params = new URLSearchParams();
  if (sync) params.set("sync", "true");
  if (connectionId) params.set("connectionId", connectionId);
  const query = params.toString();
  const data = await requestJson<{ items: Mailbox[] }>(`/api/mailboxes${query ? `?${query}` : ""}`);
  return data.items;
}

export async function createMailbox(suffix: string, connectionId?: string): Promise<Mailbox> {
  return requestJson<Mailbox>("/api/mailboxes", {
    method: "POST",
    body: JSON.stringify({ suffix, connectionId })
  });
}

export async function deleteMailbox(id: string): Promise<void> {
  await requestJson<{ success: boolean }>(`/api/mailboxes/${encodeURIComponent(id)}`, {
    method: "DELETE"
  });
}

export type CommunicationSettingsInput = {
  commLevel: 0 | 1 | 2;
  extReceiveType?: 0 | 1;
  extSendType?: 0 | 1;
};

export async function updateMailboxCommunicationSettings(
  id: string,
  input: CommunicationSettingsInput
): Promise<Mailbox> {
  return requestJson<Mailbox>(`/api/mailboxes/${encodeURIComponent(id)}/comm-settings`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export async function fetchMails(
  mailbox?: string,
  limit = 50,
  offset = 0,
  sync = false,
  connectionId?: string
): Promise<{ items: MailSummary[]; count: number }> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (mailbox) params.set("mailbox", mailbox);
  if (sync) params.set("sync", "true");
  if (connectionId) params.set("connectionId", connectionId);
  return requestJson(`/api/mails?${params.toString()}`);
}

export async function fetchMail(id: number): Promise<MailDetail> {
  return requestJson(`/api/mails/${id}`);
}

export async function deleteMail(id: number): Promise<void> {
  await requestJson<{ success: boolean }>(`/api/mails/${id}`, {
    method: "DELETE"
  });
}

export type SendMailInput = {
  from: string;
  to: string[];
  cc?: string[];
  bcc?: string[];
  subject?: string;
  body?: string;
  html?: boolean;
};

export async function sendMail(input: SendMailInput) {
  return requestJson<{ status: "sent" }>("/api/send", {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export type ReplyMailInput = {
  mailId: number;
  body?: string;
  html?: boolean;
  toAll?: boolean;
};

export async function replyMail(input: ReplyMailInput) {
  return requestJson<{ status: "sent" }>("/api/reply", {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function createEventSource(): EventSource {
  return new EventSource(`/api/events?token=${encodeURIComponent(adminPassword)}`);
}

export function getRuntimeMode(): RuntimeMode {
  return runtimeMode;
}

export function setRuntimeMode(value: RuntimeMode) {
  runtimeMode = value;
}

export async function fetchClawAuthStatus(): Promise<ClawAuthStatus> {
  return requestJson<ClawAuthStatus>("/api/auth/claw/status");
}

export async function sendClawLoginCode(email: string): Promise<void> {
  await requestJson<{ success: boolean }>("/api/auth/claw/send-code", {
    method: "POST",
    body: JSON.stringify({ email })
  });
}

export async function verifyClawLoginCode(email: string, code: string): Promise<{
  auth: ClawAuthStatus;
  syncedMailboxes: number;
}> {
  return requestJson("/api/auth/claw/verify-code", {
    method: "POST",
    body: JSON.stringify({ email, code })
  });
}

export async function refreshClawConnection(): Promise<{
  auth: ClawAuthStatus;
  syncedMailboxes: number;
}> {
  return requestJson("/api/auth/claw/refresh", {
    method: "POST"
  });
}

export async function disconnectClaw(): Promise<ClawAuthStatus> {
  return requestJson("/api/auth/claw/logout", {
    method: "POST"
  });
}

export async function fetchListeners(): Promise<ListenerSnapshot[]> {
  const data = await requestJson<{ items: ListenerSnapshot[] }>("/api/listeners");
  return data.items;
}
