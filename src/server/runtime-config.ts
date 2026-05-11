import { config } from "./config";
import {
  deleteSettings,
  getConnectionById,
  getDefaultConnection,
  getSetting,
  LEGACY_CONNECTION_ID,
  setSetting,
  upsertConnection,
  type ConnectionRow
} from "./db";

const AUTH_SETTING_KEYS = [
  "claw.apiKey",
  "claw.dashboardCookie",
  "claw.userEmail",
  "claw.workspaceId",
  "claw.workspaceName",
  "claw.parentMailboxId",
  "claw.rootPrefix",
  "claw.domain"
];

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

function fallbackConnection(): ConnectionRow | undefined {
  const existing = getConnectionById(LEGACY_CONNECTION_ID);
  if (existing) return existing;
  const apiKey = getSetting("claw.apiKey") ?? config.CLAW_API_KEY;
  const dashboardCookie = getSetting("claw.dashboardCookie") ?? config.CLAW_DASHBOARD_COOKIE;
  if (!apiKey && !dashboardCookie) return undefined;
  return upsertConnection({
    id: LEGACY_CONNECTION_ID,
    label: getSetting("claw.userEmail") ?? "默认连接",
    userEmail: getSetting("claw.userEmail") ?? null,
    workspaceId: getSetting("claw.workspaceId") ?? config.CLAW_WORKSPACE_ID ?? null,
    workspaceName: getSetting("claw.workspaceName") ?? null,
    parentMailboxId: getSetting("claw.parentMailboxId") ?? config.CLAW_PARENT_MAILBOX_ID ?? null,
    rootPrefix: getSetting("claw.rootPrefix") ?? config.CLAW_ROOT_PREFIX ?? null,
    domain: getSetting("claw.domain") ?? config.CLAW_DOMAIN,
    apiKey,
    dashboardCookie,
    status: "active"
  });
}

export function resolveConnection(connectionId?: string | null): ConnectionRow | undefined {
  if (connectionId) return getConnectionById(connectionId);
  return getDefaultConnection() ?? fallbackConnection();
}

export function requireConnection(connectionId?: string | null): ConnectionRow {
  const connection = resolveConnection(connectionId);
  if (!connection || connection.status === "disconnected") {
    throw new Error("Claw connection is not configured; connect Claw first");
  }
  return connection;
}

export function getClawApiKey(connectionId?: string | null): string | undefined {
  return resolveConnection(connectionId)?.api_key ?? undefined;
}

export function requireClawApiKey(connectionId?: string | null): string {
  const value = getClawApiKey(connectionId);
  if (!value) {
    throw new Error("CLAW_API_KEY is required for mail operations; connect Claw first");
  }
  return value;
}

export function getDashboardCookie(connectionId?: string | null): string | undefined {
  return resolveConnection(connectionId)?.dashboard_cookie ?? undefined;
}

export function requireDashboardCookie(connectionId?: string | null): string {
  const value = getDashboardCookie(connectionId);
  if (!value) {
    throw new Error("CLAW_DASHBOARD_COOKIE is required for mailbox management; connect Claw first");
  }
  return value;
}

export function getWorkspaceId(connectionId?: string | null): string {
  const value = requireConnection(connectionId).workspace_id;
  if (!value) {
    throw new Error("Claw workspace is not configured; connect Claw first");
  }
  return value;
}

export function getParentMailboxId(connectionId?: string | null): string {
  const value = requireConnection(connectionId).parent_mailbox_id;
  if (!value) {
    throw new Error("Claw parent mailbox is not configured; connect Claw first");
  }
  return value;
}

export function getRootPrefix(connectionId?: string | null): string {
  const value = requireConnection(connectionId).root_prefix;
  if (!value) {
    throw new Error("Claw root prefix is not configured; connect Claw first");
  }
  return value;
}

export function getDomain(connectionId?: string | null): string {
  return requireConnection(connectionId).domain;
}

export function hasClawMailConfig(connectionId?: string | null): boolean {
  return Boolean(getClawApiKey(connectionId));
}

export function hasClawDashboardConfig(connectionId?: string | null): boolean {
  return Boolean(getDashboardCookie(connectionId));
}

export function connectionToAuthStatus(connection?: ConnectionRow | null): ClawAuthStatus {
  const apiKey = connection?.api_key ?? null;
  const cookie = connection?.dashboard_cookie ?? null;
  const workspaceId = cookie ? connection?.workspace_id ?? null : null;
  const parentMailboxId = cookie ? connection?.parent_mailbox_id ?? null : null;
  const rootPrefix = cookie ? connection?.root_prefix ?? null : null;
  const domain = cookie ? connection?.domain ?? null : null;
  return {
    id: connection?.id ?? null,
    connected: Boolean(apiKey && cookie && workspaceId && parentMailboxId && rootPrefix && domain && connection?.status !== "disconnected"),
    hasApiKey: Boolean(apiKey),
    hasDashboardCookie: Boolean(cookie),
    userEmail: connection?.user_email ?? null,
    workspaceId,
    workspaceName: connection?.workspace_name ?? null,
    parentMailboxId,
    rootPrefix,
    domain,
    apiKeyPrefix: apiKey ? apiKey.slice(0, 10) : null,
    apiKeySuffix: apiKey ? apiKey.slice(-4) : null,
    status: connection?.status ?? null,
    label: connection?.label ?? null
  };
}

export function getClawAuthStatus(connectionId?: string | null): ClawAuthStatus {
  return connectionToAuthStatus(resolveConnection(connectionId));
}

export function saveClawAuthSettings(input: {
  connectionId?: string | null;
  apiKey: string;
  dashboardCookie: string;
  userEmail?: string | null;
  workspaceId: string;
  workspaceName?: string | null;
  parentMailboxId: string;
  rootPrefix: string;
  domain: string;
}): ConnectionRow {
  const connection = upsertConnection({
    id: input.connectionId ?? LEGACY_CONNECTION_ID,
    label: input.userEmail ?? input.workspaceName ?? input.connectionId ?? "默认连接",
    userEmail: input.userEmail ?? null,
    workspaceId: input.workspaceId,
    workspaceName: input.workspaceName ?? null,
    parentMailboxId: input.parentMailboxId,
    rootPrefix: input.rootPrefix,
    domain: input.domain,
    apiKey: input.apiKey,
    dashboardCookie: input.dashboardCookie,
    status: "active",
    lastSyncedAt: new Date().toISOString()
  });

  if (connection.id === LEGACY_CONNECTION_ID) {
    setSetting("claw.apiKey", input.apiKey);
    setSetting("claw.dashboardCookie", input.dashboardCookie);
    setSetting("claw.workspaceId", input.workspaceId);
    setSetting("claw.parentMailboxId", input.parentMailboxId);
    setSetting("claw.rootPrefix", input.rootPrefix);
    setSetting("claw.domain", input.domain);
    if (input.userEmail) setSetting("claw.userEmail", input.userEmail);
    if (input.workspaceName) setSetting("claw.workspaceName", input.workspaceName);
  }

  return connection;
}

export function clearClawAuthSettings(): void {
  deleteSettings(AUTH_SETTING_KEYS);
}
