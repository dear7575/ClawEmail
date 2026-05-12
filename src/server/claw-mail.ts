import { MailClient, MailSdkError, type MailDetail } from "@clawemail/node-sdk";
import { requireClawApiKey } from "./runtime-config";

export type SendMailInput = {
  connectionId?: string;
  from: string;
  to: string[];
  subject?: string;
  body?: string;
  html?: boolean;
  cc?: string[];
  bcc?: string[];
};

export type ReplyMailInput = {
  connectionId?: string;
  mailboxEmail: string;
  providerMailId: string;
  body?: string;
  html?: boolean;
  toAll?: boolean;
};

type RemoteMessageSummary = {
  id: string;
};

type InternalMailTransport = {
  listMessages?: (input: {
    fid: string | number;
    start?: number;
    limit?: number;
    order?: string;
    desc?: boolean;
  }) => Promise<RemoteMessageSummary[]>;
  moveMessages?: (ids: string[], target: string | number) => Promise<unknown>;
};

const clients = new Map<string, MailClient>();

function clientKey(email: string, connectionId?: string | null): string {
  return `${connectionId ?? "default"}:${email.trim().toLowerCase()}`;
}

export function getMailClient(email: string, connectionId?: string | null): MailClient {
  const normalized = email.trim().toLowerCase();
  const key = clientKey(normalized, connectionId);
  const existing = clients.get(key);
  if (existing) return existing;

  const client = new MailClient({
    apiKey: requireClawApiKey(connectionId),
    user: normalized,
    logger: null
  });
  clients.set(key, client);
  return client;
}

export function resetMailClients(connectionId?: string | null): void {
  const prefix = connectionId ? `${connectionId}:` : null;
  for (const [key, client] of clients) {
    if (prefix && !key.startsWith(prefix)) continue;
    try {
      client.ws.disconnect();
    } catch {
      // ignore disconnect errors
    }
    clients.delete(key);
  }
  if (!prefix) clients.clear();
}

export async function sendMail(input: SendMailInput): Promise<{ status: "sent" }> {
  if (!input.to.length) {
    throw new Error("to must not be empty");
  }
  const client = getMailClient(input.from, input.connectionId);
  return await client.mail.send({
    to: input.to,
    subject: input.subject,
    body: input.body,
    html: input.html,
    cc: input.cc,
    bcc: input.bcc
  });
}

export async function replyMail(input: ReplyMailInput): Promise<{ status: "sent" }> {
  const client = getMailClient(input.mailboxEmail, input.connectionId);
  return await client.mail.reply({
    id: input.providerMailId,
    body: input.body,
    html: input.html,
    toAll: input.toAll
  });
}

export async function deleteRemoteMail(mailboxEmail: string, providerMailId: string, connectionId?: string | null): Promise<void> {
  const client = getMailClient(mailboxEmail, connectionId);
  const transport = getInternalTransport(client);

  if (!transport?.moveMessages) {
    throw new Error("Remote mail deletion is not supported by the installed Claw SDK");
  }

  await transport.moveMessages([providerMailId], "Trash");
}

export async function listRemoteInboxMessageIds(mailboxEmail: string, maxMessages = 500, connectionId?: string | null): Promise<string[]> {
  const client = getMailClient(mailboxEmail, connectionId);
  const transport = getInternalTransport(client);
  if (!transport?.listMessages) {
    throw new Error("Remote mailbox sync is not supported by the installed Claw SDK");
  }

  const ids: string[] = [];
  const pageSize = 100;
  for (let start = 0; start < maxMessages; start += pageSize) {
    const messages = await transport.listMessages({
      fid: "INBOX",
      start,
      limit: Math.min(pageSize, maxMessages - start),
      order: "date",
      desc: true
    });
    for (const message of messages) {
      if (message.id) ids.push(message.id);
    }
    if (messages.length < pageSize) break;
  }
  return ids;
}

export async function readRemoteMail(
  mailboxEmail: string,
  providerMailId: string,
  connectionId?: string | null,
  markRead = false
): Promise<MailDetail> {
  return await getMailClient(mailboxEmail, connectionId).mail.read({
    id: providerMailId,
    markRead
  });
}

function getInternalTransport(client: MailClient): InternalMailTransport | undefined {
  return (client as unknown as { transport?: InternalMailTransport }).transport;
}

export function formatSdkError(error: unknown): string {
  if (error instanceof MailSdkError) {
    return `${error.code}: ${error.message}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
