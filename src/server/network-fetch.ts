import { connect, type Socket } from "node:net";
import { connect as tlsConnect, type TLSSocket } from "node:tls";

export type NetworkFetchOptions = {
  proxyUrl?: string;
  timeoutMs?: number;
};

export type NetworkFetchInit = {
  method?: string;
  headers?: Record<string, string>;
  body?: string;
};

const DEFAULT_TIMEOUT_MS = 10000;

function readResponse(socket: Socket | TLSSocket): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    socket.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
    socket.on("end", () => resolve(Buffer.concat(chunks)));
    socket.on("error", reject);
  });
}

function decodeChunkedBody(body: Buffer): Buffer {
  let index = 0;
  const decoded: Buffer[] = [];
  while (index < body.length) {
    const lineEnd = body.indexOf("\r\n", index, "utf8");
    if (lineEnd < 0) break;
    const sizeText = body.subarray(index, lineEnd).toString("ascii").split(";")[0]?.trim() ?? "0";
    const size = Number.parseInt(sizeText, 16);
    if (!Number.isFinite(size)) throw new Error("系统代理返回了无效的 chunked 响应");
    index = lineEnd + 2;
    if (size === 0) break;
    const chunkEnd = index + size;
    if (chunkEnd > body.length) throw new Error("系统代理返回了无效的 chunked 响应");
    // chunk 长度按字节计算，必须保留 Buffer 级切片，避免中文响应被字符串索引截断。
    decoded.push(body.subarray(index, chunkEnd));
    index = chunkEnd;
    if (body.subarray(index, index + 2).toString("ascii") === "\r\n") {
      index += 2;
    } else if (body.subarray(index, index + 1).toString("ascii") === "\n") {
      index += 1;
    }
  }
  return Buffer.concat(decoded);
}

function parseHttpResponse(raw: Buffer): { status: number; statusText: string; body: Buffer } {
  const separator = raw.indexOf("\r\n\r\n", 0, "utf8");
  if (separator < 0) throw new Error("系统代理返回了无效的 HTTP 响应");
  const head = raw.subarray(0, separator).toString("utf8");
  const rawBody = raw.subarray(separator + 4);
  const lines = head.split("\r\n");
  const statusLine = lines[0] ?? "";
  const statusMatch = /^HTTP\/\d\.\d\s+(\d+)\s*(.*)$/i.exec(statusLine);
  if (!statusMatch) throw new Error(`系统代理返回了无效的状态行：${statusLine}`);
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

function waitForSecureConnect(socket: TLSSocket, timeoutMs: number): Promise<TLSSocket> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      socket.destroy();
      reject(new Error(`TLS 握手超时：${timeoutMs}ms`));
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
      reject(new Error("系统代理在 HTTP 响应头完成前关闭了连接"));
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
      reject(new Error(`连接系统代理超时：${host}:${port}，超时时间 ${timeoutMs}ms`));
    };
    socket.setTimeout(timeoutMs);
    socket.once("connect", onConnect);
    socket.once("error", onError);
    socket.once("timeout", onTimeout);
  });
}

async function requestViaHttpProxy(
  endpoint: URL,
  init: NetworkFetchInit,
  proxyUrl: string,
  timeoutMs: number
): Promise<Response> {
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
    proxySocket.destroy(new Error(`系统代理请求超时：${timeoutMs}ms`));
  });

  const connectHeaders = [
    `CONNECT ${endpoint.hostname}:443 HTTP/1.1`,
    `Host: ${endpoint.hostname}:443`,
    "Connection: keep-alive"
  ];
  if (proxy.username || proxy.password) {
    connectHeaders.push(`Proxy-Authorization: Basic ${Buffer.from(`${decodeURIComponent(proxy.username)}:${decodeURIComponent(proxy.password)}`).toString("base64")}`);
  }
  proxySocket.write(`${connectHeaders.join("\r\n")}\r\n\r\n`);
  const connectHead = await readHttpHead(proxySocket, timeoutMs);
  const connectStatusLine = connectHead.split("\r\n")[0] ?? "";
  const connectStatus = /^HTTP\/\d\.\d\s+(\d+)/i.exec(connectStatusLine);
  if (!connectStatus || Number(connectStatus[1]) < 200 || Number(connectStatus[1]) >= 300) {
    proxySocket.destroy();
    throw new Error(`系统代理 CONNECT 失败：${connectStatusLine}`);
  }

  const socket = await waitForSecureConnect(
    tlsConnect({ socket: proxySocket, servername: endpoint.hostname }),
    timeoutMs
  );
  socket.setTimeout(timeoutMs, () => {
    socket.destroy(new Error(`外部请求超时：${timeoutMs}ms`));
  });

  const body = init.body ?? "";
  const headers = new Headers(init.headers);
  headers.set("Host", endpoint.host);
  headers.set("Accept-Encoding", "identity");
  headers.set("Content-Length", String(Buffer.byteLength(body)));
  headers.set("Connection", "close");

  const requestLines = [
    `${init.method ?? "GET"} ${endpoint.pathname}${endpoint.search} HTTP/1.1`,
    ...Array.from(headers.entries()).map(([key, value]) => `${key}: ${value}`),
    "",
    body
  ];
  socket.write(`${requestLines.join("\r\n")}\r\n\r\n`);

  const raw = await readResponse(socket);
  const parsed = parseHttpResponse(raw);
  return new Response(new Uint8Array(parsed.body), {
    status: parsed.status,
    statusText: parsed.statusText
  });
}

export async function fetchWithNetworkOptions(
  url: string,
  init: NetworkFetchInit,
  options: NetworkFetchOptions = {}
): Promise<Response> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  if (options.proxyUrl) {
    return requestViaHttpProxy(new URL(url), init, options.proxyUrl, timeoutMs);
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {
      method: init.method,
      headers: init.headers,
      body: init.body,
      signal: controller.signal
    });
  } finally {
    clearTimeout(timer);
  }
}
