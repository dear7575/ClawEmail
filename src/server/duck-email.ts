import { connect, type Socket } from "node:net";
import { connect as tlsConnect, type TLSSocket } from "node:tls";

const DUCK_ADDRESS_ENDPOINT = "https://quack.duckduckgo.com/api/email/addresses";
const DUCK_ADDRESS_URL = new URL(DUCK_ADDRESS_ENDPOINT);

export type DuckGeneratedAddress = {
  address: string;
  localPart: string;
  raw: unknown;
};

export type DuckNetworkOptions = {
  proxyUrl?: string;
  timeoutMs?: number;
};

export function normalizeDuckToken(value: string): string {
  const trimmed = value.trim();
  return trimmed.replace(/^Bearer\s+/i, "").trim();
}

export function duckAuthorizationHeader(token: string): string {
  return `Bearer ${normalizeDuckToken(token)}`;
}

export function normalizeDuckAddress(value: string): { address: string; localPart: string } {
  const localPart = value.trim().toLowerCase().replace(/@duck\.com$/i, "");
  if (!/^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$/i.test(localPart)) {
    throw new Error("Duck API returned an invalid private address");
  }
  return {
    address: `${localPart}@duck.com`,
    localPart
  };
}

function networkErrorMessage(error: unknown, proxyUrl?: string): string {
  const message = error instanceof Error ? error.message : String(error);
  const proxyHint = proxyUrl ? "请检查系统设置里的 Duck 代理地址是否可从容器访问。" : "请检查容器网络是否能直连 DuckDuckGo，或在系统设置里配置 Duck 代理。";
  if (message.includes("Timeout") || message.includes("timed out") || message.includes("timeout")) {
    return `DuckDuckGo 连接超时：${proxyHint}`;
  }
  return `DuckDuckGo 网络请求失败：${message}。${proxyHint}`;
}

function readResponse(socket: Socket | TLSSocket): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    socket.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
    socket.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    socket.on("error", reject);
  });
}

function parseHttpResponse(raw: string): { status: number; statusText: string; body: string } {
  const separator = raw.indexOf("\r\n\r\n");
  if (separator < 0) throw new Error("Duck proxy returned an invalid HTTP response");
  const head = raw.slice(0, separator);
  const rawBody = raw.slice(separator + 4);
  const lines = head.split("\r\n");
  const statusLine = lines[0] ?? "";
  const statusMatch = /^HTTP\/\d\.\d\s+(\d+)\s*(.*)$/i.exec(statusLine);
  if (!statusMatch) throw new Error(`Duck proxy returned an invalid status line: ${statusLine}`);
  const headers = new Map<string, string>();
  for (const line of lines.slice(1)) {
    const index = line.indexOf(":");
    if (index < 0) continue;
    headers.set(line.slice(0, index).trim().toLowerCase(), line.slice(index + 1).trim());
  }
  const transferEncoding = headers.get("transfer-encoding")?.toLowerCase() ?? "";
  return {
    status: Number(statusMatch[1]),
    statusText: statusMatch[2] || "",
    body: transferEncoding.includes("chunked") ? decodeChunkedBody(rawBody) : rawBody
  };
}

function decodeChunkedBody(body: string): string {
  let index = 0;
  let decoded = "";
  while (index < body.length) {
    const lineEnd = body.indexOf("\r\n", index);
    if (lineEnd < 0) break;
    const sizeText = body.slice(index, lineEnd).split(";")[0]?.trim() ?? "0";
    const size = Number.parseInt(sizeText, 16);
    if (!Number.isFinite(size)) throw new Error("Duck proxy returned invalid chunked response");
    index = lineEnd + 2;
    if (size === 0) break;
    decoded += body.slice(index, index + size);
    index += size + 2;
  }
  return decoded;
}

function waitForSecureConnect(socket: TLSSocket, timeoutMs: number): Promise<TLSSocket> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      socket.destroy();
      reject(new Error(`TLS handshake timeout after ${timeoutMs}ms`));
    }, timeoutMs);
    const cleanup = () => {
      clearTimeout(timer);
      socket.removeListener("secureConnect", onSecureConnect);
      socket.removeListener("error", onError);
    };
    const onSecureConnect = () => {
      cleanup();
      resolve(socket);
    };
    const onError = (error: Error) => {
      cleanup();
      socket.destroy();
      reject(error);
    };
    socket.once("secureConnect", onSecureConnect);
    socket.once("error", onError);
  });
}

function readHttpHead(socket: Socket | TLSSocket, timeoutMs: number): Promise<string> {
  return new Promise((resolve, reject) => {
    let buffer = Buffer.alloc(0);
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error(`HTTP header timeout after ${timeoutMs}ms`));
    }, timeoutMs);
    const cleanup = () => {
      clearTimeout(timer);
      socket.removeListener("data", onData);
      socket.removeListener("error", onError);
      socket.removeListener("end", onEnd);
    };
    const onData = (chunk: Buffer) => {
      buffer = Buffer.concat([buffer, Buffer.from(chunk)]);
      const marker = buffer.indexOf("\r\n\r\n");
      if (marker < 0) return;
      cleanup();
      const rest = buffer.subarray(marker + 4);
      if (rest.length > 0) socket.unshift(rest);
      resolve(buffer.subarray(0, marker + 4).toString("utf8"));
    };
    const onError = (error: Error) => {
      cleanup();
      reject(error);
    };
    const onEnd = () => {
      cleanup();
      reject(new Error("connection closed before HTTP headers completed"));
    };
    socket.on("data", onData);
    socket.once("error", onError);
    socket.once("end", onEnd);
  });
}

