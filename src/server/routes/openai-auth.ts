import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { pushOpenAiDuckAddressToSub2 } from "../openai-auth";

const duckPushSchema = z.object({
  duckAddressId: z.coerce.number().int().positive(),
  groupId: z.coerce.number().int().positive().nullable().optional()
});

export async function openAiAuthRoutes(app: FastifyInstance): Promise<void> {
  app.post("/api/openai/duck-push-sub2", async (request) => {
    const body = duckPushSchema.parse(request.body);
    return {
      success: true,
      ...await pushOpenAiDuckAddressToSub2(body.duckAddressId, body.groupId, request.log)
    };
  });
}
