import { z, ZodError } from "zod";
import {
  createMailbox as createDashboardMailbox,
  deleteMailbox as deleteDashboardMailbox,
  getAuthMe,
  listApiKeys,
  listDashboardMailboxes,
  listWorkspaces,
  sendLoginCode,
  updateMailboxCommunicationSettings,
  verifyLoginCode,
  type ClawMailbox
} from "./claw-dashboard";
import {
  attachmentList,
  deleteRemoteMail,
  getAttachment,
  listRemoteInboxMessageIds,
  readRemoteMail,
  replyMail,
  sendMail
} from "./claw-mail";
import {
  deleteSettings,
  createDuckAccount,
  deleteDuckAddress,
  deleteMailById,
  deleteMailsByProviderIds,
  deleteDuckAccount,
  ensureSchema,
  getDuckAccountById,
  getMailboxByEmail,
  getMailboxById,
  getMailById,
  getMailByProviderId,
  getSetting,
  listActiveMailboxes,
  listAttachments,
  listDuckAccounts,
  listDuckAddresses,
  listMailboxes,
  listMailProviderIds,
  listMails,
  markMailRead,
  markDuckAccountError,
  markDuckAccountUsed,
  markMailboxDeleted,
  markMailboxesMissingDeleted,
  saveMail,
  saveDuckAddress,
  setSetting,
  upsertMailbox,
  updateDuckAccountToken,
  updateDuckAddress,
  updateMailboxCommSettings
} from "./db";
import {
  clearClawAuthSettings,
  getClawAuthStatus,
  getParentMailboxId,
  requireDashboardCookie,
  saveClawAuthSettings
} from "./runtime-config";
import type { Env, MailboxRow } from "./types";

type Params = Record<string, string>;
type Handler = (ctx: {
  request: Request;
  env: Env;
  params: Params;
  url: URL;
}) => Promise<Response> | Response;

type Route = {
  method: string;
  pattern: RegExp;
  keys: string[];
  handler: Handler;
};

const jsonHeaders = {
  "content-type": "application/json; charset=utf-8"
};

const createMailboxSchema = z.object({
  suffix: z.string().regex(/^[a-z0-9]{1,32}$/)
});

const DEFAULT_COMM_SETTINGS = {
  commLevel: 2,
  extReceiveType: 1,
  extSendType: 1
} as const;

const commSettingsSchema = z.object({
  commLevel: z.number().int().min(0).max(2),
  extReceiveType: z.number().int().min(0).max(1).optional(),
  extSendType: z.number().int().min(0).max(1).optional()
}).superRefine((value, ctx) => {
  if (value.commLevel !== 2) return;
  if (value.extReceiveType === undefined) {
    ctx.addIssue({
      code: "custom",
      path: ["extReceiveType"],
      message: "extReceiveType is required when commLevel is 2"
    });
  }
  if (value.extSendType === undefined) {
    ctx.addIssue({
      code: "custom",
      path: ["extSendType"],
      message: "extSendType is required when commLevel is 2"
    });
  }
});

const listenerSettingsSchema = z.object({
  logMode: z.enum(["quiet", "lifecycle", "verbose"]).optional(),
  reconnectMode: z.enum(["standard", "slow"]).optional()
});

const DEFAULT_LISTENER_SETTINGS = {
  logMode: "quiet",
  reconnectMode: "standard"
} as const;

const systemNetworkSettingsSchema = z.object({
  proxyUrl: z.string().trim().max(300).optional().or(z.literal("")),
  timeoutMs: z.coerce.number().int().min(1000).max(120000).optional()
});

const telegramSettingsSchema = z.object({
  enabled: z.boolean().optional(),
  botToken: z.string().trim().max(200).optional(),
  chatId: z.string().trim().max(120).optional()
});

const telegramMessageSchema = z.object({
  text: z.string().trim().min(1).max(4096)
});

const sub2SettingsSchema = z.object({
  apiUrl: z.string().trim().max(500).optional(),
  apiKey: z.string().trim().max(500).optional()
});

const sub2AccountPayloadSchema = z.object({
  input: z.unknown(),
  groupId: z.coerce.number().int().positive().optional()
});

const sendSchema = z.object({
  from: z.string().email(),
  to: z.array(z.string().email()).min(1),
  cc: z.array(z.string().email()).optional(),
  bcc: z.array(z.string().email()).optional(),
  subject: z.string().optional(),
  body: z.string().optional(),
  html: z.boolean().optional()
});

const replySchema = z.object({
  mailId: z.coerce.number().int().positive(),
  body: z.string().optional(),
  html: z.boolean().optional(),
  toAll: z.boolean().optional()
});

const clawLoginEmailSchema = z.string()
  .transform((value) => {
    const normalized = value.trim().replace(/＠/g, "@").toLowerCase();
    return normalized.includes("@") ? normalized : `${normalized}@163.com`;
  })
  .pipe(z.string().regex(/^[^\s@]+@163\.com$/, "请输入完整 163 登录邮箱"));

const sendCodeSchema = z.object({
  email: clawLoginEmailSchema
});

const duckAccountSchema = z.object({
  label: z.string().trim().min(1).max(80),
  token: z.string().trim().min(12)
});

const duckAccountTokenSchema = z.object({
  token: z.string().trim().min(12)
});

const duckAddressListSchema = z.object({
  accountId: z.string().min(1).optional()
});

