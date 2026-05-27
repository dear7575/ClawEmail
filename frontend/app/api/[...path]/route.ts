export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "content-encoding",
  "content-length",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

/** 读取后端服务基址，默认匹配本地 FastAPI 开发端口。 */
function backendBaseUrl(): string {
  return process.env.BACKEND_URL || "http://127.0.0.1:8000";
}

/** 使用原始请求路径拼出完整后端 API URL，并保留查询参数和路径编码。 */
function targetUrl(request: Request): string {
  const sourceUrl = new URL(request.url);
  const url = new URL(sourceUrl.pathname, backendBaseUrl());
  url.search = sourceUrl.search;
  return url.toString();
}

/** 过滤逐跳请求头，避免把代理连接内部状态透传给 FastAPI。 */
function forwardRequestHeaders(request: Request): Headers {
  const headers = new Headers(request.headers);
  for (const name of HOP_BY_HOP_HEADERS) {
    headers.delete(name);
  }
  headers.delete("host");
  return headers;
}

/** 过滤逐跳响应头，交由 Next.js/Node 重新计算传输相关字段。 */
function forwardResponseHeaders(response: Response): Headers {
  const headers = new Headers(response.headers);
  for (const name of HOP_BY_HOP_HEADERS) {
    headers.delete(name);
  }
  return headers;
}

/** 执行实际代理请求，并把后端不可用转换为结构化 503。 */
async function proxyRequest(request: Request): Promise<Response> {
  const url = targetUrl(request);
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers: forwardRequestHeaders(request),
    redirect: "manual",
    cache: "no-store",
    signal: request.signal,
  };

  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body;
    init.duplex = "half";
  }

  try {
    const response = await fetch(url, init);
    return new Response(request.method === "HEAD" ? null : response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: forwardResponseHeaders(response),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error(`后端代理请求失败：${url} error=${message}`);
    return Response.json(
      {
        error: "backend unavailable",
        details: `FastAPI 后端不可用：${backendBaseUrl()}。请使用 npm run dev 启动全栈开发服务，或先单独启动 backend。`,
      },
      { status: 503 },
    );
  }
}

export function GET(request: Request): Promise<Response> {
  return proxyRequest(request);
}

export function POST(request: Request): Promise<Response> {
  return proxyRequest(request);
}

export function PUT(request: Request): Promise<Response> {
  return proxyRequest(request);
}

export function PATCH(request: Request): Promise<Response> {
  return proxyRequest(request);
}

export function DELETE(request: Request): Promise<Response> {
  return proxyRequest(request);
}

export function OPTIONS(request: Request): Promise<Response> {
  return proxyRequest(request);
}