function connectSocket(host: string, port: number, timeoutMs: number): Promise<Socket> {
  return new Promise((resolve, reject) => {
    const socket = connect({ host, port });
    const cleanup = () => {
      socket.removeListener("connect", onConnect);
      socket.removeListener("error", onError);
      socket.removeListener("timeout", onTimeout);
    };
    const onConnect = () => {
      cleanup();
      resolve(socket);
    };
    const onError = (error: Error) => {
      cleanup();
      socket.destroy();
      reject(error);
    };
    const onTimeout = () => {
      cleanup();
      socket.destroy();
      reject(new Error(`Connect Timeout Error (attempted address: ${host}:${port}, timeout: ${timeoutMs}ms)`));
    };
    socket.setTimeout(timeoutMs);
    socket.once("connect", onConnect);
    socket.once("error", onError);
    socket.once("timeout", onTimeout);
  });
}

async function requestViaHttpProxy(token: string, proxyUrl: string, timeoutMs: number): Promise<Response> {
  const proxy = new URL(proxyUrl);
  const proxyPort = Number(proxy.port || (proxy.protocol === "https:" ? 443 : 80));
  const rawSocket = await connectSocket(proxy.hostname, proxyPort, timeoutMs);
  let proxySocket: Socket | TLSSocket = rawSocket;
  if (proxy.protocol === "https:") {
    proxySocket = await waitForSecureConnect(
      tlsConnect({ socket: rawSocket, servername: proxy.hostname }),
      timeoutMs
    );
  }

  proxySocket.setTimeout(timeoutMs, () => {
    proxySocket.destroy(new Error(`Duck proxy request timeout after ${timeoutMs}ms`));
  });

  const connectHeaders = [
    `CONNECT ${DUCK_ADDRESS_URL.hostname}:443 HTTP/1.1`,
    `Host: ${DUCK_ADDRESS_URL.hostname}:443`,
    "Connection: keep-alive"
  ];
  if (proxy.username || proxy.password) {
    connectHeaders.push(`Proxy-Authorization: Basic ${Buffer.from(`${decodeURIComponent(proxy.username)}:${decodeURIComponent(proxy.password)}`).toString("base64")}`);
  }
  const connectRequest = `${connectHeaders.join("\r\n")}\r\n\r\n`;
  proxySocket.write(connectRequest);
  const connectHead = await readHttpHead(proxySocket, timeoutMs);
  const connectStatusLine = connectHead.split("\r\n")[0] ?? "";
  const connectStatus = /^HTTP\/\d\.\d\s+(\d+)/i.exec(connectStatusLine);
  if (!connectStatus || Number(connectStatus[1]) < 200 || Number(connectStatus[1]) >= 300) {
    proxySocket.destroy();
    throw new Error(`Duck proxy CONNECT failed: ${connectStatusLine}`);
  }

  const socket = await waitForSecureConnect(
    tlsConnect({ socket: proxySocket, servername: DUCK_ADDRESS_URL.hostname }),
    timeoutMs
  );
  socket.setTimeout(timeoutMs, () => {
    socket.destroy(new Error(`Duck request timeout after ${timeoutMs}ms`));
  });

  const body = "";
  const request = [
    `POST ${DUCK_ADDRESS_URL.pathname} HTTP/1.1`,
    `Host: ${DUCK_ADDRESS_URL.host}`,
    `Authorization: ${duckAuthorizationHeader(token)}`,
    "Accept: application/json",
    "Accept-Encoding: identity",
    "Content-Length: 0",
    "Connection: close",
    "",
    body
  ].join("\r\n") + "\r\n\r\n";

  socket.write(request);
  const raw = await readResponse(socket);
  const parsed = parseHttpResponse(raw);
  return new Response(parsed.body, {
    status: parsed.status,
    statusText: parsed.statusText
  });
}

async function requestDuckAddress(token: string, options: DuckNetworkOptions): Promise<Response> {
  const timeoutMs = options.timeoutMs ?? 10000;
  if (options.proxyUrl) {
    return requestViaHttpProxy(token, options.proxyUrl, timeoutMs);
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(DUCK_ADDRESS_ENDPOINT, {
      method: "POST",
      headers: {
        authorization: duckAuthorizationHeader(token),
        accept: "application/json"
      },
      signal: controller.signal
    });
  } finally {
    clearTimeout(timer);
  }
}

export async function generateDuckAddress(
  token: string,
  options: DuckNetworkOptions = {}
): Promise<DuckGeneratedAddress> {
  let response: Response;
  try {
    response = await requestDuckAddress(token, options);
  } catch (error) {
    throw new Error(networkErrorMessage(error, options.proxyUrl));
  }

  const text = await response.text();
  let body: unknown = null;
  if (text.trim()) {
    try {
      body = JSON.parse(text);
    } catch {
      throw new Error(`Duck address API returned non-JSON response: HTTP ${response.status}`);
    }
  }

  if (!response.ok) {
    const message = typeof body === "object" && body && "message" in body
      ? String((body as { message?: unknown }).message)
      : response.statusText || `HTTP ${response.status}`;
    throw new Error(`Duck address API error: ${message}`);
  }

  const rawAddress = typeof body === "object" && body && "address" in body
    ? (body as { address?: unknown }).address
    : undefined;
  if (typeof rawAddress !== "string" || !rawAddress.trim()) {
    throw new Error("Duck address API response did not include address");
  }

  return {
    ...normalizeDuckAddress(rawAddress),
    raw: body
  };
}
