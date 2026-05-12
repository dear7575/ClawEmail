import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { config } from "./config";
import { getSetting, setSetting } from "./db";
import { fetchWithNetworkOptions } from "./network-fetch";
import { getSystemNetworkSettings } from "./network-settings";

export type Sub2Settings = {
  apiUrl: string;
  apiKey: string;
  defaultGroupId: number | null;
};

export type Sub2PublicSettings = {
  apiUrl: string;
  hasApiKey: boolean;
  apiKeyPreview: string | null;
  defaultGroupId: number | null;
};

export type Sub2Proxy = Record<string, unknown> & {
  proxy_key?: string;
  name?: string;
  protocol?: string;
  host?: string;
  port?: number;
  username?: string;
  password?: string;
};

export type Sub2Account = {
  name: string;
  platform: string;
  type: string;
  credentials: Record<string, unknown>;
  extra: Record<string, unknown>;
  proxy_key: string;
  concurrency: number;
  priority: number;
  rate_multiplier: number;
  auto_pause_on_expired: boolean;
  group_ids?: number[];
};

type Sub2Group = {
  id: number;
  name?: string;
};

type Sub2ProxyListItem = {
  id: number;
  name?: string;
  protocol?: string;
  host?: string;
  port?: number;
  username?: string;
  password?: string;
};

export type Sub2DataPayload = {
  exported_at: string;
  proxies: Sub2Proxy[];
  accounts: Sub2Account[];
};

export type OpenAiOAuthSub2Input = {
  email: string;
  accessToken: string;
  refreshToken?: string;
  idToken?: string;
  expiresAt?: string;
  userId?: string;
  accountId?: string;
  planType?: string;
};

export type Sub2PushMode = "sub2_auth" | "oauth_token" | "fallback_oauth_token";

export type Sub2AuthLoginCallback = {
  code: string;
  state: string;
  scope?: string;
};

export type Sub2AuthLoginRequest = {
  authUrl: string;
  sessionId: string;
  email: string;
  account: Sub2Account;
  proxyId: number | null;
};

export type Sub2AuthLoginDriver = (request: Sub2AuthLoginRequest) => Promise<Sub2AuthLoginCallback>;

export class Sub2AuthBranchFallbackError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "Sub2AuthBranchFallbackError";
  }
}

const API_URL_KEY = "sub2.apiUrl";
const API_KEY_KEY = "sub2.apiKey";
const DEFAULT_GROUP_ID_KEY = "sub2.defaultGroupId";
const DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann";
const DEFAULT_CONCURRENCY = 10;
const DEFAULT_PRIORITY = 50;
const DEFAULT_RATE_MULTIPLIER = 1;

function trimString(value?: string | null): string {
  return value?.trim() ?? "";
}

function maskApiKey(apiKey: string): string | null {
  if (!apiKey) return null;
  if (apiKey.length <= 12) return `${apiKey.slice(0, 4)}****`;
  return `${apiKey.slice(0, 8)}...${apiKey.slice(-4)}`;
}

