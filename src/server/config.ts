import "dotenv/config";
import { z } from "zod";

const envSchema = z.object({
  NODE_ENV: z.string().default("development"),
  PORT: z.coerce.number().int().positive().default(3000),
  ADMIN_PASSWORD: z.string().min(1).default("change-me"),
  CLAW_API_KEY: z.string().optional(),
  CLAW_DASHBOARD_COOKIE: z.string().optional(),
  CLAW_WORKSPACE_ID: z.string().optional(),
  CLAW_PARENT_MAILBOX_ID: z.string().optional(),
  CLAW_ROOT_PREFIX: z.string().optional(),
  CLAW_DOMAIN: z.string().default("claw.163.com"),
  DUCK_PROXY_URL: z.string().optional(),
  DUCK_REQUEST_TIMEOUT_MS: z.coerce.number().int().positive().optional(),
  DATABASE_PATH: z.string().default("./data/app.db")
});

export const config = envSchema.parse(process.env);

export function requireClawApiKey(): string {
  if (!config.CLAW_API_KEY) {
    throw new Error("CLAW_API_KEY is required for mail operations");
  }
  return config.CLAW_API_KEY;
}

export function requireDashboardCookie(): string {
  if (!config.CLAW_DASHBOARD_COOKIE) {
    throw new Error("CLAW_DASHBOARD_COOKIE is required for mailbox management");
  }
  return config.CLAW_DASHBOARD_COOKIE;
}

export function normalizeMailboxEmail(value: string): string {
  const trimmed = value.trim().toLowerCase();
  if (trimmed.includes("@")) {
    return trimmed;
  }
  return `${trimmed}@${config.CLAW_DOMAIN}`;
}

export function suffixToEmail(suffix: string): string {
  if (!config.CLAW_ROOT_PREFIX) {
    throw new Error("CLAW_ROOT_PREFIX is required to format mailbox addresses");
  }
  const root = config.CLAW_ROOT_PREFIX.trim().toLowerCase();
  return `${root}.${suffix}@${config.CLAW_DOMAIN}`;
}
