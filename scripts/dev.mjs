import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const backendDir = path.join(rootDir, "backend");
const frontendDir = path.join(rootDir, "frontend");
const backendHost = process.env.HOST || "127.0.0.1";
const backendPort = process.env.PORT || "8000";
const frontendHost = process.env.FRONTEND_HOST || "0.0.0.0";
const frontendPort = process.env.FRONTEND_PORT || "3001";
const backendUrl = process.env.BACKEND_URL || `http://${backendHost}:${backendPort}`;
const backendHealthUrl = `${backendUrl}/health`;
const backendStartTimeoutMs = Number(process.env.BACKEND_START_TIMEOUT_MS || "30000");
const pythonCommand = process.env.PYTHON || "python";
const children = new Set();

function spawnProcess(name, command, args, options) {
  const child = spawn(command, args, {
    ...options,
    env: {
      ...process.env,
      ...options.env,
    },
    stdio: "inherit",
  });
  children.add(child);

  child.on("exit", (code, signal) => {
    children.delete(child);
    if (shuttingDown) return;
    console.error(`${name} 已退出：code=${code ?? "null"} signal=${signal ?? "null"}`);
    shutdown(code && code > 0 ? code : 1);
  });

  child.on("error", (error) => {
    children.delete(child);
    console.error(`${name} 启动失败：${error.message}`);
    shutdown(1);
  });

  return child;
}

let shuttingDown = false;

function shutdown(exitCode = 0) {
  shuttingDown = true;
  for (const child of children) {
    child.kill("SIGTERM");
  }
  setTimeout(() => process.exit(exitCode), 300).unref();
}

async function waitForBackend() {
  const deadline = Date.now() + backendStartTimeoutMs;
  while (Date.now() < deadline) {
    if (await isBackendReady()) return;
    await sleep(500);
  }

  throw new Error(`后端未在 ${backendStartTimeoutMs}ms 内就绪：${backendHealthUrl}`);
}

async function isBackendReady() {
  try {
    const response = await fetch(backendHealthUrl);
    return response.ok;
  } catch {
    // 后端启动期间连接失败是预期状态，直到超时才报错。
    return false;
  }
}

process.on("SIGINT", () => shutdown(130));
process.on("SIGTERM", () => shutdown(143));

if (await isBackendReady()) {
  console.log(`复用已就绪的 FastAPI 后端：${backendUrl}`);
} else {
  console.log(`启动 FastAPI 后端：${backendUrl}`);
  spawnProcess(
    "backend",
    pythonCommand,
    ["-m", "uvicorn", "app.main:app", "--reload", "--host", backendHost, "--port", backendPort],
    {
      cwd: backendDir,
      env: {
        HOST: backendHost,
        PORT: backendPort,
      },
    },
  );
}

try {
  await waitForBackend();
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  shutdown(1);
  await sleep(1000);
}

console.log(`启动 Next.js 前端：http://localhost:${frontendPort}`);
spawnProcess(
  "frontend",
  process.execPath,
  ["./node_modules/next/dist/bin/next", "dev", "--hostname", frontendHost, "--port", frontendPort],
  {
    cwd: frontendDir,
    env: {
      BACKEND_URL: backendUrl,
    },
  },
);