const duckAddressCreateSchema = z.object({
  forwardingMailboxEmail: z.string().email().optional().or(z.literal("")),
  note: z.string().trim().max(300).optional()
});

const duckAddressUpdateSchema = z.object({
  forwardingMailboxEmail: z.string().email().optional().or(z.literal("")).nullable(),
  note: z.string().trim().max(300).optional().nullable()
});

const verifyCodeSchema = z.object({
  email: clawLoginEmailSchema,
  code: z.string().trim().regex(/^\d+$/)
});

function pendingLoginCookieKey(email: string): string {
  return `claw.pendingLoginCookie.${email}`;
}

const routes: Route[] = [
  route("GET", "/health", health),
  route("GET", "/api/connections", connectionsList),
  route("POST", "/api/connections/send-code", authSendCode),
  route("POST", "/api/connections/verify-code", authVerifyCode),
  route("POST", "/api/connections/:id/refresh", authRefresh),
  route("POST", "/api/connections/:id/logout", authLogout),
  route("GET", "/api/duck/accounts", duckAccountsList),
  route("GET", "/api/system/network-settings", systemNetworkSettingsGet),
  route("PUT", "/api/system/network-settings", systemNetworkSettingsUpdate),
  route("GET", "/api/duck/network-settings", systemNetworkSettingsGet),
  route("PUT", "/api/duck/network-settings", systemNetworkSettingsUpdate),
  route("GET", "/api/telegram/settings", telegramSettingsGet),
  route("PUT", "/api/telegram/settings", telegramSettingsUpdate),
  route("POST", "/api/telegram/send", telegramSend),
  route("GET", "/api/sub2/settings", sub2SettingsGet),
  route("PUT", "/api/sub2/settings", sub2SettingsUpdate),
  route("GET", "/api/sub2/groups", sub2GroupsList),
  route("POST", "/api/sub2/convert", sub2Convert),
  route("POST", "/api/sub2/push", sub2Push),
  route("POST", "/api/duck/accounts", duckAccountsCreate),
  route("PATCH", "/api/duck/accounts/:id", duckAccountsUpdate),
  route("DELETE", "/api/duck/accounts/:id", duckAccountsDelete),
  route("GET", "/api/duck/addresses", duckAddressesList),
  route("POST", "/api/duck/accounts/:id/addresses", duckAddressesCreate),
  route("PATCH", "/api/duck/addresses/:id", duckAddressesUpdate),
  route("DELETE", "/api/duck/addresses/:id", duckAddressesDelete),
  route("GET", "/api/auth/claw/status", authStatus),
  route("POST", "/api/auth/claw/send-code", authSendCode),
  route("POST", "/api/auth/claw/verify-code", authVerifyCode),
  route("POST", "/api/auth/claw/refresh", authRefresh),
  route("POST", "/api/auth/claw/logout", authLogout),
  route("GET", "/api/mailboxes", mailboxesList),
  route("POST", "/api/mailboxes", mailboxesCreate),
  route("POST", "/api/mailboxes/:id/comm-settings", mailboxesCommSettings),
  route("DELETE", "/api/mailboxes/:id", mailboxesDelete),
  route("GET", "/api/mails", mailsList),
  route("GET", "/api/mails/:id", mailsDetail),
  route("GET", "/api/mails/:id/attachments/:partId", mailsAttachment),
  route("DELETE", "/api/mails/:id", mailsDelete),
  route("POST", "/api/send", sendCreate),
  route("POST", "/api/reply", sendReply),
  route("GET", "/api/events", eventsStream),
  route("GET", "/api/listeners", listenersList),
  route("GET", "/api/listener-settings", listenerSettingsGet),
  route("PUT", "/api/listener-settings", listenerSettingsUpdate)
];

function route(method: string, path: string, handler: Handler): Route {
  const keys: string[] = [];
  const pattern = new RegExp(`^${
    path
      .split("/")
      .map((part) => {
        if (!part.startsWith(":")) return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        keys.push(part.slice(1));
        return "([^/]+)";
      })
      .join("/")
  }$`);
  return { method, pattern, keys, handler };
}

function matchRoute(method: string, pathname: string): { route: Route; params: Params } | null {
  for (const item of routes) {
    if (item.method !== method) continue;
    const match = item.pattern.exec(pathname);
    if (!match) continue;
    const params: Params = {};
    item.keys.forEach((key, index) => {
      params[key] = decodeURIComponent(match[index + 1]);
    });
    return { route: item, params };
  }
  return null;
}

function json(data: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(data), {
    ...init,
    headers: {
      ...jsonHeaders,
      ...Object.fromEntries(new Headers(init.headers))
    }
  });
}

function error(message: string, status = 500): Response {
  return json({ error: message }, { status });
}

async function readBody<T>(request: Request): Promise<T> {
  const text = await request.text();
  return (text ? JSON.parse(text) : {}) as T;
}

function extractAdminPassword(request: Request, url: URL): string | undefined {
  return request.headers.get("x-admin-password") ?? url.searchParams.get("token") ?? undefined;
}

function authorize(request: Request, env: Env, url: URL): Response | null {
  if (!url.pathname.startsWith("/api/")) return null;
  const expected = env.ADMIN_PASSWORD ?? "change-me";
  if (extractAdminPassword(request, url) !== expected) {
    return error("unauthorized", 401);
  }
  return null;
}