function parseOptionalGroupId(value?: string | null): number | null {
  const trimmed = value?.trim() ?? "";
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

export function getSub2Settings(): Sub2Settings {
  return {
    apiUrl: trimString(getSetting(API_URL_KEY) ?? config.SUB2_API_URL),
    apiKey: trimString(getSetting(API_KEY_KEY) ?? config.SUB2_API_KEY),
    defaultGroupId: parseOptionalGroupId(getSetting(DEFAULT_GROUP_ID_KEY))
  };
}

export function toPublicSub2Settings(settings = getSub2Settings()): Sub2PublicSettings {
  return {
    apiUrl: settings.apiUrl,
    hasApiKey: Boolean(settings.apiKey),
    apiKeyPreview: maskApiKey(settings.apiKey),
    defaultGroupId: settings.defaultGroupId
  };
}

export function saveSub2Settings(input: {
  apiUrl?: string;
  apiKey?: string;
  defaultGroupId?: number | null;
}): Sub2PublicSettings {
  const current = getSub2Settings();
  const apiUrl = input.apiUrl === undefined ? current.apiUrl : trimString(input.apiUrl);
  const apiKey = input.apiKey === undefined ? current.apiKey : trimString(input.apiKey);
  const defaultGroupId = input.defaultGroupId === undefined ? current.defaultGroupId : input.defaultGroupId;
  setSetting(API_URL_KEY, apiUrl);
  setSetting(API_KEY_KEY, apiKey);
  if (defaultGroupId === null) {
    setSetting(DEFAULT_GROUP_ID_KEY, "");
  } else if (Number.isInteger(defaultGroupId) && defaultGroupId > 0) {
    setSetting(DEFAULT_GROUP_ID_KEY, String(defaultGroupId));
  } else {
    throw new Error("Sub2 默认分组 ID 无效");
  }
  return toPublicSub2Settings({ apiUrl, apiKey, defaultGroupId });
}

function readDefaultProxies(): Sub2Proxy[] {
  if (config.SUB2_PROXY_TEMPLATE_JSON?.trim()) {
    const parsed = JSON.parse(config.SUB2_PROXY_TEMPLATE_JSON) as { proxies?: unknown };
    if (Array.isArray(parsed)) return parsed as Sub2Proxy[];
    if (Array.isArray(parsed.proxies)) return parsed.proxies as Sub2Proxy[];
  }
  try {
    const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
    const raw = readFileSync(resolve(root, "temp/toSub2.json"), "utf8");
    const parsed = JSON.parse(raw) as { proxies?: unknown };
    return Array.isArray(parsed.proxies) ? parsed.proxies as Sub2Proxy[] : [];
  } catch {
    return [];
  }
}

function firstProxyKey(proxies: Sub2Proxy[]): string {
  const key = proxies.find((proxy) => typeof proxy.proxy_key === "string" && proxy.proxy_key.trim())?.proxy_key;
  if (!key) {
    throw new Error("Sub2 代理模板缺少 proxy_key，请检查 temp/toSub2.json");
  }
  return key;
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
  const offsetMs = 8 * 60 * 60 * 1000;
  const local = new Date(date.getTime() + offsetMs);
  return `${local.toISOString().slice(0, 19)}+08:00`;
}

export function convertChatGptSessionToSub2(input: unknown): Sub2DataPayload {
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

  const proxies = readDefaultProxies();
  const proxyKey = firstProxyKey(proxies);
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
          client_id: DEFAULT_CLIENT_ID,
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
        concurrency: DEFAULT_CONCURRENCY,
        priority: DEFAULT_PRIORITY,
        rate_multiplier: DEFAULT_RATE_MULTIPLIER,
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

export function convertOpenAiOAuthToSub2(input: OpenAiOAuthSub2Input): Sub2DataPayload {
  const email = input.email.trim().toLowerCase();
  if (!email) throw new Error("OpenAI OAuth 结果缺少 email");
  if (!input.accessToken.trim()) throw new Error("OpenAI OAuth 结果缺少 access_token");

  const proxies = readDefaultProxies();
  const proxyKey = firstProxyKey(proxies);
  const userId = input.userId?.trim() || email;
  const accountId = input.accountId?.trim() || userId;
  return {
    exported_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    proxies,
    accounts: [
      {
        name: email,
        platform: "openai",
        type: "oauth",
        credentials: {
          access_token: input.accessToken.trim(),
          chatgpt_account_id: accountId,
          chatgpt_user_id: userId,
          client_id: DEFAULT_CLIENT_ID,
          email,
          expires_at: input.expiresAt ? normalizeExpiresAt(input.expiresAt) : "",
          plan_type: input.planType?.trim() || "unknown",
          ...(input.refreshToken ? { refresh_token: input.refreshToken } : {}),
          ...(input.idToken ? { id_token: input.idToken } : {})
        },
        extra: {
          email,
          openai_oauth_responses_websockets_v2_enabled: false,
          openai_oauth_responses_websockets_v2_mode: "off",
          privacy_mode: "training_off"
        },
        proxy_key: proxyKey,
        concurrency: DEFAULT_CONCURRENCY,
        priority: DEFAULT_PRIORITY,
        rate_multiplier: DEFAULT_RATE_MULTIPLIER,
        auto_pause_on_expired: true
      }
    ]
  };
}

function normalizeSub2GroupsUrl(apiUrl: string): string {
  const url = new URL(normalizeSub2ImportUrl(apiUrl));
  url.pathname = url.pathname.replace(/\/admin\/accounts\/data$/, "/admin/groups");
  // 账号推送只处理 OpenAI OAuth 账号，因此分组选择也只展示 OpenAI 可用分组。
  url.search = "page=1&page_size=1000&platform=openai&status=active";
  return url.toString();
}

function normalizeSub2AccountsUrl(apiUrl: string): string {
  const url = new URL(normalizeSub2ImportUrl(apiUrl));
  url.pathname = url.pathname.replace(/\/admin\/accounts\/data$/, "/admin/accounts");
  url.search = "";
  return url.toString();
}

function normalizeSub2ProxiesUrl(apiUrl: string): string {
  const url = new URL(normalizeSub2ImportUrl(apiUrl));
  url.pathname = url.pathname.replace(/\/admin\/accounts\/data$/, "/admin/proxies");
  url.search = "";
  return url.toString();
}

function normalizeSub2ProxyListUrl(apiUrl: string, proxy: Sub2Proxy): string {
  const url = normalizeSub2ProxiesUrl(apiUrl);
  const parsed = new URL(url);
  parsed.search = new URLSearchParams({
    page: "1",
    page_size: "20",
    protocol: String(proxy.protocol ?? ""),
    status: "active",
    search: String(proxy.host ?? ""),
    sort_by: "id",
    sort_order: "desc"
  }).toString();
  return parsed.toString();
}

function normalizeSub2ProxyCreateUrl(apiUrl: string): string {
  const url = normalizeSub2ProxiesUrl(apiUrl);
  return url;
}

function normalizeSub2AccountCreateUrl(apiUrl: string): string {
  const url = normalizeSub2AccountsUrl(apiUrl);
  return url.toString();
}

function normalizeSub2OpenAiAuthUrl(apiUrl: string, action: "generate-auth-url" | "create-from-oauth"): string {
  const url = new URL(normalizeSub2ImportUrl(apiUrl));
  url.pathname = url.pathname.replace(/\/admin\/accounts\/data$/, `/admin/openai/${action}`);
  url.search = "";
  return url.toString();
}

function sub2ErrorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    const error = record.error;
    if (error && typeof error === "object" && !Array.isArray(error)) {
      const message = (error as Record<string, unknown>).message;
      if (typeof message === "string" && message) return message;
    }
    for (const key of ["message", "error", "detail", "reason"]) {
      if (typeof record[key] === "string" && record[key]) return String(record[key]);
    }
  }
  return fallback;
}

