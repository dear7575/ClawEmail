const DUCK_ADDRESS_ENDPOINT = "https://quack.duckduckgo.com/api/email/addresses";

export type DuckGeneratedAddress = {
  address: string;
  localPart: string;
  raw: unknown;
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

export async function generateDuckAddress(token: string): Promise<DuckGeneratedAddress> {
  const response = await fetch(DUCK_ADDRESS_ENDPOINT, {
    method: "POST",
    headers: {
      authorization: duckAuthorizationHeader(token),
      accept: "application/json"
    }
  });

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
