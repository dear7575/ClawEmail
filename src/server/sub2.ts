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
};

export type Sub2PublicSettings = {
  apiUrl: string;
  hasApiKey: boolean;
  apiKeyPreview: string | null;
};

export type Sub2Proxy = Record<string, unknown> & {
  proxy_key?: string;
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

export type Sub2DataPayload = {
  exported_at: string;
  proxies: Sub2Proxy[];
  accounts: Sub2Account[];
};

const API_URL_KEY = "sub2.apiUrl";
const API_KEY_KEY = "sub2.apiKey";
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

export function getSub2Settings(): Sub2Settings {
  return {
    apiUrl: trimString(getSetting(API_URL_KEY) ?? config.SUB2_API_URL),
    apiKey: trimString(getSetting(API_KEY_KEY) ?? config.SUB2_API_KEY)
  };
}

export function toPublicSub2Settings(settings = getSub2Settings()): Sub2PublicSettings {
  return {
    apiUrl: settings.apiUrl,
    hasApiKey: Boolean(settings.apiKey),
    apiKeyPreview: maskApiKey(settings.apiKey)
  };
}

export function saveSub2Settings(input: {
  apiUrl?: string;
  apiKey?: string;
}): Sub2PublicSettings {
  const current = getSub2Settings();
  const apiUrl = input.apiUrl === undefined ? current.apiUrl : trimString(input.apiUrl);
  const apiKey = input.apiKey === undefined ? current.apiKey : trimString(input.apiKey);
  setSetting(API_URL_KEY, apiUrl);
  setSetting(API_KEY_KEY, apiKey);
  return toPublicSub2Settings({ apiUrl, apiKey });
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

function applySub2Group(data: Sub2DataPayload, groupId: number): Sub2DataPayload {
  return {
    ...data,
    accounts: data.accounts.map((account) => ({
      ...account,
      group_ids: [groupId]
    }))
  };
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

export async function pushSub2Account(input: unknown, groupId: number): Promise<{
  data: Sub2DataPayload;
  response: unknown;
}> {
  const settings = getSub2Settings();
  if (!settings.apiKey) throw new Error("请先在系统设置里配置 Sub2API APIKey");
  if (!Number.isInteger(groupId) || groupId <= 0) throw new Error("请选择要推送到的 Sub2 分组");
  const data = applySub2Group(convertChatGptSessionToSub2(input), groupId);
  const response = await fetchWithNetworkOptions(normalizeSub2ImportUrl(settings.apiUrl), {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...sub2AuthHeaders(settings.apiKey)
    },
    body: JSON.stringify({
      data,
      skip_default_group_bind: true
    })
  }, getSystemNetworkSettings());
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(`Sub2API 推送失败：${sub2ErrorMessage(body, response.statusText || `HTTP ${response.status}`)}`);
  }
  if (body && typeof body === "object" && "code" in body && Number((body as { code?: unknown }).code) !== 0) {
    throw new Error(`Sub2API 推送失败：${sub2ErrorMessage(body, "接口返回失败")}`);
  }
  return { data, response: body };
}