function parseJsonOrText(text: string): unknown {
  if (!text.trim()) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { message: text };
  }
}

function unwrapSub2Data(body: unknown): unknown {
  if (body && typeof body === "object" && !Array.isArray(body)) {
    const record = body as Record<string, unknown>;
    if ("data" in record) return record.data;
  }
  return body;
}

function assertSub2Success(body: unknown, fallback: string): void {
  if (body && typeof body === "object" && !Array.isArray(body) && "code" in body) {
    const code = Number((body as { code?: unknown }).code);
    if (Number.isFinite(code) && code !== 0) {
      throw new Error(sub2ErrorMessage(body, fallback));
    }
  }
}

async function requestSub2Json(
  url: string,
  settings: Sub2Settings,
  payload: Record<string, unknown>
): Promise<unknown> {
  const response = await fetchWithNetworkOptions(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...sub2AuthHeaders(settings.apiKey)
    },
    body: JSON.stringify(payload)
  }, getSystemNetworkSettings());
  const text = await response.text();
  const body = parseJsonOrText(text);
  if (!response.ok) {
    throw new Error(sub2ErrorMessage(body, response.statusText || `HTTP ${response.status}`));
  }
  assertSub2Success(body, "接口返回失败");
  return body;
}

function extractSub2AuthSession(body: unknown): { authUrl: string; sessionId: string } {
  const data = unwrapSub2Data(body);
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("Sub2API 授权登录响应格式无效");
  }
  const record = data as Record<string, unknown>;
  const authUrl = typeof record.auth_url === "string" ? record.auth_url.trim() : "";
  const sessionId = typeof record.session_id === "string" ? record.session_id.trim() : "";
  if (!authUrl || !sessionId) {
    throw new Error("Sub2API 授权登录响应缺少 auth_url 或 session_id");
  }
  return { authUrl, sessionId };
}

function authUrlState(authUrl: string): string {
  try {
    return new URL(authUrl).searchParams.get("state")?.trim() ?? "";
  } catch {
    return "";
  }
}

export function isSub2AuthBranchFallbackError(error: unknown): boolean {
  return error instanceof Sub2AuthBranchFallbackError ||
    /add[-_ ]?phone|phone[_-]?verification|phone[_-]?number|绑定手机号|手机/i.test(error instanceof Error ? error.message : String(error));
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

function extractSub2Groups(body: unknown): Sub2Group[] {
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

function extractSub2Proxies(body: unknown): Sub2ProxyListItem[] {
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
      name: typeof record.name === "string" ? record.name : undefined,
      protocol: typeof record.protocol === "string" ? record.protocol : undefined,
      host: typeof record.host === "string" ? record.host : undefined,
      port: numberField(record, "port") ?? undefined,
      username: typeof record.username === "string" ? record.username : undefined,
      password: typeof record.password === "string" ? record.password : undefined
    }];
  });
}

