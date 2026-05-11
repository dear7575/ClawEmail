import type { FastifyInstance } from "fastify";
import { z } from "zod";
import {
  createDuckAccount,
  deleteDuckAddress,
  deleteDuckAccount,
  getDuckAccountById,
  listDuckAccounts,
  listDuckAddresses,
  markDuckAccountError,
  markDuckAccountUsed,
  saveDuckAddress,
  updateDuckAccountToken,
  updateDuckAddress
} from "../db";
import { generateDuckAddress, normalizeDuckToken } from "../duck-email";

const accountSchema = z.object({
  label: z.string().trim().min(1).max(80),
  token: z.string().trim().min(12)
});

const accountTokenSchema = z.object({
  token: z.string().trim().min(12)
});

const listAddressQuerySchema = z.object({
  accountId: z.string().min(1).optional()
});

const createAddressSchema = z.object({
  forwardingMailboxEmail: z.string().email().optional().or(z.literal("")),
  note: z.string().trim().max(300).optional()
});

const updateAddressSchema = z.object({
  forwardingMailboxEmail: z.string().email().optional().or(z.literal("")).nullable(),
  note: z.string().trim().max(300).optional().nullable()
});

function duckAccountId(): string {
  return `duck:${crypto.randomUUID()}`;
}

function normalizeOptionalEmail(value?: string | null): string | null {
  const trimmed = value?.trim().toLowerCase();
  return trimmed || null;
}

export async function duckRoutes(app: FastifyInstance): Promise<void> {
  app.get("/api/duck/accounts", async () => {
    return { items: listDuckAccounts() };
  });

  app.post("/api/duck/accounts", async (request, reply) => {
    const body = accountSchema.parse(request.body);
    const account = createDuckAccount({
      id: duckAccountId(),
      label: body.label,
      token: normalizeDuckToken(body.token)
    });
    return reply.code(201).send(account);
  });

  app.delete("/api/duck/accounts/:id", async (request, reply) => {
    const { id } = request.params as { id: string };
    if (!deleteDuckAccount(id)) {
      return reply.code(404).send({ error: "Duck account not found" });
    }
    return { success: true };
  });

  app.patch("/api/duck/accounts/:id", async (request, reply) => {
    const { id } = request.params as { id: string };
    const body = accountTokenSchema.parse(request.body);
    const account = updateDuckAccountToken(id, normalizeDuckToken(body.token));
    if (!account) {
      return reply.code(404).send({ error: "Duck account not found" });
    }
    return account;
  });

  app.get("/api/duck/addresses", async (request) => {
    const query = listAddressQuerySchema.parse(request.query);
    return {
      items: listDuckAddresses({
        accountId: query.accountId
      })
    };
  });

  app.post("/api/duck/accounts/:id/addresses", async (request, reply) => {
    const { id } = request.params as { id: string };
    const account = getDuckAccountById(id);
    if (!account || account.status === "disabled" || !account.token) {
      return reply.code(404).send({ error: "Duck account not found or disabled" });
    }

    const body = createAddressSchema.parse(request.body ?? {});
    try {
      const generated = await generateDuckAddress(account.token);
      const row = saveDuckAddress({
        accountId: account.id,
        address: generated.address,
        localPart: generated.localPart,
        forwardingMailboxEmail: normalizeOptionalEmail(body.forwardingMailboxEmail),
        note: body.note ?? null,
        rawJson: JSON.stringify(generated.raw)
      });
      markDuckAccountUsed(account.id);
      return reply.code(201).send(row);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      markDuckAccountError(account.id, message);
      throw error;
    }
  });

  app.patch("/api/duck/addresses/:id", async (request, reply) => {
    const { id } = request.params as { id: string };
    const body = updateAddressSchema.parse(request.body);
    const row = updateDuckAddress(Number(id), {
      forwardingMailboxEmail: body.forwardingMailboxEmail === undefined
        ? undefined
        : normalizeOptionalEmail(body.forwardingMailboxEmail),
      note: body.note === undefined ? undefined : body.note ?? null
    });
    if (!row) {
      return reply.code(404).send({ error: "Duck address not found" });
    }
    return row;
  });

  app.delete("/api/duck/addresses/:id", async (request, reply) => {
    const { id } = request.params as { id: string };
    if (!deleteDuckAddress(Number(id))) {
      return reply.code(404).send({ error: "Duck address not found" });
    }
    return { success: true };
  });
}
