import type { MailDetail } from "@clawemail/node-sdk";
import { getMailClient, formatSdkError } from "./claw-mail";
import { listActiveMailboxes, saveMail, type MailboxRow } from "./db";
import { hasClawMailConfig } from "./runtime-config";
import { sseHub } from "./sse";

type ListenerState = {
  key: string;
  connectionId: string;
  email: string;
  stopped: boolean;
  connected: boolean;
  retry: number;
  timer?: NodeJS.Timeout;
};

const listeners = new Map<string, ListenerState>();
const BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];

function attachmentList(mail: MailDetail) {
  return (mail.attachments ?? []).map((attachment) => ({
    providerPartId: attachment.id,
    filename: attachment.filename ?? null,
    contentType: attachment.contentType ?? null,
    size: attachment.size ?? null
  }));
}

async function persistIncomingMail(connectionId: string, mailboxEmail: string, providerMailId: string): Promise<number> {
  const client = getMailClient(mailboxEmail, connectionId);
  const mail = await client.mail.read({ id: providerMailId, markRead: true });
  const row = saveMail({
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
  return row.id;
}

async function connect(state: ListenerState): Promise<void> {
  if (state.stopped) return;

  try {
    const client = getMailClient(state.email, state.connectionId);
    client.ws.onMessage(async ({ mailId }) => {
      try {
        const localMailId = await persistIncomingMail(state.connectionId, state.email, mailId);
        sseHub.broadcast("mail", {
          connectionId: state.connectionId,
          mailboxEmail: state.email,
          id: localMailId,
          providerMailId: mailId
        });
      } catch (error) {
        console.error(`[listener:${state.email}] failed to persist mail`, formatSdkError(error));
      }
    });

    client.ws.onDisconnect((reason) => {
      if (state.stopped) return;
      state.connected = false;
      console.warn(`[listener:${state.email}] disconnected: ${reason}`);
      scheduleReconnect(state);
    });

    await client.ws.connect();
    state.connected = true;
    state.retry = 0;
    console.log(`[listener:${state.email}] connected`);
  } catch (error) {
    state.connected = false;
    console.error(`[listener:${state.email}] connect failed`, formatSdkError(error));
    scheduleReconnect(state);
  }
}

function scheduleReconnect(state: ListenerState): void {
  if (state.stopped || state.timer) return;
  const delay = BACKOFF_MS[Math.min(state.retry, BACKOFF_MS.length - 1)];
  state.retry += 1;
  state.timer = setTimeout(() => {
    state.timer = undefined;
    void connect(state);
  }, delay);
}

function listenerKey(connectionId: string, email: string): string {
  return `${connectionId}:${email.trim().toLowerCase()}`;
}

export function startMailboxListener(mailbox: Pick<MailboxRow, "connection_id" | "email" | "status">): void {
  const connectionId = mailbox.connection_id ?? "legacy";
  if (!hasClawMailConfig(connectionId)) return;
  const email = mailbox.email.trim().toLowerCase();
  if (mailbox.status !== "active") return;
  const key = listenerKey(connectionId, email);
  const existing = listeners.get(key);
  if (existing && !existing.stopped) return;

  const state: ListenerState = {
    key,
    connectionId,
    email,
    stopped: false,
    connected: false,
    retry: 0
  };
  listeners.set(key, state);
  void connect(state);
}

export function stopMailboxListener(email: string, connectionId = "legacy"): void {
  const normalized = email.trim().toLowerCase();
  const key = listenerKey(connectionId, normalized);
  const state = listeners.get(key);
  if (!state) return;
  state.stopped = true;
  if (state.timer) clearTimeout(state.timer);
  try {
    getMailClient(normalized, connectionId).ws.disconnect();
  } catch {
    // ignore disconnect errors
  }
  listeners.delete(key);
}

export function startAllMailboxListeners(): void {
  for (const mailbox of listActiveMailboxes()) {
    startMailboxListener(mailbox);
  }
}

export function startConnectionMailboxListeners(connectionId: string): void {
  for (const mailbox of listActiveMailboxes(connectionId)) {
    startMailboxListener(mailbox);
  }
}

export function stopAllMailboxListeners(): void {
  for (const listener of listenerSnapshot()) {
    stopMailboxListener(listener.email, listener.connectionId);
  }
}

export function stopConnectionMailboxListeners(connectionId: string): void {
  for (const listener of listenerSnapshot().filter((item) => item.connectionId === connectionId)) {
    stopMailboxListener(listener.email, connectionId);
  }
}

export function listenerSnapshot() {
  return Array.from(listeners.values()).map((listener) => ({
    connectionId: listener.connectionId,
    email: listener.email,
    status: listener.connected ? "running" : "error",
    connected: listener.connected,
    retry: listener.retry
  }));
}