function applySub2Group(data: Sub2DataPayload, groupId: number): Sub2DataPayload {
  return {
    ...data,
    accounts: data.accounts.map((account) => ({
      ...account,
      group_ids: [groupId]
    }))
  };
}

function proxyMatches(candidate: Sub2ProxyListItem, proxy: Sub2Proxy): boolean {
  return (candidate.protocol ?? "") === String(proxy.protocol ?? "") &&
    (candidate.host ?? "") === String(proxy.host ?? "") &&
    candidate.port === Number(proxy.port) &&
    (candidate.username ?? "") === String(proxy.username ?? "") &&
    (candidate.password ?? "") === String(proxy.password ?? "");
}

async function resolveSub2ProxyId(
  proxy: Sub2Proxy,
  settings: Sub2Settings
): Promise<number | null> {
  if (!proxy.host || !proxy.protocol || !proxy.port) return null;
  const response = await fetchWithNetworkOptions(normalizeSub2ProxyListUrl(settings.apiUrl, proxy), {
    method: "GET",
    headers: sub2AuthHeaders(settings.apiKey)
  }, getSystemNetworkSettings());
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(`Sub2API 查询代理失败：${sub2ErrorMessage(body, response.statusText || `HTTP ${response.status}`)}`);
  }
  if (body && typeof body === "object" && "code" in body && Number((body as { code?: unknown }).code) !== 0) {
    throw new Error(`Sub2API 查询代理失败：${sub2ErrorMessage(body, "接口返回失败")}`);
  }
  const existing = extractSub2Proxies(body).find((item) => proxyMatches(item, proxy))?.id ?? null;
  if (existing) return existing;

  const create = await fetchWithNetworkOptions(normalizeSub2ProxyCreateUrl(settings.apiUrl), {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...sub2AuthHeaders(settings.apiKey)
    },
    body: JSON.stringify({
      name: String(proxy.name || proxy.host),
      protocol: String(proxy.protocol),
      host: String(proxy.host),
      port: Number(proxy.port),
      username: String(proxy.username ?? ""),
      password: String(proxy.password ?? "")
    })
  }, getSystemNetworkSettings());
  const createText = await create.text();
  const createBody = createText ? JSON.parse(createText) : null;
  if (!create.ok) {
    throw new Error(`Sub2API 创建代理失败：${sub2ErrorMessage(createBody, create.statusText || `HTTP ${create.status}`)}`);
  }
  if (createBody && typeof createBody === "object" && "code" in createBody && Number((createBody as { code?: unknown }).code) !== 0) {
    throw new Error(`Sub2API 创建代理失败：${sub2ErrorMessage(createBody, "接口返回失败")}`);
  }
  const data = createBody && typeof createBody === "object" && "data" in createBody
    ? (createBody as Record<string, unknown>).data
    : createBody;
  return data && typeof data === "object" && !Array.isArray(data)
    ? numberField(data as Record<string, unknown>, "id")
    : null;
}

function proxyByKey(data: Sub2DataPayload): Map<string, Sub2Proxy> {
  const proxies = new Map<string, Sub2Proxy>();
  for (const proxy of data.proxies) {
    if (proxy.proxy_key) proxies.set(proxy.proxy_key, proxy);
  }
  return proxies;
}

async function createSub2Accounts(
  data: Sub2DataPayload,
  groupId: number,
  settings: Sub2Settings
): Promise<unknown[]> {
  const proxies = proxyByKey(data);
  const responses: unknown[] = [];
  for (const account of data.accounts) {
    const proxyId = await resolveSub2ProxyId(proxies.get(account.proxy_key) ?? {}, settings);
    const bodyPayload: Record<string, unknown> = {
      name: account.name,
      platform: account.platform,
      type: account.type,
      credentials: account.credentials,
      extra: account.extra,
      concurrency: account.concurrency,
      priority: account.priority,
      rate_multiplier: account.rate_multiplier,
      auto_pause_on_expired: account.auto_pause_on_expired,
      group_ids: [groupId],
      confirm_mixed_channel_risk: true
    };
    if (proxyId) bodyPayload.proxy_id = proxyId;
    const response = await fetchWithNetworkOptions(normalizeSub2AccountCreateUrl(settings.apiUrl), {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...sub2AuthHeaders(settings.apiKey)
      },
      body: JSON.stringify(bodyPayload)
    }, getSystemNetworkSettings());
    const text = await response.text();
    const body = text ? JSON.parse(text) : null;
    if (!response.ok) {
      throw new Error(`Sub2API 创建账号失败：${sub2ErrorMessage(body, response.statusText || `HTTP ${response.status}`)}`);
    }
    if (body && typeof body === "object" && "code" in body && Number((body as { code?: unknown }).code) !== 0) {
      throw new Error(`Sub2API 创建账号失败：${sub2ErrorMessage(body, "接口返回失败")}`);
    }
    responses.push(body);
  }
  return responses;
}

