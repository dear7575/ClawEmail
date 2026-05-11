import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { saveTelegramSettings, sendTelegramMessage, toPublicTelegramSettings } from "../telegram";

const telegramSettingsSchema = z.object({
  enabled: z.boolean().optional(),
  botToken: z.string().trim().max(200).optional(),
  chatId: z.string().trim().max(120).optional()
});

const telegramMessageSchema = z.object({
  text: z.string().trim().min(1).max(4096)
});

export async function telegramRoutes(app: FastifyInstance): Promise<void> {
  app.get("/api/telegram/settings", async () => {
    return toPublicTelegramSettings();
  });

  app.put("/api/telegram/settings", async (request) => {
    const body = telegramSettingsSchema.parse(request.body);
    return saveTelegramSettings(body);
  });

  app.post("/api/telegram/send", async (request) => {
    const body = telegramMessageSchema.parse(request.body);
    await sendTelegramMessage(body.text);
    return { success: true };
  });
}
