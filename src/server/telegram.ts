import { config } from "./config";
import { getSetting, setSetting } from "./db";
import { fetchWithNetworkOptions } from "./network-fetch";
import { getSystemNetworkSettings } from "./network-settings";

export type TelegramSettings = {
  enabled: boolean;
  chatId: string;
  botToken: string;
};

export type TelegramPublicSettings = {
  enabled: boolean;
  chatId: string;
  hasBotToken: boolean;
  botTokenPreview: string | null;
};

const ENABLED_KEY = "telegram.enabled";
const BOT_TOKEN_KEY = "telegram.botToken";
const CHAT_ID_KEY = "telegram.chatId";
const TELEGRAM_API_ORIGIN = "https://api.telegram.org";

function normalizeBotToken(value?: string | null): string {
  return value?.trim() ?? "";
}

function normalizeChatId(value?: string | null): string {
  return value?.trim() ?? "";
}

function maskBotToken(token: string): string | null {
  if (!token) return null;
  if (token.length <= 12) return `${token.slice(0, 4)}****`;
  return `${token.slice(0, 8)}...${token.slice(-4)}`;
}

export function getTelegramSettings(): TelegramSettings {
  const botToken = normalizeBotToken(getSetting(BOT_TOKEN_KEY) ?? config.TELEGRAM_BOT_TOKEN);
  const chatId = normalizeChatId(getSetting(CHAT_ID_KEY) ?? config.TELEGRAM_CHAT_ID);
  return {
    enabled: (getSetting(ENABLED_KEY) ?? "false") === "true",
    botToken,
    chatId
  };
}

export function toPublicTelegramSettings(settings = getTelegramSettings()): TelegramPublicSettings {
  return {
    enabled: settings.enabled,
    chatId: settings.chatId,
    hasBotToken: Boolean(settings.botToken),
    botTokenPreview: maskBotToken(settings.botToken)
  };
}

export function saveTelegramSettings(input: {
  enabled?: boolean;
  botToken?: string;
  chatId?: string;
}): TelegramPublicSettings {
  const current = getTelegramSettings();
  const botToken = input.botToken === undefined
    ? current.botToken
    : normalizeBotToken(input.botToken);
  const chatId = input.chatId === undefined
    ? current.chatId
    : normalizeChatId(input.chatId);
  const enabled = input.enabled ?? current.enabled;

  setSetting(ENABLED_KEY, String(enabled));
  setSetting(BOT_TOKEN_KEY, botToken);
  setSetting(CHAT_ID_KEY, chatId);
  return toPublicTelegramSettings({ enabled, botToken, chatId });
}

export async function sendTelegramMessage(text: string): Promise<void> {
  const settings = getTelegramSettings();
  if (!settings.enabled) {
    throw new Error("Telegram 消息通知未启用");
  }
  if (!settings.botToken || !settings.chatId) {
    throw new Error("请先在系统设置里配置 Telegram Bot Token 和 Chat ID");
  }

  const networkSettings = getSystemNetworkSettings();
  const response = await fetchWithNetworkOptions(`${TELEGRAM_API_ORIGIN}/bot${settings.botToken}/sendMessage`, {
    method: "POST",
    headers: {
      "content-type": "application/json"
    },
    body: JSON.stringify({
      chat_id: settings.chatId,
      text
    })
  }, networkSettings);
  const body = await response.json().catch(() => null) as { description?: string } | null;
  if (!response.ok) {
    throw new Error(`Telegram 发送失败：${body?.description ?? response.statusText}`);
  }
}
