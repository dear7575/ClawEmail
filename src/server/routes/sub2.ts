import type { FastifyInstance } from "fastify";
import { z } from "zod";
import {
  convertChatGptSessionToSub2,
  fetchSub2Groups,
  pushSub2Account,
  saveSub2Settings,
  toPublicSub2Settings
} from "../sub2";

const sub2SettingsSchema = z.object({
  apiUrl: z.string().trim().max(500).optional(),
  apiKey: z.string().trim().max(500).optional()
});

const accountPayloadSchema = z.object({
  input: z.unknown(),
  groupId: z.coerce.number().int().positive().optional()
});

export async function sub2Routes(app: FastifyInstance): Promise<void> {
  app.get("/api/sub2/settings", async () => {
    return toPublicSub2Settings();
  });

  app.put("/api/sub2/settings", async (request) => {
    const body = sub2SettingsSchema.parse(request.body);
    return saveSub2Settings(body);
  });

  app.get("/api/sub2/groups", async () => {
    return { items: await fetchSub2Groups() };
  });

  app.post("/api/sub2/convert", async (request) => {
    const body = accountPayloadSchema.parse(request.body);
    return { data: convertChatGptSessionToSub2(body.input) };
  });

  app.post("/api/sub2/push", async (request) => {
    const body = accountPayloadSchema.parse(request.body);
    if (!body.groupId) {
      throw new Error("请选择要推送到的 Sub2 分组");
    }
    return {
      success: true,
      ...await pushSub2Account(body.input, body.groupId)
    };
  });
}
