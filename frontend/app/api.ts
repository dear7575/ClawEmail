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
  read_at: string | null;
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

export type ListenerSettings = {
  logMode: "quiet" | "lifecycle" | "verbose";
  reconnectMode: "standard" | "slow";
  inboxSyncInterval: "manual" | "30" | "60" | "300";
};

export type DuckAccount = {
  id: string;
  label: string;
  status: string;
  last_error: string | null;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
  token_prefix: string | null;
  token_suffix: string | null;
};

export type DuckAddress = {
  id: number;
  account_id: string;
  address: string;
  local_part: string;
  forwarding_mailbox_email: string | null;
  note: string | null;
  status: string;
  raw_json: string;
  has_openai_password: boolean;
  has_openai_auth_json: boolean;
  sub2_pushed_at: string | null;
  sub2_push_mode: string | null;
  sub2_push_email: string | null;
  is_sub2_pushed: boolean;
  created_at: string;
  updated_at: string;
};

export type SystemNetworkSettings = {
  proxyUrl: string;
  timeoutMs: number;
  openAiOtpTimeoutMs: number;
};

export type TelegramSettings = {
  enabled: boolean;
  chatId: string;
  hasBotToken: boolean;
  botTokenPreview: string | null;
};

export type Sub2Settings = {
  apiUrl: string;
  hasApiKey: boolean;
  apiKeyPreview: string | null;
  defaultGroupId: number | null;
  openAiAuthLoginEnabled: boolean;
};

export type Sub2Group = {
  id: number;
  name?: string;
};

export type Sub2PushResult = {
  success?: boolean;
  data: unknown;
  response?: unknown;
  pushMode?: "sub2_auth" | "oauth_token" | "fallback_oauth_token";
  fallbackReason?: string;
  telegram?: {
    sent: boolean;
    error?: string;
  };
};

export type OpenAiDuckPushJobStatus = {
  success: boolean;
  jobId: string;
  status: "running" | "succeeded" | "failed";
  result?: (Sub2PushResult & { email?: string }) | null;
  error?: string | null;
};

export type RuntimeMode = "server" | "unknown";

export type PagedResult<T> = {
  items: T[];
  total: number;
  count: number;
  limit: number;
  offset: number;
};

let runtimeMode: RuntimeMode = "unknown";

function readStoredAdminPassword(): string {
  if (typeof localStorage === "undefined") return "";
  return localStorage.getItem("adminPassword") ?? "";
}

let adminPassword = readStoredAdminPassword();

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
  let data: any = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: text };
    }
  }
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

function parseResponseBody(text: string): any {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { error: text };
  }
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

