import Fastify from "fastify";
import fastifyStatic from "@fastify/static";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { ZodError } from "zod";
import { config } from "./config";
import "./db";
import { mailboxRoutes } from "./routes/mailboxes";
import { mailRoutes } from "./routes/mails";
import { sendRoutes } from "./routes/send";
import { eventRoutes } from "./routes/events";
import { clawAuthRoutes } from "./routes/claw-auth";
import { duckRoutes } from "./routes/duck";
import { telegramRoutes } from "./routes/telegram";
import { sub2Routes } from "./routes/sub2";
import { openAiAuthRoutes } from "./routes/openai-auth";
import { startAllMailboxListeners } from "./listener-manager";
import { hasClawMailConfig } from "./runtime-config";

const app = Fastify({
  logger: true
});

function extractAdminPassword(request: any): string | undefined {
  const header = request.headers["x-admin-password"];
  if (typeof header === "string") return header;
  const queryPassword = request.query?.token;
  if (typeof queryPassword === "string") return queryPassword;
  return undefined;
}

app.addHook("onRequest", async (request, reply) => {
  if (!request.url.startsWith("/api/")) return;
  const password = extractAdminPassword(request);
  if (password !== config.ADMIN_PASSWORD) {
    return reply.code(401).send({ error: "unauthorized" });
  }
});

app.setErrorHandler((error, _request, reply) => {
  if (error instanceof ZodError) {
    return reply.code(400).send({ error: "invalid input", details: error.issues });
  }
  app.log.error(error);
  return reply.code(500).send({
    error: error instanceof Error ? error.message : "internal server error"
  });
});

app.get("/health", async () => {
  return { ok: true, runtime: "node" };
});

await mailboxRoutes(app);
await mailRoutes(app);
await sendRoutes(app);
await eventRoutes(app);
await clawAuthRoutes(app);
await duckRoutes(app);
await telegramRoutes(app);
await sub2Routes(app);
await openAiAuthRoutes(app);

const __dirname = dirname(fileURLToPath(import.meta.url));
const webRoot = join(__dirname, "../web");
await app.register(fastifyStatic, {
  root: webRoot,
  prefix: "/"
});

app.setNotFoundHandler(async (_request, reply) => {
  return reply.sendFile("index.html");
});

if (hasClawMailConfig()) {
  startAllMailboxListeners();
} else {
  app.log.warn("CLAW_API_KEY is not set; mailbox listeners are disabled until configured");
}

await app.listen({ host: "0.0.0.0", port: config.PORT });
