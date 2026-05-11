import type { FastifyInstance } from "fastify";
import { z } from "zod";
import {
  getAuthMe,
  listApiKeys,
  listDashboardMailboxes,
  listWorkspaces,
  sendLoginCode,
  verifyLoginCode,
  type ClawMailbox
} from "../claw-dashboard";
import { resetMailClients } from "../claw-mail";
import {
  getConnectionById,
  LEGACY_CONNECTION_ID,
  listConnections,
  markConnectionDisconnected,
  markMailboxesMissingDeleted,
  upsertMailbox
} from "../db";
import {
  startAllMailboxListeners,
  startConnectionMailboxListeners,
  stopAllMailboxListeners,
  stopConnectionMailboxListeners
} from "../listener-manager";
import {
  clearClawAuthSettings,
  connectionToAuthStatus,
  getClawAuthStatus,
  requireDashboardCookie,
  saveClawAuthSettings
} from "../runtime-config";

const clawLoginEmailSchema = z.string()
  .transform((value) => {
    const normalized = value.trim().replace(/＠/g, "@").toLowerCase();
    return normalized.includes("@") ? normalized : `${normalized}@163.com`;
  })
  .pipe(z.string().regex(/^[^\s@]+@163\.com$/, "请输入完整 163 登录邮箱"));

const sendCodeSchema = z.object({
  email: clawLoginEmailSchema
});

const verifyCodeSchema = z.object({
  email: clawLoginEmailSchema,
  code: z.string().trim().regex(/^\d+$/),
  connectionId: z.string().min(1).optional()
});

const connectionParamsSchema = z.object({
  id: z.string().min(1)
});

const pendingLoginCookies = new Map<string, string>();

function emailDomain(email: string): string {
  return email.split("@")[1] || "claw.163.com";
}

function mailboxRootPrefix(mailbox: ClawMailbox): string {
  if (mailbox.prefix) {
    return mailbox.prefix.split("@")[0].split(".")[0];
  }
  return mailbox.email.split("@")[0].split(".")[0];
}

function connectionIdFromIdentity(input: {
  userEmail?: string | null;
  workspaceId: string;
}): string {
  const base = `${input.userEmail ?? "claw"}:${input.workspaceId}`;
  return base
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._:-]+/g, "-")
    .slice(0, 96);
}

function saveMailboxes(connectionId: string, mailboxes: ClawMailbox[]): void {
  for (const item of mailboxes) {
    upsertMailbox({
      id: item.id,
      connectionId,
      providerMailboxId: item.id,
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
  markMailboxesMissingDeleted(mailboxes.map((item) => item.email), connectionId);
}

async function connectWithCookie(cookie: string, preferredConnectionId?: string) {
  const [user, workspaces, apiKeys] = await Promise.all([
    getAuthMe(cookie),
    listWorkspaces(cookie),
    listApiKeys(cookie)
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

  const mailboxes = await listDashboardMailboxes({
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
  const connectionId = preferredConnectionId ?? connectionIdFromIdentity({
    userEmail,
    workspaceId: workspace.id
  });

  stopConnectionMailboxListeners(connectionId);
  resetMailClients(connectionId);
  const connection = saveClawAuthSettings({
    connectionId,
    apiKey: apiKey.apiKey,
    dashboardCookie: cookie,
    userEmail,
    workspaceId: workspace.id,
    workspaceName: workspace.name,
    parentMailboxId: primaryMailbox.id,
    rootPrefix: mailboxRootPrefix(primaryMailbox),
    domain: emailDomain(primaryMailbox.email)
  });
  saveMailboxes(connection.id, mailboxes);
  startConnectionMailboxListeners(connection.id);

  return {
    connection: connectionToAuthStatus(connection),
    auth: connectionToAuthStatus(connection),
    syncedMailboxes: mailboxes.length
  };
}

export async function clawAuthRoutes(app: FastifyInstance): Promise<void> {
  app.get("/api/connections", async () => {
    return {
      items: listConnections(true).map(connectionToAuthStatus)
    };
  });

  app.get("/api/connections/:id", async (request, reply) => {
    const params = connectionParamsSchema.parse(request.params);
    const connection = getConnectionById(params.id);
    if (!connection) {
      return reply.code(404).send({ error: "connection not found" });
    }
    return connectionToAuthStatus(connection);
  });

  app.post("/api/connections/send-code", async (request) => {
    const body = sendCodeSchema.parse(request.body);
    const pendingCookie = await sendLoginCode(body.email);
    if (pendingCookie) {
      pendingLoginCookies.set(body.email, pendingCookie);
    }
    return { success: true };
  });

  app.post("/api/connections/verify-code", async (request) => {
    const body = verifyCodeSchema.parse(request.body);
    const cookie = await verifyLoginCode(body.email, body.code, pendingLoginCookies.get(body.email));
    pendingLoginCookies.delete(body.email);
    return await connectWithCookie(cookie, body.connectionId);
  });

  app.post("/api/connections/:id/refresh", async (request, reply) => {
    const params = connectionParamsSchema.parse(request.params);
    const connection = getConnectionById(params.id);
    if (!connection) {
      return reply.code(404).send({ error: "connection not found" });
    }
    return await connectWithCookie(requireDashboardCookie(params.id), params.id);
  });

  app.post("/api/connections/:id/logout", async (request, reply) => {
    const params = connectionParamsSchema.parse(request.params);
    const connection = getConnectionById(params.id);
    if (!connection) {
      return reply.code(404).send({ error: "connection not found" });
    }
    stopConnectionMailboxListeners(params.id);
    resetMailClients(params.id);
    markConnectionDisconnected(params.id);
    return connectionToAuthStatus(getConnectionById(params.id));
  });

  app.get("/api/auth/claw/status", async () => {
    return getClawAuthStatus();
  });

  app.post("/api/auth/claw/send-code", async (request) => {
    const body = sendCodeSchema.parse(request.body);
    const pendingCookie = await sendLoginCode(body.email);
    if (pendingCookie) {
      pendingLoginCookies.set(body.email, pendingCookie);
    }
    return { success: true };
  });

  app.post("/api/auth/claw/verify-code", async (request) => {
    const body = verifyCodeSchema.parse(request.body);
    const cookie = await verifyLoginCode(body.email, body.code, pendingLoginCookies.get(body.email));
    pendingLoginCookies.delete(body.email);
    return await connectWithCookie(cookie, LEGACY_CONNECTION_ID);
  });

  app.post("/api/auth/claw/refresh", async () => {
    return await connectWithCookie(requireDashboardCookie(), LEGACY_CONNECTION_ID);
  });

  app.post("/api/auth/claw/logout", async () => {
    stopAllMailboxListeners();
    resetMailClients();
    clearClawAuthSettings();
    markConnectionDisconnected(LEGACY_CONNECTION_ID);
    startAllMailboxListeners();
    return getClawAuthStatus();
  });
}
