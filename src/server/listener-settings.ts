import { getSetting, setSetting } from "./db";

export type ListenerLogMode = "quiet" | "lifecycle" | "verbose";
export type ListenerReconnectMode = "standard" | "slow";

export type ListenerSettings = {
  logMode: ListenerLogMode;
  reconnectMode: ListenerReconnectMode;
};

const LOG_MODE_KEY = "listener.logMode";
const RECONNECT_MODE_KEY = "listener.reconnectMode";

const DEFAULT_SETTINGS: ListenerSettings = {
  logMode: "quiet",
  reconnectMode: "standard"
};

export function normalizeListenerSettings(input: Partial<ListenerSettings> = {}): ListenerSettings {
  return {
    logMode: input.logMode === "lifecycle" || input.logMode === "verbose" ? input.logMode : DEFAULT_SETTINGS.logMode,
    reconnectMode: input.reconnectMode === "slow" ? "slow" : DEFAULT_SETTINGS.reconnectMode
  };
}

export function getListenerSettings(): ListenerSettings {
  return normalizeListenerSettings({
    logMode: getSetting(LOG_MODE_KEY) as ListenerLogMode | undefined,
    reconnectMode: getSetting(RECONNECT_MODE_KEY) as ListenerReconnectMode | undefined
  });
}

export function saveListenerSettings(input: Partial<ListenerSettings>): ListenerSettings {
  const settings = normalizeListenerSettings(input);
  setSetting(LOG_MODE_KEY, settings.logMode);
  setSetting(RECONNECT_MODE_KEY, settings.reconnectMode);
  return settings;
}
