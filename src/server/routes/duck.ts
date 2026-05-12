import type { FastifyInstance } from "fastify";
import { z } from "zod";
import {
  createDuckAccount,
  deleteDuckAddress,
  deleteDuckAccount,
  getDuckAddressByAddress,
  getDuckAddressById,
  getDuckAccountById,
  listDuckAccounts,
  listDuckAddresses,
  markDuckAccountError,
  markDuckAccountUsed,
  saveDuckAddress,
  toPublicDuckAddress,
  updateDuckAccountToken,
  updateDuckAddress,
  updateDuckAddressOpenAiCredentials
} from "../db";
import { generateDuckAddress, normalizeDuckToken } from "../duck-email";
import { getSystemNetworkSettings, saveSystemNetworkSettings } from "../network-settings";

const DUCK_GENERATE_MAX_ATTEMPTS = 5;

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

const openAiCredentialsSchema = z.object({
  password: z.string().optional().nullable(),
  authJson: z.unknown().optional()
});

const networkSettingsSchema = z.object({
  proxyUrl: z.string().trim().max(300).optional().or(z.literal("")),
  timeoutMs: z.coerce.number().int().min(1000).max(120000).optional(),
  openAiOtpTimeoutMs: z.coerce.number().int().min(15000).max(300000).optional()
});

function duckAccountId(): string {
  return `duck:${crypto.randomUUID()}`;
}

function normalizeOptionalEmail(value?: string | null): string | null {
  const trimmed = value?.trim().toLowerCase();
  return trimmed || null;
}

function normalizeOptionalJson(value: unknown): string | null | undefined {
  if (value === undefined) return undefined;
  if (value === null) return null;
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return null;
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  }
  return JSON.stringify(value, null, 2);
}

export async function duckRoutes(app: FastifyInstance): Promise<void> {
  app.get("/api/duck/accounts", async () => {
    return { items: listDuckAccounts() };
  });

  app.get("/api/system/network-settings", async () => {
    return getSystemNetworkSettings();
  });

  app.put("/api/system/network-settings", async (request) => {
    const body = networkSettingsSchema.parse(request.body);
    return saveSystemNetworkSettings({
      proxyUrl: body.proxyUrl ?? "",
      timeoutMs: body.timeoutMs,
      openAiOtpTimeoutMs: body.openAiOtpTimeoutMs
    });
  });

  app.get("/api/duck/network-settings", async () => {
    return getSystemNetworkSettings();
  });

  app.put("/api/duck/network-settings", async (request) => {
    const body = networkSettingsSchema.parse(request.body);
    return saveSystemNetworkSettings({
      proxyUrl: body.proxyUrl ?? "",
      timeoutMs: body.timeoutMs,
      openAiOtpTimeoutMs: body.openAiOtpTimeoutMs
    });
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
      }).map(toPublicDuckAddress)
    };
  });

  app.get("/api/duck/addresses/:id/openai-password", async (request, reply) => {
    const { id } = request.params as { id: string };
    const row = getDuckAddressById(Number(id));
    if (!row || row.status !== "active") {
      return reply.code(404).send({ error: "Duck address not found" });
    }
    if (!row.openai_password) {
      return reply.code(404).send({ error: "该 Duck 邮箱没有保存 OpenAI 密码" });
    }
    return { password: row.openai_password };
  });

  app.get("/api/duck/addresses/:id/openai-auth-json", async (request, reply) => {
    const { id } = request.params as { id: string };
    const row = getDuckAddressById(Number(id));
    if (!row || row.status !== "active") {
      return reply.code(404).send({ error: "Duck address not found" });
    }
    if (!row.openai_auth_json) {
      return reply.code(404).send({ error: "该 Duck 邮箱没有保存 OpenAI 授权信息" });
    }
    return { authJson: row.openai_auth_json };
  });

  app.patch("/api/duck/addresses/:id/openai-credentials", async (request, reply) => {
    const { id } = request.params as { id: string };
    const body = openAiCredentialsSchema.parse(request.body ?? {});
    let authJson: string | null | undefined;
    try {
      authJson = normalizeOptionalJson(body.authJson);
    } catch {
      return reply.code(400).send({ error: "OpenAI 授权信息必须是合法 JSON" });
    }
    const row = updateDuckAddressOpenAiCredentials(Number(id), {
      password: body.password === undefined ? undefined : body.password || null,
      authJson
    });
    if (!row) {
      return reply.code(404).send({ error: "Duck address not found" });
    }
    return toPublicDuckAddress(row);
  });

  app.post("/api/duck/accounts/:id/addresses", async (request, reply) => {
    const { id } = request.params as { id: string };
    const account = getDuckAccountById(id);
    if (!account || account.status === "disabled" || !account.token) {
      return reply.code(404).send({ error: "Duck account not found or disabled" });
    }

    const body = createAddressSchema.parse(request.body ?? {});
    try {
      const networkSettings = getSystemNetworkSettings();
      let generated = await generateDuckAddress(account.token, networkSettings);
      let duplicate = getDuckAddressByAddress(generated.address);
      for (let attempt = 2; duplicate && attempt <= DUCK_GENERATE_MAX_ATTEMPTS; attempt += 1) {
        request.log.warn({
          accountId: account.id,
          address: generated.address,
          attempt
        }, "Duck API returned a duplicate private address; retrying");
        generated = await generateDuckAddress(account.token, networkSettings);
        duplicate = getDuckAddressByAddress(generated.address);
      }
      if (duplicate) {
        throw new Error(`DuckDuckGo 连续返回已存在地址：${generated.address}，请稍后重试或检查 Token 是否受限`);
      }
      const row = saveDuckAddress({
        accountId: account.id,
        address: generated.address,
        localPart: generated.localPart,
        forwardingMailboxEmail: normalizeOptionalEmail(body.forwardingMailboxEmail),
        note: body.note ?? null,
        rawJson: JSON.stringify(generated.raw)
      });
      markDuckAccountUsed(account.id);
      return reply.code(201).send(toPublicDuckAddress(row));
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
    return toPublicDuckAddress(row);
  });

  app.delete("/api/duck/addresses/:id", async (request, reply) => {
    const { id } = request.params as { id: string };
    if (!deleteDuckAddress(Number(id))) {
      return reply.code(404).send({ error: "Duck address not found" });
    }
    return { success: true };
  });
}
