import type { FastifyInstance } from "fastify";
import { z } from "zod";
import type { MailDetail } from "@clawemail/node-sdk";
import { deleteRemoteMail, getMailClient, listRemoteInboxMessageIds, readRemoteMail } from "../claw-mail";
import {
  deleteMailById,
  deleteMailsByProviderIds,
  getMailById,
  getMailByProviderId,
  listActiveMailboxes,
  listAttachments,
  listMailProviderIds,
  listMails,
  saveMail
} from "../db";

const listQuerySchema = z.object({
  connectionId: z.string().min(1).optional(),
  mailbox: z.string().optional(),
  sync: z.enum(["true", "false"]).optional(),
  limit: z.coerce.number().int().min(1).max(100).default(50),
  offset: z.coerce.number().int().min(0).default(0)
});

function attachmentList(mail: MailDetail) {
  return (mail.attachments ?? []).map((attachment) => ({
    providerPartId: attachment.id,
    filename: attachment.filename ?? null,
    contentType: attachment.contentType ?? null,
    size: attachment.size ?? null
  }));
}

async function syncMailboxInbox(connectionId: string | undefined, mailboxEmail: string): Promise<void> {
  const remoteIds = await listRemoteInboxMessageIds(mailboxEmail, 500, connectionId);
  const remoteIdSet = new Set(remoteIds);
  const localIds = listMailProviderIds(mailboxEmail, connectionId);
  const staleLocalIds = localIds.filter((id) => !remoteIdSet.has(id));
  deleteMailsByProviderIds(mailboxEmail, staleLocalIds, connectionId);

  for (const providerMailId of remoteIds) {
    if (getMailByProviderId(mailboxEmail, providerMailId, connectionId)) continue;
    const mail = await readRemoteMail(mailboxEmail, providerMailId, connectionId);
    saveMail({
      connectionId,
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

async function syncAllMailboxInboxes(connectionId?: string): Promise<void> {
  for (const mailbox of listActiveMailboxes(connectionId)) {
    await syncMailboxInbox(mailbox.connection_id ?? connectionId, mailbox.email);
  }
}

export async function mailRoutes(app: FastifyInstance): Promise<void> {
  app.get("/api/mails", async (request) => {
    const query = listQuerySchema.parse(request.query);
    const mailboxEmail = query.mailbox?.trim().toLowerCase();
    if (query.sync === "true" && mailboxEmail) {
      await syncMailboxInbox(query.connectionId, mailboxEmail);
    } else if (query.sync === "true") {
      await syncAllMailboxInboxes(query.connectionId);
    }
    return listMails({
      connectionId: query.connectionId,
      mailboxEmail,
      limit: query.limit,
      offset: query.offset
    });
  });

  app.get("/api/mails/:id", async (request, reply) => {
    const { id } = request.params as { id: string };
    const mail = getMailById(Number(id));
    if (!mail) {
      return reply.code(404).send({ error: "mail not found" });
    }
    return {
      ...mail,
      parsed: JSON.parse(mail.raw_json),
      attachments: listAttachments(mail.id)
    };
  });

  app.get("/api/mails/:id/attachments/:partId", async (request, reply) => {
    const { id, partId } = request.params as { id: string; partId: string };
    const mail = getMailById(Number(id));
    if (!mail) {
      return reply.code(404).send({ error: "mail not found" });
    }
    const attachment = await getMailClient(mail.mailbox_email, mail.connection_id).mail.getAttachment({
      id: mail.provider_mail_id,
      part: partId
    });
    reply.header("content-type", attachment.contentType || "application/octet-stream");
    reply.header("content-disposition", `attachment; filename="${encodeURIComponent(attachment.filename)}"`);
    return reply.send(attachment.stream());
  });

  app.delete("/api/mails/:id", async (request) => {
    const { id } = request.params as { id: string };
    const mail = getMailById(Number(id));
    if (!mail) {
      return { success: true };
    }
    await deleteRemoteMail(mail.mailbox_email, mail.provider_mail_id, mail.connection_id);
    deleteMailById(Number(id));
    return { success: true };
  });
}