export async function deleteConnection(connectionId: string): Promise<void> {
  await requestJson<{ success: boolean }>(`/api/connections/${encodeURIComponent(connectionId)}/delete`, {
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
  connectionId?: string,
  keyword?: string
): Promise<PagedResult<MailSummary>> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (mailbox) params.set("mailbox", mailbox);
  if (sync) params.set("sync", "true");
  if (connectionId) params.set("connectionId", connectionId);
  if (keyword) params.set("keyword", keyword);
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

export type ClearMailsResult = {
  success: boolean;
  deleted: number;
  failed: number;
  errors: Array<{
    id: number;
    mailboxEmail: string;
    providerMailId: string;
    error: string;
  }>;
};

export async function clearMails(input: {
  mailbox?: string;
  connectionId?: string;
} = {}): Promise<ClearMailsResult> {
  const params = new URLSearchParams();
  if (input.mailbox) params.set("mailbox", input.mailbox);
  if (input.connectionId) params.set("connectionId", input.connectionId);
  const query = params.toString();
  const result = await fetch(`/api/mails${query ? `?${query}` : ""}`, {
    method: "DELETE",
    headers: {
      "x-admin-password": adminPassword
    }
  });
  const text = await result.text();
  const data = parseResponseBody(text);
  if (!result.ok && result.status !== 207) {
    throw new Error(data?.error ?? `HTTP ${result.status}`);
  }
  return data as ClearMailsResult;
}

export type MarkMailsReadResult = {
  success: boolean;
  updated: number;
};

export async function markMailsRead(input: {
  mailbox?: string;
  connectionId?: string;
} = {}): Promise<MarkMailsReadResult> {
  const params = new URLSearchParams();
  if (input.mailbox) params.set("mailbox", input.mailbox);
  if (input.connectionId) params.set("connectionId", input.connectionId);
  const query = params.toString();
  return requestJson<MarkMailsReadResult>(`/api/mails/mark-read${query ? `?${query}` : ""}`, {
    method: "POST"
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

export async function fetchListenerSettings(): Promise<ListenerSettings> {
  return requestJson<ListenerSettings>("/api/listener-settings");
}

export async function updateListenerSettings(input: ListenerSettings): Promise<ListenerSettings> {
  return requestJson<ListenerSettings>("/api/listener-settings", {
    method: "PUT",
    body: JSON.stringify(input)
  });
}

export async function fetchDuckAccounts(): Promise<DuckAccount[]> {
  const data = await requestJson<{ items: DuckAccount[] }>("/api/duck/accounts");
  return data.items;
}

export async function fetchSystemNetworkSettings(): Promise<SystemNetworkSettings> {
  return requestJson<SystemNetworkSettings>("/api/system/network-settings");
}

export async function updateSystemNetworkSettings(input: SystemNetworkSettings): Promise<SystemNetworkSettings> {
  return requestJson<SystemNetworkSettings>("/api/system/network-settings", {
    method: "PUT",
    body: JSON.stringify(input)
  });
}

export async function fetchTelegramSettings(): Promise<TelegramSettings> {
  return requestJson<TelegramSettings>("/api/telegram/settings");
}

export async function updateTelegramSettings(input: {
  enabled?: boolean;
  botToken?: string;
  chatId?: string;
}): Promise<TelegramSettings> {
  return requestJson<TelegramSettings>("/api/telegram/settings", {
    method: "PUT",
    body: JSON.stringify(input)
  });
}

export async function sendTelegramNotification(text: string): Promise<void> {
  await requestJson<{ success: boolean }>("/api/telegram/send", {
    method: "POST",
    body: JSON.stringify({ text })
  });
}

export async function fetchSub2Settings(): Promise<Sub2Settings> {
  return requestJson<Sub2Settings>("/api/sub2/settings");
}

export async function updateSub2Settings(input: {
  apiUrl?: string;
  apiKey?: string;
  defaultGroupId?: number | null;
  openAiAuthLoginEnabled?: boolean;
}): Promise<Sub2Settings> {
  return requestJson<Sub2Settings>("/api/sub2/settings", {
    method: "PUT",
    body: JSON.stringify(input)
  });
}

export async function fetchSub2Groups(): Promise<Sub2Group[]> {
  const result = await requestJson<{ items: Sub2Group[] }>("/api/sub2/groups");
  return result.items;
}

export async function convertSub2Account(input: unknown): Promise<unknown> {
  const result = await requestJson<{ data: unknown }>("/api/sub2/convert", {
    method: "POST",
    body: JSON.stringify({ input })
  });
  return result.data;
}

export async function pushSub2Account(input: unknown, groupId: number): Promise<Sub2PushResult> {
  return requestJson<Sub2PushResult>("/api/sub2/push", {
    method: "POST",
    body: JSON.stringify({ input, groupId })
  });
}

export async function pushOpenAiDuckAddressToSub2(
  duckAddressId: number,
  groupId?: number | null,
  options?: { onPoll?: (job: OpenAiDuckPushJobStatus) => void | Promise<void> }
): Promise<Sub2PushResult & { email?: string }> {
  const started = await requestJson<OpenAiDuckPushJobStatus>("/api/openai/duck-push-sub2", {
    method: "POST",
    body: JSON.stringify({ duckAddressId, groupId })
  });
  await options?.onPoll?.(started);
  return waitForOpenAiDuckPushJob(started.jobId, options);
}

export async function fetchOpenAiDuckPushJob(jobId: string): Promise<OpenAiDuckPushJobStatus> {
  return requestJson<OpenAiDuckPushJobStatus>(`/api/openai/duck-push-sub2/jobs/${encodeURIComponent(jobId)}`);
}

async function waitForOpenAiDuckPushJob(
  jobId: string,
  options?: { onPoll?: (job: OpenAiDuckPushJobStatus) => void | Promise<void> }
): Promise<Sub2PushResult & { email?: string }> {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    const job = await fetchOpenAiDuckPushJob(jobId);
    await options?.onPoll?.(job);
    if (job.status === "succeeded" && job.result) return job.result;
    if (job.status === "failed") throw new Error(job.error || "OpenAI Duck 推送失败");
    await new Promise((resolve) => globalThis.setTimeout(resolve, 2000));
  }
  throw new Error("OpenAI Duck 推送仍在后台执行，请稍后刷新 Duck 地址状态");
}

export async function fetchDuckAddressOpenAiPassword(duckAddressId: number): Promise<string> {
  const result = await requestJson<{ password: string }>(`/api/duck/addresses/${duckAddressId}/openai-password`);
  return result.password;
}

export async function fetchDuckAddressOpenAiAuthJson(duckAddressId: number): Promise<string> {
  const result = await requestJson<{ authJson: string }>(`/api/duck/addresses/${duckAddressId}/openai-auth-json`);
  return result.authJson;
}

export async function updateDuckAddressOpenAiCredentials(
  duckAddressId: number,
  input: {
    password?: string | null;
    authJson?: string | null;
  }
): Promise<DuckAddress> {
  return requestJson<DuckAddress>(`/api/duck/addresses/${duckAddressId}/openai-credentials`, {
    method: "PATCH",
    body: JSON.stringify(input)
  });
}

export async function createDuckAccount(input: {
  label: string;
  token: string;
}): Promise<DuckAccount> {
  return requestJson<DuckAccount>("/api/duck/accounts", {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export async function deleteDuckAccount(id: string): Promise<void> {
  await requestJson<{ success: boolean }>(`/api/duck/accounts/${encodeURIComponent(id)}`, {
    method: "DELETE"
  });
}

export async function updateDuckAccountToken(id: string, token: string): Promise<DuckAccount> {
  return requestJson<DuckAccount>(`/api/duck/accounts/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ token })
  });
}

export async function fetchDuckAddresses(input: {
  accountId?: string;
  limit?: number;
  offset?: number;
  keyword?: string;
} = {}): Promise<PagedResult<DuckAddress>> {
  const params = new URLSearchParams();
  if (input.accountId) params.set("accountId", input.accountId);
  params.set("limit", String(input.limit ?? 50));
  params.set("offset", String(input.offset ?? 0));
  if (input.keyword) params.set("keyword", input.keyword);
  const query = params.toString();
  return requestJson<PagedResult<DuckAddress>>(`/api/duck/addresses${query ? `?${query}` : ""}`);
}

export async function generateDuckAddress(
  accountId: string,
  input: {
    forwardingMailboxEmail?: string;
    note?: string;
  } = {}
): Promise<DuckAddress> {
  return requestJson<DuckAddress>(`/api/duck/accounts/${encodeURIComponent(accountId)}/addresses`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export async function updateDuckAddress(
  id: number,
  input: {
    forwardingMailboxEmail?: string | null;
    note?: string | null;
  }
): Promise<DuckAddress> {
  return requestJson<DuckAddress>(`/api/duck/addresses/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input)
  });
}

export async function deleteDuckAddress(id: number): Promise<void> {
  await requestJson<{ success: boolean }>(`/api/duck/addresses/${id}`, {
    method: "DELETE"
  });
}