function upsertRemoteMailbox(env: Env, item: {
  id: string;
  email: string;
  prefix: string;
  displayName?: string | null;
  status?: string | null;
  openclawStatus?: string | null;
  installCommand?: string | null;
  authUrl?: string | null;
  commLevel?: number | null;
  extReceiveType?: number | null;
  extSendType?: number | null;
}): Promise<MailboxRow> {
  return upsertMailbox(env.DB, {
    id: item.id,
    email: item.email,
    prefix: item.prefix,
    displayName: item.displayName,
    status: item.status ?? "active",
    openclawStatus: item.openclawStatus,
    installCommand: item.installCommand,
    authUrl: item.authUrl,
    commLevel: item.commLevel,
    extReceiveType: item.extReceiveType,
    extSendType: item.extSendType
  });
}

function emailDomain(email: string): string {
  return email.split("@")[1] || "claw.163.com";
}

function mailboxRootPrefix(mailbox: ClawMailbox): string {
  if (mailbox.prefix) {
    return mailbox.prefix.split("@")[0].split(".")[0];
  }
  return mailbox.email.split("@")[0].split(".")[0];
}

async function saveMailboxes(env: Env, mailboxes: ClawMailbox[]): Promise<void> {
  for (const item of mailboxes) {
    await upsertMailbox(env.DB, {
      id: item.id,
      email: item.email,
      prefix: item.prefix,
      displayName: item.displayName,
      status: item.status ?? "active",
      openclawStatus: item.openclawStatus,
      installCommand: item.installCommand,
      authUrl: item.authUrl,
      commLevel: item.commLevel,
      extReceiveType: item.extReceiveType,
      extSendType: item.extSendType
    });
  }
  await markMailboxesMissingDeleted(env.DB, mailboxes.map((item) => item.email));
}

async function connectWithCookie(env: Env, cookie: string) {
  const [user, workspaces, apiKeys] = await Promise.all([
    getAuthMe(env, cookie),
    listWorkspaces(env, cookie),
    listApiKeys(env, cookie)
  ]);

  const workspace = workspaces.find((item) => item.status === "active") ?? workspaces[0];
  if (!workspace) {
    throw new Error("Claw account has no active workspace");
  }

  const apiKey =
    apiKeys.find((item) => item.status === "active" && item.defaultFlag === 1) ??
    apiKeys.find((item) => item.status === "active") ??
    apiKeys[0];
  if (!apiKey?.apiKey) {
    throw new Error("Claw account has no API key to use");
  }

  const mailboxes = await listDashboardMailboxes(env, {
    cookie,
    workspaceId: workspace.id
  });
  const primaryMailbox =
    mailboxes.find((item) => item.mailboxType === "primary") ??
    mailboxes.find((item) => !item.email.split("@")[0].includes(".")) ??
    mailboxes[0];
  if (!primaryMailbox) {
    throw new Error("Claw account has no mailbox");
  }

  const userEmail =
    typeof user?.email === "string" ? user.email :
    typeof user?.emailAddress === "string" ? user.emailAddress :
    null;

  await saveClawAuthSettings(env, {
    apiKey: apiKey.apiKey,
    dashboardCookie: cookie,
    userEmail,
    workspaceId: workspace.id,
    workspaceName: workspace.name,
    parentMailboxId: primaryMailbox.id,
    rootPrefix: mailboxRootPrefix(primaryMailbox),
    domain: emailDomain(primaryMailbox.email)
  });
  await saveMailboxes(env, mailboxes);

  return {
    auth: await getClawAuthStatus(env),
    syncedMailboxes: mailboxes.length
  };
}

async function syncMailboxInbox(env: Env, mailboxEmail: string): Promise<void> {
  const remoteIds = await listRemoteInboxMessageIds(env, mailboxEmail);
  const remoteIdSet = new Set(remoteIds);
  const localIds = await listMailProviderIds(env.DB, mailboxEmail);
  const staleLocalIds = localIds.filter((id) => !remoteIdSet.has(id));
  await deleteMailsByProviderIds(env.DB, mailboxEmail, staleLocalIds);

  for (const providerMailId of remoteIds) {
    if (await getMailByProviderId(env.DB, mailboxEmail, providerMailId)) continue;
    const mail = await readRemoteMail(env, mailboxEmail, providerMailId);
    await saveMail(env.DB, {
      providerMailId,
      mailboxEmail,
      source: mail.from?.[0] ?? null,
      address: mail.to?.[0] ?? mailboxEmail,
      subject: mail.subject ?? null,
      text: mail.text?.content ?? null,
      html: mail.html?.content ?? null,
      rawJson: JSON.stringify(mail),
      headerRaw: mail.headerRaw ?? null,
      hasAttachments: (mail.attachments ?? []).length > 0,
      receivedAt: mail.date ?? null,
      attachments: attachmentList(mail)
    });
  }
}

async function syncAllMailboxInboxes(env: Env): Promise<void> {
  for (const mailbox of await listActiveMailboxes(env.DB)) {
    await syncMailboxInbox(env, mailbox.email);
  }
}

function health() {
  return json({ ok: true, runtime: "cloudflare" });
}

async function authStatus({ env }: { env: Env }) {
  return json(await getClawAuthStatus(env));
}

function duckAccountId(): string {
  return `duck:${crypto.randomUUID()}`;
}