export async function fetchSub2Groups(): Promise<Sub2Group[]> {
  const settings = getSub2Settings();
  if (!settings.apiKey) throw new Error("请先在系统设置里配置 Sub2API APIKey");
  const response = await fetchWithNetworkOptions(normalizeSub2GroupsUrl(settings.apiUrl), {
    method: "GET",
    headers: sub2AuthHeaders(settings.apiKey)
  }, getSystemNetworkSettings());
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(`Sub2API 获取分组失败：${sub2ErrorMessage(body, response.statusText || `HTTP ${response.status}`)}`);
  }
  if (body && typeof body === "object" && "code" in body && Number((body as { code?: unknown }).code) !== 0) {
    throw new Error(`Sub2API 获取分组失败：${sub2ErrorMessage(body, "接口返回失败")}`);
  }
  return extractSub2Groups(body);
}

function resolvePushGroupId(groupId: number | null | undefined, settings = getSub2Settings()): number {
  const resolved = groupId ?? settings.defaultGroupId;
  if (typeof resolved !== "number" || !Number.isInteger(resolved) || resolved <= 0) {
    throw new Error("请先在系统设置里选择 Sub2 默认推送分组");
  }
  return resolved;
}

export async function pushSub2Account(input: unknown, groupId?: number | null): Promise<{
  data: Sub2DataPayload;
  response: unknown;
}> {
  const settings = getSub2Settings();
  if (!settings.apiKey) throw new Error("请先在系统设置里配置 Sub2API APIKey");
  const resolvedGroupId = resolvePushGroupId(groupId, settings);
  const data = applySub2Group(convertChatGptSessionToSub2(input), resolvedGroupId);
  const response = await createSub2Accounts(data, resolvedGroupId, settings);
  return { data, response };
}

export async function pushSub2Data(data: Sub2DataPayload, groupId?: number | null): Promise<{
  data: Sub2DataPayload;
  response: unknown;
}> {
  const settings = getSub2Settings();
  if (!settings.apiKey) throw new Error("请先在系统设置里配置 Sub2API APIKey");
  const resolvedGroupId = resolvePushGroupId(groupId, settings);
  const grouped = applySub2Group(data, resolvedGroupId);
  const response = await createSub2Accounts(grouped, resolvedGroupId, settings);
  return { data: grouped, response };
}

export async function pushSub2DataViaAuthLogin(
  data: Sub2DataPayload,
  groupId: number | null | undefined,
  authorize: Sub2AuthLoginDriver
): Promise<{
  data: Sub2DataPayload;
  response: unknown;
}> {
  const settings = getSub2Settings();
  if (!settings.apiKey) throw new Error("请先在系统设置里配置 Sub2API APIKey");
  const resolvedGroupId = resolvePushGroupId(groupId, settings);
  const grouped = applySub2Group(data, resolvedGroupId);
  const proxies = proxyByKey(grouped);
  const responses: unknown[] = [];
  for (const account of grouped.accounts) {
    const proxyId = await resolveSub2ProxyId(proxies.get(account.proxy_key) ?? {}, settings);
    const generated = await requestSub2Json(
      normalizeSub2OpenAiAuthUrl(settings.apiUrl, "generate-auth-url"),
      settings,
      proxyId ? { proxy_id: proxyId } : {}
    );
    const authSession = extractSub2AuthSession(generated);
    const callback = await authorize({
      authUrl: authSession.authUrl,
      sessionId: authSession.sessionId,
      email: String(account.credentials.email ?? account.name),
      account,
      proxyId
    });
    const state = callback.state || authUrlState(authSession.authUrl);
    if (!callback.code || !state) {
      throw new Sub2AuthBranchFallbackError("Sub2 授权登录未拿到完整 OAuth callback code/state");
    }
    const created = await requestSub2Json(
      normalizeSub2OpenAiAuthUrl(settings.apiUrl, "create-from-oauth"),
      settings,
      {
        session_id: authSession.sessionId,
        code: callback.code,
        state,
        ...( proxyId ? { proxy_id: proxyId } : {} ),
        name: account.name,
        concurrency: account.concurrency,
        priority: account.priority,
        group_ids: [resolvedGroupId],
        confirm_mixed_channel_risk: true
      }
    );
    responses.push(created);
  }
  return { data: grouped, response: responses };
}
