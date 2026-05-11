import type { FastifyInstance } from "fastify";
import { z } from "zod";
import {
  createMailbox,
  deleteMailbox,
  listDashboardMailboxes,
  updateMailboxCommunicationSettings
} from "../claw-dashboard";
import {
  getMailboxById,
  listMailboxes,
  markMailboxDeleted,
  markMailboxesMissingDeleted,
  updateMailboxCommSettings,
  upsertMailbox
} from "../db";
import { startMailboxListener, stopMailboxListener } from "../listener-manager";
import { getParentMailboxId, requireConnection } from "../runtime-config";

const listQuerySchema = z.object({
  connectionId: z.string().min(1).optional(),
  sync: z.enum(["true", "false"]).optional()
});

const createMailboxSchema = z.object({
  connectionId: z.string().min(1).optional(),
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

function upsertRemoteMailbox(connectionId: string, item: {
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
}) {
  return upsertMailbox({
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

export async function mailboxRoutes(app: FastifyInstance): Promise<void> {
  app.get("/api/mailboxes", async (request) => {
    const query = listQuerySchema.parse(request.query);
    const connection = query.connectionId ? requireConnection(query.connectionId) : undefined;
    if (query.sync === "true") {
      const syncConnection = requireConnection(query.connectionId);
      const remote = await listDashboardMailboxes({
        connectionId: syncConnection.id
      });
      for (const item of remote) {
        const row = upsertRemoteMailbox(syncConnection.id, item);
        startMailboxListener(row);
      }
      for (const mailbox of markMailboxesMissingDeleted(remote.map((item) => item.email), syncConnection.id)) {
        stopMailboxListener(mailbox.email, syncConnection.id);
      }
    }
    return { items: listMailboxes({ connectionId: connection?.id }) };
  });

  app.post("/api/mailboxes", async (request, reply) => {
    const body = createMailboxSchema.parse(request.body);
    const connection = requireConnection(body.connectionId);
    const mailbox = await createMailbox(body.suffix, connection.id);
    await updateMailboxCommunicationSettings(mailbox.id, DEFAULT_COMM_SETTINGS, connection.id);
    const row = upsertRemoteMailbox(connection.id, {
      ...mailbox,
      commLevel: DEFAULT_COMM_SETTINGS.commLevel,
      extReceiveType: DEFAULT_COMM_SETTINGS.extReceiveType,
      extSendType: DEFAULT_COMM_SETTINGS.extSendType
    });
    startMailboxListener(row);
    return reply.code(201).send(row);
  });

  app.post("/api/mailboxes/:id/comm-settings", async (request, reply) => {
    const { id } = request.params as { id: string };
    const mailbox = getMailboxById(id);
    if (!mailbox) {
      return reply.code(404).send({ error: "mailbox not found" });
    }

    const connectionId = mailbox.connection_id ?? undefined;
    const body = commSettingsSchema.parse(request.body);
    const dashboardPayload = body.commLevel === 2
      ? {
          commLevel: body.commLevel,
          extReceiveType: body.extReceiveType!,
          extSendType: body.extSendType!
        }
      : { commLevel: body.commLevel };

    await updateMailboxCommunicationSettings(
      mailbox.provider_mailbox_id ?? id,
      dashboardPayload,
      connectionId
    );
    const updated = updateMailboxCommSettings(id, {
      commLevel: body.commLevel,
      extReceiveType: body.commLevel === 2 ? body.extReceiveType : null,
      extSendType: body.commLevel === 2 ? body.extSendType : null
    });
    return updated ?? getMailboxById(id);
  });

  app.delete("/api/mailboxes/:id", async (request, reply) => {
    const { id } = request.params as { id: string };
    const mailbox = getMailboxById(id);
    if (!mailbox) {
      return { success: true };
    }
    const connectionId = mailbox.connection_id ?? undefined;
    if ((mailbox.provider_mailbox_id ?? id) === getParentMailboxId(connectionId)) {
      return reply.code(400).send({ error: "primary mailbox cannot be deleted here" });
    }
    await deleteMailbox(mailbox.provider_mailbox_id ?? id, connectionId);
    markMailboxDeleted(id);
    stopMailboxListener(mailbox.email, connectionId);
    return { success: true };
  });
}