function normalizeDuckToken(value: string): string {
  return value.trim().replace(/^Bearer\s+/i, "").trim();
}

function normalizeOptionalEmail(value?: string | null): string | null {
  const trimmed = value?.trim().toLowerCase();
  return trimmed || null;
}

function maskBotToken(token: string): string | null {
  if (!token) return null;
  if (token.length <= 12) return `${token.slice(0, 4)}****`;
  return `${token.slice(0, 8)}...${token.slice(-4)}`;
}

function maskApiKey(value: string): string | null {
  if (!value) return null;
  if (value.length <= 12) return `${value.slice(0, 4)}****`;
  return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

async function getTelegramSettings(env: Env) {
  const botToken = (await getSetting(env.DB, "telegram.botToken") ?? env.TELEGRAM_BOT_TOKEN ?? "").trim();
  const chatId = (await getSetting(env.DB, "telegram.chatId") ?? env.TELEGRAM_CHAT_ID ?? "").trim();
  const enabled = (await getSetting(env.DB, "telegram.enabled") ?? "false") === "true";
  return { enabled, botToken, chatId };
}

function publicTelegramSettings(settings: { enabled: boolean; botToken: string; chatId: string }) {
  return {
    enabled: settings.enabled,
    chatId: settings.chatId,
    hasBotToken: Boolean(settings.botToken),
    botTokenPreview: maskBotToken(settings.botToken)
  };
}

async function getSub2Settings(env: Env) {
  const apiUrl = (await getSetting(env.DB, "sub2.apiUrl") ?? env.SUB2_API_URL ?? "").trim();
  const apiKey = (await getSetting(env.DB, "sub2.apiKey") ?? env.SUB2_API_KEY ?? "").trim();
  return { apiUrl, apiKey };
}

function publicSub2Settings(settings: { apiUrl: string; apiKey: string }) {
  return {
    apiUrl: settings.apiUrl,
    hasApiKey: Boolean(settings.apiKey),
    apiKeyPreview: maskApiKey(settings.apiKey)
  };
}

function asRecord(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`输入 JSON 缺少对象字段：${field}`);
  }
  return value as Record<string, unknown>;
}

function stringField(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value.trim() : "";
}

function normalizeExpiresAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    throw new Error("输入 JSON 的 expires 不是有效时间");
  }
  const local = new Date(date.getTime() + 8 * 60 * 60 * 1000);
  return `${local.toISOString().slice(0, 19)}+08:00`;
}

function readSub2TemplateProxies(env: Env): Array<Record<string, unknown> & { proxy_key?: string }> {
  const raw = env.SUB2_PROXY_TEMPLATE_JSON?.trim();
  if (!raw) return [];
  const parsed = JSON.parse(raw) as { proxies?: unknown };
  if (Array.isArray(parsed)) return parsed as Array<Record<string, unknown> & { proxy_key?: string }>;
  if (Array.isArray(parsed.proxies)) return parsed.proxies as Array<Record<string, unknown> & { proxy_key?: string }>;
  return [];
}

function convertChatGptSessionToSub2(input: unknown, env: Env) {
  const root = asRecord(input, "root");
  const user = asRecord(root.user, "user");
  const account = asRecord(root.account, "account");
  const email = stringField(user, "email");
  const accessToken = stringField(root, "accessToken");
  const expires = stringField(root, "expires");
  const accountId = stringField(account, "id");
  const userId = stringField(user, "id");
  const planType = stringField(account, "planType") || "unknown";
  if (!email) throw new Error("输入 JSON 缺少 user.email");
  if (!accessToken) throw new Error("输入 JSON 缺少 accessToken");
  if (!expires) throw new Error("输入 JSON 缺少 expires");
  if (!accountId) throw new Error("输入 JSON 缺少 account.id");
  if (!userId) throw new Error("输入 JSON 缺少 user.id");

  const proxies = readSub2TemplateProxies(env);
  const proxyKey = proxies.find((proxy) => typeof proxy.proxy_key === "string" && proxy.proxy_key.trim())?.proxy_key;
  if (!proxyKey) {
    throw new Error("Sub2 代理模板缺少 proxy_key，请配置 SUB2_PROXY_TEMPLATE_JSON");
  }

  return {
    exported_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    proxies,
    accounts: [
      {
        name: email,
        platform: "openai",
        type: "oauth",
        credentials: {
          access_token: accessToken,
          chatgpt_account_id: accountId,
          chatgpt_user_id: userId,
          client_id: "app_EMoamEEZ73f0CkXaXp7hrann",
          email,
          expires_at: normalizeExpiresAt(expires),
          plan_type: planType
        },
        extra: {
          email,
          openai_oauth_responses_websockets_v2_enabled: false,
          openai_oauth_responses_websockets_v2_mode: "off",
          privacy_mode: "training_off"
        },
        proxy_key: proxyKey,
        concurrency: 10,
        priority: 50,
        rate_multiplier: 1,
        auto_pause_on_expired: true
      }
    ]
  };
}

function normalizeSub2ImportUrl(apiUrl: string): string {
  const trimmed = apiUrl.trim();
  if (!trimmed) throw new Error("请先在系统设置里配置 Sub2API 地址");
  const url = new URL(trimmed);
  if (url.pathname.endsWith("/admin/accounts/data")) return url.toString();
  const basePath = url.pathname.replace(/\/+$/, "");
  const prefix = basePath.endsWith("/api/v1") ? basePath : `${basePath}/api/v1`;
  url.pathname = `${prefix}/admin/accounts/data`.replace(/\/+/g, "/");
  return url.toString();
}

