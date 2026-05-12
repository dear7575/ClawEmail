import { config } from "./config";
import { getSetting, setSetting } from "./db";

export type SystemNetworkSettings = {
  proxyUrl: string;
  timeoutMs: number;
  openAiOtpTimeoutMs: number;
};

const PROXY_URL_KEY = "system.proxyUrl";
const TIMEOUT_MS_KEY = "system.timeoutMs";
const OPENAI_OTP_TIMEOUT_MS_KEY = "openai.otpTimeoutMs";
const LEGACY_DUCK_PROXY_URL_KEY = "duck.proxyUrl";
const LEGACY_DUCK_TIMEOUT_MS_KEY = "duck.timeoutMs";
const DEFAULT_TIMEOUT_MS = 10000;
const DEFAULT_OPENAI_OTP_TIMEOUT_MS = 60000;
const MIN_TIMEOUT_MS = 1000;
const MAX_TIMEOUT_MS = 120000;
const MIN_OPENAI_OTP_TIMEOUT_MS = 15000;
const MAX_OPENAI_OTP_TIMEOUT_MS = 300000;

function normalizeTimeoutMs(value?: number | string | null): number {
  const parsed = Number(
    value
    ?? config.SYSTEM_REQUEST_TIMEOUT_MS
    ?? config.DUCK_REQUEST_TIMEOUT_MS
    ?? DEFAULT_TIMEOUT_MS
  );
  if (!Number.isFinite(parsed)) return DEFAULT_TIMEOUT_MS;
  return Math.min(MAX_TIMEOUT_MS, Math.max(MIN_TIMEOUT_MS, Math.round(parsed)));
}

function normalizeOpenAiOtpTimeoutMs(value?: number | string | null): number {
  const parsed = Number(value ?? DEFAULT_OPENAI_OTP_TIMEOUT_MS);
  if (!Number.isFinite(parsed)) return DEFAULT_OPENAI_OTP_TIMEOUT_MS;
  return Math.min(MAX_OPENAI_OTP_TIMEOUT_MS, Math.max(MIN_OPENAI_OTP_TIMEOUT_MS, Math.round(parsed)));
}

function normalizeProxyUrl(value?: string | null): string {
  const trimmed = value?.trim() ?? "";
  if (!trimmed) return "";
  const url = new URL(trimmed);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("系统代理地址只支持 http:// 或 https://");
  }
  if (!url.hostname) {
    throw new Error("系统代理地址缺少主机名");
  }
  return url.toString();
}

export function getSystemNetworkSettings(): SystemNetworkSettings {
  const storedProxyUrl = getSetting(PROXY_URL_KEY) ?? getSetting(LEGACY_DUCK_PROXY_URL_KEY);
  const storedTimeoutMs = getSetting(TIMEOUT_MS_KEY) ?? getSetting(LEGACY_DUCK_TIMEOUT_MS_KEY);
  const storedOpenAiOtpTimeoutMs = getSetting(OPENAI_OTP_TIMEOUT_MS_KEY);
  return {
    proxyUrl: normalizeProxyUrl(storedProxyUrl ?? config.SYSTEM_PROXY_URL ?? config.DUCK_PROXY_URL ?? ""),
    timeoutMs: normalizeTimeoutMs(storedTimeoutMs),
    openAiOtpTimeoutMs: normalizeOpenAiOtpTimeoutMs(storedOpenAiOtpTimeoutMs)
  };
}

export function saveSystemNetworkSettings(input: Partial<SystemNetworkSettings>): SystemNetworkSettings {
  const current = getSystemNetworkSettings();
  const settings = {
    proxyUrl: normalizeProxyUrl(input.proxyUrl ?? ""),
    timeoutMs: normalizeTimeoutMs(input.timeoutMs),
    openAiOtpTimeoutMs: normalizeOpenAiOtpTimeoutMs(input.openAiOtpTimeoutMs ?? current.openAiOtpTimeoutMs)
  };
  setSetting(PROXY_URL_KEY, settings.proxyUrl);
  setSetting(TIMEOUT_MS_KEY, String(settings.timeoutMs));
  setSetting(OPENAI_OTP_TIMEOUT_MS_KEY, String(settings.openAiOtpTimeoutMs));
  return settings;
}
