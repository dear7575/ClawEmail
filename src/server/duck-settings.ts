import { config } from "./config";
import { getSetting, setSetting } from "./db";

export type DuckNetworkSettings = {
  proxyUrl: string;
  timeoutMs: number;
};

const PROXY_URL_KEY = "duck.proxyUrl";
const TIMEOUT_MS_KEY = "duck.timeoutMs";
const DEFAULT_TIMEOUT_MS = 10000;
const MIN_TIMEOUT_MS = 1000;
const MAX_TIMEOUT_MS = 120000;

function normalizeTimeoutMs(value?: number | string | null): number {
  const parsed = Number(value ?? config.DUCK_REQUEST_TIMEOUT_MS ?? DEFAULT_TIMEOUT_MS);
  if (!Number.isFinite(parsed)) return DEFAULT_TIMEOUT_MS;
  return Math.min(MAX_TIMEOUT_MS, Math.max(MIN_TIMEOUT_MS, Math.round(parsed)));
}

function normalizeProxyUrl(value?: string | null): string {
  const trimmed = value?.trim() ?? "";
  if (!trimmed) return "";
  const url = new URL(trimmed);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("Duck 代理地址只支持 http:// 或 https://");
  }
  if (!url.hostname) {
    throw new Error("Duck 代理地址缺少主机名");
  }
  return url.toString();
}

export function getDuckNetworkSettings(): DuckNetworkSettings {
  const storedProxyUrl = getSetting(PROXY_URL_KEY);
  const storedTimeoutMs = getSetting(TIMEOUT_MS_KEY);
  return {
    proxyUrl: normalizeProxyUrl(storedProxyUrl ?? config.DUCK_PROXY_URL ?? ""),
    timeoutMs: normalizeTimeoutMs(storedTimeoutMs)
  };
}

export function saveDuckNetworkSettings(input: Partial<DuckNetworkSettings>): DuckNetworkSettings {
  const settings = {
    proxyUrl: normalizeProxyUrl(input.proxyUrl ?? ""),
    timeoutMs: normalizeTimeoutMs(input.timeoutMs)
  };
  setSetting(PROXY_URL_KEY, settings.proxyUrl);
  setSetting(TIMEOUT_MS_KEY, String(settings.timeoutMs));
  return settings;
}