function normalizeSub2GroupsUrl(apiUrl: string): string {
  const url = new URL(normalizeSub2ImportUrl(apiUrl));
  url.pathname = url.pathname.replace(/\/admin\/accounts\/data$/, "/admin/groups");
  // 账号推送只处理 OpenAI OAuth 账号，因此分组选择也只展示 OpenAI 可用分组。
  url.search = "page=1&page_size=1000&platform=openai&status=active";
  return url.toString();
}

function sub2ErrorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    for (const key of ["message", "error", "detail", "reason"]) {
      if (typeof record[key] === "string" && record[key]) return String(record[key]);
    }
  }
  return fallback;
}

function sub2AuthHeaders(apiKey: string): Record<string, string> {
  const trimmed = apiKey.trim();
  if (/^Bearer\s+/i.test(trimmed)) {
    return { authorization: trimmed };
  }
  // Sub2API 管理接口的 Admin API Key 使用 x-api-key；Bearer 仅保留给 JWT 登录令牌。
  return { "x-api-key": trimmed };
}

function numberField(record: Record<string, unknown>, key: string): number | null {
  const value = record[key];
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function extractSub2Groups(body: unknown): Array<{ id: number; name?: string }> {
  const candidates: unknown[] = [];
  if (Array.isArray(body)) {
    candidates.push(...body);
  } else if (body && typeof body === "object") {
    const root = body as Record<string, unknown>;
    if (Array.isArray(root.data)) candidates.push(...root.data);
    if (root.data && typeof root.data === "object" && Array.isArray((root.data as Record<string, unknown>).items)) {
      candidates.push(...((root.data as Record<string, unknown>).items as unknown[]));
    }
    if (Array.isArray(root.items)) candidates.push(...root.items);
  }
  return candidates.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const record = item as Record<string, unknown>;
    const id = numberField(record, "id");
    if (id === null) return [];
    return [{
      id,
      name: typeof record.name === "string" ? record.name : undefined
    }];
  });
}

function applySub2Group<T extends { accounts?: Array<Record<string, unknown>> }>(data: T, groupId: number): T {
  return {
    ...data,
    accounts: (data.accounts ?? []).map((account) => ({
      ...account,
      group_ids: [groupId]
    }))
  };
}

async function fetchSub2GroupsForSettings(settings: { apiUrl: string; apiKey: string }) {
  const response = await fetch(normalizeSub2GroupsUrl(settings.apiUrl), {
    method: "GET",
    headers: sub2AuthHeaders(settings.apiKey)
  });
  const result = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(`Sub2API 获取分组失败：${sub2ErrorMessage(result, response.statusText)}`);
  }
  if (result && typeof result === "object" && "code" in result && Number((result as { code?: unknown }).code) !== 0) {
    throw new Error(`Sub2API 获取分组失败：${sub2ErrorMessage(result, "接口返回失败")}`);
  }
  return extractSub2Groups(result);
}

function normalizeDuckAddress(value: string): { address: string; localPart: string } {
  const localPart = value.trim().toLowerCase().replace(/@duck\.com$/i, "");
  if (!/^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$/i.test(localPart)) {
    throw new Error("Duck API returned an invalid private address");
  }
  return {
    address: `${localPart}@duck.com`,
    localPart
  };
}

async function generateDuckAddress(token: string): Promise<{
  address: string;
  localPart: string;
  raw: unknown;
}> {
  const response = await fetch("https://quack.duckduckgo.com/api/email/addresses", {
    method: "POST",
    headers: {
      authorization: `Bearer ${normalizeDuckToken(token)}`,
      accept: "application/json"
    }
  });
  const text = await response.text();
  let body: unknown = null;
  if (text.trim()) {
    try {
      body = JSON.parse(text);
    } catch {
      throw new Error(`Duck address API returned non-JSON response: HTTP ${response.status}`);
    }
  }
  if (!response.ok) {
    const message = typeof body === "object" && body && "message" in body
      ? String((body as { message?: unknown }).message)
      : response.statusText || `HTTP ${response.status}`;
    throw new Error(`Duck address API error: ${message}`);
  }
  const rawAddress = typeof body === "object" && body && "address" in body
    ? (body as { address?: unknown }).address
    : undefined;
  if (typeof rawAddress !== "string" || !rawAddress.trim()) {
    throw new Error("Duck address API response did not include address");
  }
  return {
    ...normalizeDuckAddress(rawAddress),
    raw: body
  };
}

async function connectionsList({ env }: { env: Env }) {
  const status = await getClawAuthStatus(env);
  return json({
    items: [{
      ...status,
      id: status.id ?? "legacy",
      label: status.label ?? status.userEmail ?? status.workspaceName ?? "默认连接",
      status: status.status ?? (status.connected ? "active" : "incomplete")
    }]
  });
}

async function duckAccountsList({ env }: { env: Env }) {
  return json({ items: await listDuckAccounts(env.DB) });
}

async function systemNetworkSettingsGet({ env }: { env: Env }) {
  return json({
    proxyUrl: env.SYSTEM_PROXY_URL ?? env.DUCK_PROXY_URL ?? "",
    timeoutMs: Number(env.SYSTEM_REQUEST_TIMEOUT_MS ?? env.DUCK_REQUEST_TIMEOUT_MS ?? 10000)
  });
}

