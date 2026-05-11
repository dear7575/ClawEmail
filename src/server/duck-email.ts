import { fetchWithNetworkOptions, type NetworkFetchOptions } from "./network-fetch";

const DUCK_ADDRESS_ENDPOINT = "https://quack.duckduckgo.com/api/email/addresses";

export type DuckGeneratedAddress = {
  address: string;
  localPart: string;
  raw: unknown;
};

export type DuckNetworkOptions = NetworkFetchOptions;

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
  const proxyHint = proxyUrl ? "请检查系统设置里的系统代理地址是否可从容器访问。" : "请检查容器网络是否能直连 DuckDuckGo，或在系统设置里配置系统代理。";
  if (message.includes("Timeout") || message.includes("timed out") || message.includes("timeout")) {
    return `DuckDuckGo 连接超时：${proxyHint}`;
  }
  return `DuckDuckGo 网络请求失败：${message}。${proxyHint}`;
}

async function requestDuckAddress(token: string, options: DuckNetworkOptions): Promise<Response> {
  return fetchWithNetworkOptions(DUCK_ADDRESS_ENDPOINT, {
    method: "POST",
    headers: {
      authorization: duckAuthorizationHeader(token),
      accept: "application/json"
    }
  }, options);
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