async function systemNetworkSettingsUpdate({ request, env }: { request: Request; env: Env }) {
  systemNetworkSettingsSchema.parse(await readBody(request));
  return systemNetworkSettingsGet({ env });
}

async function telegramSettingsGet({ env }: { env: Env }) {
  return json(publicTelegramSettings(await getTelegramSettings(env)));
}

async function telegramSettingsUpdate({ request, env }: { request: Request; env: Env }) {
  const body = telegramSettingsSchema.parse(await readBody(request));
  const current = await getTelegramSettings(env);
  const next = {
    enabled: body.enabled ?? current.enabled,
    botToken: body.botToken === undefined ? current.botToken : body.botToken.trim(),
    chatId: body.chatId === undefined ? current.chatId : body.chatId.trim()
  };
  await setSetting(env.DB, "telegram.enabled", String(next.enabled));
  await setSetting(env.DB, "telegram.botToken", next.botToken);
  await setSetting(env.DB, "telegram.chatId", next.chatId);
  return json(publicTelegramSettings(next));
}

async function telegramSend({ request, env }: { request: Request; env: Env }) {
  const body = telegramMessageSchema.parse(await readBody(request));
  const settings = await getTelegramSettings(env);
  if (!settings.enabled) {
    return error("Telegram 消息通知未启用", 400);
  }
  if (!settings.botToken || !settings.chatId) {
    return error("请先在系统设置里配置 Telegram Bot Token 和 Chat ID", 400);
  }

  const response = await fetch(`https://api.telegram.org/bot${settings.botToken}/sendMessage`, {
    method: "POST",
    headers: {
      "content-type": "application/json"
    },
    body: JSON.stringify({
      chat_id: settings.chatId,
      text: body.text
    })
  });
  const result = await response.json().catch(() => null) as { description?: string } | null;
  if (!response.ok) {
    return error(`Telegram 发送失败：${result?.description ?? response.statusText}`, 502);
  }
  return json({ success: true });
}

async function sub2SettingsGet({ env }: { env: Env }) {
  return json(publicSub2Settings(await getSub2Settings(env)));
}

async function sub2SettingsUpdate({ request, env }: { request: Request; env: Env }) {
  const body = sub2SettingsSchema.parse(await readBody(request));
  const current = await getSub2Settings(env);
  const next = {
    apiUrl: body.apiUrl === undefined ? current.apiUrl : body.apiUrl.trim(),
    apiKey: body.apiKey === undefined ? current.apiKey : body.apiKey.trim()
  };
  await setSetting(env.DB, "sub2.apiUrl", next.apiUrl);
  await setSetting(env.DB, "sub2.apiKey", next.apiKey);
  return json(publicSub2Settings(next));
}

async function sub2Convert({ request, env }: { request: Request; env: Env }) {
  const body = sub2AccountPayloadSchema.parse(await readBody(request));
  return json({ data: convertChatGptSessionToSub2(body.input, env) });
}

async function sub2GroupsList({ env }: { env: Env }) {
  const settings = await getSub2Settings(env);
  if (!settings.apiKey) return error("请先在系统设置里配置 Sub2API APIKey", 400);
  return json({ items: await fetchSub2GroupsForSettings(settings) });
}

async function sub2Push({ request, env }: { request: Request; env: Env }) {
  const body = sub2AccountPayloadSchema.parse(await readBody(request));
  const settings = await getSub2Settings(env);
  if (!settings.apiKey) return error("请先在系统设置里配置 Sub2API APIKey", 400);
  if (!body.groupId) return error("请选择要推送到的 Sub2 分组", 400);
  const data = applySub2Group(convertChatGptSessionToSub2(body.input, env), body.groupId);
  const response = await fetch(normalizeSub2ImportUrl(settings.apiUrl), {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...sub2AuthHeaders(settings.apiKey)
    },
    body: JSON.stringify({
      data,
      skip_default_group_bind: true
    })
  });
  const result = await response.json().catch(() => null);
  if (!response.ok) {
    return error(`Sub2API 推送失败：${sub2ErrorMessage(result, response.statusText)}`, 502);
  }
  if (result && typeof result === "object" && "code" in result && Number((result as { code?: unknown }).code) !== 0) {
    return error(`Sub2API 推送失败：${sub2ErrorMessage(result, "接口返回失败")}`, 502);
  }
  return json({ success: true, data, response: result });
}

async function duckAccountsCreate({ request, env }: { request: Request; env: Env }) {
  const body = duckAccountSchema.parse(await readBody(request));
  const account = await createDuckAccount(env.DB, {
    id: duckAccountId(),
    label: body.label,
    token: normalizeDuckToken(body.token)
  });
  return json(account, { status: 201 });
}

async function duckAccountsDelete({ env, params }: { env: Env; params: Params }) {
  if (!await deleteDuckAccount(env.DB, params.id)) {
    return error("Duck account not found", 404);
  }
  return json({ success: true });
}

async function duckAccountsUpdate({
  request,
  env,
  params
}: {
  request: Request;
  env: Env;
  params: Params;
}) {
  const body = duckAccountTokenSchema.parse(await readBody(request));
  const account = await updateDuckAccountToken(env.DB, params.id, normalizeDuckToken(body.token));
  if (!account) {
    return error("Duck account not found", 404);
  }
  return json(account);
}

async function duckAddressesList({ env, url }: { env: Env; url: URL }) {
  const query = duckAddressListSchema.parse(Object.fromEntries(url.searchParams));
  return json({
    items: await listDuckAddresses(env.DB, {
      accountId: query.accountId
    })
  });
}

async function duckAddressesCreate({
  request,
  env,
  params
}: {
  request: Request;
  env: Env;
  params: Params;
}) {
  const account = await getDuckAccountById(env.DB, params.id);
  if (!account || account.status === "disabled" || !account.token) {
    return error("Duck account not found or disabled", 404);
  }
  const body = duckAddressCreateSchema.parse(await readBody(request));
  try {
    const generated = await generateDuckAddress(account.token);
    const row = await saveDuckAddress(env.DB, {
      accountId: account.id,
      address: generated.address,
      localPart: generated.localPart,
      forwardingMailboxEmail: normalizeOptionalEmail(body.forwardingMailboxEmail),
      note: body.note ?? null,
      rawJson: JSON.stringify(generated.raw)
    });
    await markDuckAccountUsed(env.DB, account.id);
    return json(row, { status: 201 });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    await markDuckAccountError(env.DB, account.id, message);
    throw err;
  }
}

async function duckAddressesUpdate({
  request,
  env,
  params
}: {
  request: Request;
  env: Env;
  params: Params;
}) {
  const body = duckAddressUpdateSchema.parse(await readBody(request));
  const row = await updateDuckAddress(env.DB, Number(params.id), {
    forwardingMailboxEmail: body.forwardingMailboxEmail === undefined
      ? undefined
      : normalizeOptionalEmail(body.forwardingMailboxEmail),
    note: body.note === undefined ? undefined : body.note ?? null
  });
  if (!row) {
    return error("Duck address not found", 404);
  }
  return json(row);
}

async function duckAddressesDelete({ env, params }: { env: Env; params: Params }) {
  if (!await deleteDuckAddress(env.DB, Number(params.id))) {
    return error("Duck address not found", 404);
  }
  return json({ success: true });
}

async function authSendCode({ request, env }: { request: Request; env: Env }) {
  const body = sendCodeSchema.parse(await readBody(request));
  const pendingCookie = await sendLoginCode(body.email);
  if (pendingCookie) {
    await setSetting(env.DB, pendingLoginCookieKey(body.email), pendingCookie);
  }
  return json({ success: true });
}

async function authVerifyCode({ request, env }: { request: Request; env: Env }) {
  const body = verifyCodeSchema.parse(await readBody(request));
  const cookie = await verifyLoginCode(
    body.email,
    body.code,
    await getSetting(env.DB, pendingLoginCookieKey(body.email))
  );
  await deleteSettings(env.DB, [pendingLoginCookieKey(body.email)]);
  return json(await connectWithCookie(env, cookie));
}

async function authRefresh({ env }: { env: Env }) {
  return json(await connectWithCookie(env, await requireDashboardCookie(env)));
}

async function authLogout({ env }: { env: Env }) {
  await clearClawAuthSettings(env);
  return json(await getClawAuthStatus(env));
}

async function mailboxesList({ env, url }: { env: Env; url: URL }) {
  if (url.searchParams.get("sync") === "true") {
    const remote = await listDashboardMailboxes(env);
    for (const item of remote) {
      await upsertRemoteMailbox(env, item);
    }
    await markMailboxesMissingDeleted(env.DB, remote.map((item) => item.email));
  }
  return json({ items: await listMailboxes(env.DB, false) });
}

async function mailboxesCreate({ request, env }: { request: Request; env: Env }) {
  const body = createMailboxSchema.parse(await readBody(request));
  const mailbox = await createDashboardMailbox(env, body.suffix);
  await updateMailboxCommunicationSettings(env, mailbox.id, DEFAULT_COMM_SETTINGS);
  const row = await upsertRemoteMailbox(env, {
    ...mailbox,
    commLevel: DEFAULT_COMM_SETTINGS.commLevel,
    extReceiveType: DEFAULT_COMM_SETTINGS.extReceiveType,
    extSendType: DEFAULT_COMM_SETTINGS.extSendType
  });
  return json(row, { status: 201 });
}

async function mailboxesCommSettings({
  request,
  env,
  params
}: {
  request: Request;
  env: Env;
  params: Params;
}) {
  const mailbox = await getMailboxById(env.DB, params.id);
  if (!mailbox) {
    return error("mailbox not found", 404);
  }

  const body = commSettingsSchema.parse(await readBody(request));
  const dashboardPayload = body.commLevel === 2
    ? {
        commLevel: body.commLevel,
        extReceiveType: body.extReceiveType!,
        extSendType: body.extSendType!
      }
    : { commLevel: body.commLevel };

  await updateMailboxCommunicationSettings(env, params.id, dashboardPayload);
  const updated = await updateMailboxCommSettings(env.DB, params.id, {
    commLevel: body.commLevel,
    extReceiveType: body.commLevel === 2 ? body.extReceiveType : null,
    extSendType: body.commLevel === 2 ? body.extSendType : null
  });
  return json(updated ?? await getMailboxById(env.DB, params.id));
}

async function mailboxesDelete({ env, params }: { env: Env; params: Params }) {
  const mailbox = await getMailboxById(env.DB, params.id);
  if (!mailbox) {
    return json({ success: true });
  }
  if (params.id === await getParentMailboxId(env)) {
    return error("primary mailbox cannot be deleted here", 400);
  }
  await deleteDashboardMailbox(env, params.id);
  await markMailboxDeleted(env.DB, params.id);
  return json({ success: true });
}

async function mailsList({ env, url }: { env: Env; url: URL }) {
  const mailbox = url.searchParams.get("mailbox")?.trim().toLowerCase() || undefined;
  const sync = url.searchParams.get("sync");
  const limit = Math.min(Math.max(Number(url.searchParams.get("limit") ?? 50) || 50, 1), 100);
  const offset = Math.max(Number(url.searchParams.get("offset") ?? 0) || 0, 0);
  if (sync === "true" && mailbox) {
    await syncMailboxInbox(env, mailbox);
  } else if (sync === "true") {
    await syncAllMailboxInboxes(env);
  }
  return json(await listMails(env.DB, {
    mailboxEmail: mailbox,
    limit,
    offset
  }));
}

async function mailsDetail({ env, params }: { env: Env; params: Params }) {
  const mail = await markMailRead(env.DB, Number(params.id));
  if (!mail) {
    return error("mail not found", 404);
  }
  return json({
    ...mail,
    parsed: JSON.parse(mail.raw_json),
    attachments: await listAttachments(env.DB, mail.id)
  });
}

async function mailsAttachment({ env, params }: { env: Env; params: Params }) {
  const mail = await getMailById(env.DB, Number(params.id));
  if (!mail) {
    return error("mail not found", 404);
  }
  const attachment = await getAttachment(env, mail.mailbox_email, mail.provider_mail_id, params.partId);
  return new Response(attachment.body, {
    headers: {
      "content-type": attachment.contentType,
      "content-disposition": `attachment; filename="${encodeURIComponent(attachment.filename)}"`
    }
  });
}

async function mailsDelete({ env, params }: { env: Env; params: Params }) {
  const mail = await getMailById(env.DB, Number(params.id));
  if (!mail) {
    return json({ success: true });
  }
  await deleteRemoteMail(env, mail.mailbox_email, mail.provider_mail_id);
  await deleteMailById(env.DB, Number(params.id));
  return json({ success: true });
}

async function sendCreate({ request, env }: { request: Request; env: Env }) {
  const body = sendSchema.parse(await readBody(request));
  const mailbox = await getMailboxByEmail(env.DB, body.from.trim().toLowerCase());
  if (!mailbox) {
    return error("from mailbox is not managed by this app", 400);
  }
  return json(await sendMail(env, body));
}

async function sendReply({ request, env }: { request: Request; env: Env }) {
  const body = replySchema.parse(await readBody(request));
  const mail = await getMailById(env.DB, body.mailId);
  if (!mail) {
    return error("mail not found", 404);
  }
  return json(await replyMail(env, {
    mailboxEmail: mail.mailbox_email,
    providerMailId: mail.provider_mail_id,
    body: body.body,
    html: body.html,
    toAll: body.toAll
  }));
}

function eventsStream() {
  const body = new ReadableStream({
    start(controller) {
      const encoder = new TextEncoder();
      controller.enqueue(encoder.encode("event: cloudflare-mode\n"));
      controller.enqueue(encoder.encode("data: {\"mode\":\"manual-sync\"}\n\n"));
      controller.close();
    }
  });
  return new Response(body, {
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform"
    }
  });
}

async function listenersList({ env }: { env: Env }) {
  const mailboxes = await listActiveMailboxes(env.DB);
  return json({
    items: mailboxes.map((mailbox) => ({
      email: mailbox.email,
      status: "manual-sync",
      startedAt: null,
      lastEventAt: null,
      error: "Cloudflare deployment uses request-triggered inbox sync instead of persistent listeners."
    }))
  });
}

async function listenerSettingsGet() {
  return json(DEFAULT_LISTENER_SETTINGS);
}

async function listenerSettingsUpdate({ request }: { request: Request }) {
  listenerSettingsSchema.parse(await readBody(request));
  return json(DEFAULT_LISTENER_SETTINGS);
}

async function handleApi(request: Request, env: Env, url: URL): Promise<Response> {
  const unauthorized = authorize(request, env, url);
  if (unauthorized) return unauthorized;

  const matched = matchRoute(request.method, url.pathname);
  if (!matched) return error("not found", 404);

  try {
    await ensureSchema(env.DB);
    return await matched.route.handler({
      request,
      env,
      params: matched.params,
      url
    });
  } catch (err) {
    if (err instanceof ZodError) {
      return json({ error: "invalid input", details: err.issues }, { status: 400 });
    }
    return error(err instanceof Error ? err.message : "internal server error", 500);
  }
}

async function serveAsset(request: Request, env: Env): Promise<Response> {
  const response = await env.ASSETS.fetch(request);
  if (response.status !== 404) return response;

  const url = new URL(request.url);
  if (request.method === "GET" && !url.pathname.includes(".")) {
    return env.ASSETS.fetch(new Request(new URL("/", url), request));
  }
  return response;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health" || url.pathname.startsWith("/api/")) {
      return handleApi(request, env, url);
    }
    return serveAsset(request, env);
  }
};
