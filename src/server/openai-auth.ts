import {createHash, randomBytes, randomUUID} from "node:crypto";
import {Buffer} from "node:buffer";
import {getMailClient, listRemoteInboxMessageIds, readRemoteMail} from "./claw-mail";
import {
    type DuckAddressRow,
    getDuckAddressById,
    getMailboxByEmail,
    getMailByProviderId,
    type MailboxRow,
    markMailRead,
    setDuckAddressOpenAiAuthJson,
    setDuckAddressOpenAiPassword
} from "./db";
import {getSystemNetworkSettings, type SystemNetworkSettings} from "./network-settings";
import {type NetworkFetchResponse, requestWithNetworkOptions} from "./network-fetch";
import {
    convertOpenAiOAuthToSub2,
    isSub2AuthBranchFallbackError,
    type OpenAiOAuthSub2Input,
    pushSub2Data,
    pushSub2DataViaAuthLogin,
    Sub2AuthBranchFallbackError,
    type Sub2AuthLoginCallback,
    type Sub2AuthLoginRequest,
    type Sub2DataPayload,
    type Sub2PushMode
} from "./sub2";
import {sendTelegramMessage} from "./telegram";

type OpenAiAuthLogger = {
    info: (obj: Record<string, unknown>, msg?: string) => void;
    warn?: (obj: Record<string, unknown>, msg?: string) => void;
    error?: (obj: Record<string, unknown>, msg?: string) => void;
};

type CookieEntry = {
    name: string;
    value: string;
    domain: string;
    path: string;
};

type RequestOptions = {
    method?: string;
    headers?: Record<string, string>;
    body?: unknown;
    referer?: string;
    redirectLimit?: number;
};

type PasswordVerifyResult = {
    nextUrl: string;
    pageType: string;
    requiresEmailOtp: boolean;
    otpRequestedAtMs?: number;
};

type VerificationCodeCandidate = {
    code: string;
    providerMailId: string;
    mailTime: number;
};

type OpenAiAuthProgress = {
    logger?: OpenAiAuthLogger;
    operationId: string;
    startedAt: number;
    email: string;
    inboxEmail: string;
};

type OpenAiLoginResult = {
    token: OpenAiOAuthSub2Input;
    client: OpenAiAuthClient;
    deviceId: string;
};

export type OpenAiDuckPushResult = {
    email: string;
    data: Sub2DataPayload;
    response: unknown;
    pushMode: Sub2PushMode;
    fallbackReason?: string;
    telegram: {
        sent: boolean;
        error?: string;
    };
};

const AUTH_BASE = "https://auth.openai.com";
const PLATFORM_BASE = "https://platform.openai.com";
const CHATGPT_BASE = "https://chatgpt.com";
const PLATFORM_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD";
const PLATFORM_REDIRECT_URI = `${PLATFORM_BASE}/auth/callback`;
const PLATFORM_AUDIENCE = "https://api.openai.com/v1";
const PLATFORM_AUTH0_CLIENT = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9";
const USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36";
const SEC_CH_UA = "\"Google Chrome\";v=\"145\", \"Not?A_Brand\";v=\"8\", \"Chromium\";v=\"145\"";
const SEC_CH_UA_FULL = "\"Chromium\";v=\"145.0.0.0\", \"Not:A-Brand\";v=\"99.0.0.0\", \"Google Chrome\";v=\"145.0.0.0\"";
const CODE_PATTERN = /\b(\d{6})\b/;
const HTML_CODE_BLOCK_PATTERN = /<p\b[^>]*font-size:\s*24px[^>]*>\s*(\d{6})\s*<\/p>/i;
const STANDALONE_CODE_PATTERN = /(?:^|[\s>])(\d{6})(?:[\s<]|$)/;
const OPENAI_CODE_SUBJECT_PATTERN = /(openai|chatgpt|chat gpt|验证码|verification|code|login)/i;
const OTP_MAIL_TIME_GRACE_MS = 90_000;
const OTP_MAIL_FALLBACK_ACCEPT_MS = 15 * 60_000;

function base64Url(input: Buffer): string {
    return input.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function serializeOpenAiAuthJson(token: OpenAiOAuthSub2Input): string {
    return JSON.stringify({
        email: token.email,
        accessToken: token.accessToken,
        refreshToken: token.refreshToken,
        idToken: token.idToken,
        expiresAt: token.expiresAt,
        userId: token.userId,
        accountId: token.accountId,
        planType: token.planType
    }, null, 2);
}

function formatOpenAiAccessTokenMessage(token: OpenAiOAuthSub2Input): string {
    return [
        `OpenAI 账号推送成功：${token.email}`,
        "",
        "access_token:",
        token.accessToken
    ].join("\n");
}

async function notifyOpenAiAccessToken(token: OpenAiOAuthSub2Input, logger?: OpenAiAuthLogger): Promise<OpenAiDuckPushResult["telegram"]> {
    try {
        await sendTelegramMessage(formatOpenAiAccessTokenMessage(token));
        return {sent: true};
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        logger?.warn?.({email: token.email, error: message}, "OpenAI access token Telegram notification failed");
        return {sent: false, error: message};
    }
}

function generatePkce(): { verifier: string; challenge: string } {
    const verifier = base64Url(randomBytes(64));
    const challenge = base64Url(createHash("sha256").update(verifier).digest());
    return {verifier, challenge};
}

function decodeJwtPayload(token: string): Record<string, unknown> {
    try {
        const payload = token.split(".")[ 1 ];
        if (!payload) return {};
        const padded = payload + "=".repeat(( 4 - payload.length % 4 ) % 4);
        const decoded = Buffer.from(padded.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8");
        const parsed = JSON.parse(decoded);
        return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
    } catch {
        return {};
    }
}

function createOperationId(): string {
    return randomBytes(4).toString("hex");
}

function logProgress(
    progress: OpenAiAuthProgress | undefined,
    stage: string,
    extra: Record<string, unknown> = {},
    level: "info" | "warn" | "error" = "info"
): void {
    const logger = progress?.logger;
    if (!logger) return;
    const payload = {
        component: "openai-sub2-push",
        operationId: progress.operationId,
        stage,
        elapsedMs: Date.now() - progress.startedAt,
        email: progress.email,
        inboxEmail: progress.inboxEmail,
        ...extra
    };
    const msg = `openai-sub2-push ${stage} operation=${payload.operationId} elapsed=${payload.elapsedMs}ms`;
    if (level === "error" && logger.error) logger.error(payload, msg);
    else if (level === "warn" && logger.warn) logger.warn(payload, msg);
    else logger.info(payload, msg);
}

function makeTraceHeaders(): Record<string, string> {
    const traceId = BigInt(`0x${randomBytes(8).toString("hex")}`).toString();
    const parentId = BigInt(`0x${randomBytes(8).toString("hex")}`).toString();
    return {
        traceparent: `00-${randomUUID().replace(/-/g, "")}-${Number(parentId).toString(16).padStart(16, "0").slice(-16)}-01`,
        tracestate: "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-parent-id": parentId,
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": traceId
    };
}

function jsonOrNull(text: string): unknown {
    if (!text.trim()) return null;
    try {
        return JSON.parse(text);
    } catch {
        return null;
    }
}

function bodyError(body: unknown, fallback: string): string {
    if (body && typeof body === "object") {
        const record = body as Record<string, unknown>;
        const error = record.error;
        if (error && typeof error === "object" && typeof ( error as Record<string, unknown> ).message === "string") {
            return String(( error as Record<string, unknown> ).message);
        }
        for (const key of ["message", "error_description", "detail", "reason"]) {
            if (typeof record[ key ] === "string" && record[ key ]) return String(record[ key ]);
        }
    }
    return fallback;
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringField(record: Record<string, unknown>, key: string): string {
    const value = record[ key ];
    return typeof value === "string" ? value.trim() : "";
}

function pageType(body: unknown): string {
    const root = asRecord(body);
    const page = root.page;
    if (typeof page === "string") return page.trim();
    return stringField(asRecord(page), "type");
}

function continueUrl(body: unknown): string {
    return stringField(asRecord(body), "continue_url");
}

function authStep(body: unknown): string {
    const root = asRecord(body);
    for (const key of ["step", "action", "screen", "state", "method"]) {
        const value = stringField(root, key);
        if (value) return value;
    }
    const flow = stringField(asRecord(root.flow), "step") || stringField(asRecord(root.flow), "name");
    return flow;
}

function requiresAccountProfile(page: string): boolean {
    return page === "account_details" || page === "about_you";
}

function requiresEmailOtpStep(body: unknown): boolean {
    const values = [
        pageType(body),
        continueUrl(body),
        authStep(body),
        JSON.stringify(summarizeAuthBody(body))
    ].join(" ");
    return /email[_-]?otp|email[_-]?verification|verification[_-]?code|verify[_-]?email|mfa/i.test(values);
}

function summarizeAuthBody(body: unknown): Record<string, unknown> {
    const root = asRecord(body);
    return {
        pageType: pageType(body),
        continueUrl: continueUrl(body),
        step: authStep(body),
        hasError: Boolean(root.error),
        keys: Object.keys(root).slice(0, 12)
    };
}

function normalizeEmail(value: string): string {
    return value.trim().toLowerCase();
}

function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomPassword(length = 18): string {
    const upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
    const lower = "abcdefghijklmnopqrstuvwxyz";
    const digits = "0123456789";
    const symbols = "!@#$%";
    const all = upper + lower + digits + symbols;
    const chars = [
        upper[ Math.floor(Math.random() * upper.length) ],
        lower[ Math.floor(Math.random() * lower.length) ],
        digits[ Math.floor(Math.random() * digits.length) ],
        symbols[ Math.floor(Math.random() * symbols.length) ]
    ];
    while (chars.length < length) {
        chars.push(all[ Math.floor(Math.random() * all.length) ]);
    }
    return chars.sort(() => Math.random() - 0.5).join("");
}

function randomProfile(): { name: string; birthdate: string } {
    const first = ["James", "Robert", "John", "Michael", "David", "Mary", "Emma", "Olivia"];
    const last = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller"];
    const year = 1996 + Math.floor(Math.random() * 11);
    const month = String(1 + Math.floor(Math.random() * 12)).padStart(2, "0");
    const day = String(1 + Math.floor(Math.random() * 28)).padStart(2, "0");
    return {
        name: `${first[ Math.floor(Math.random() * first.length) ]} ${last[ Math.floor(Math.random() * last.length) ]}`,
        birthdate: `${year}-${month}-${day}`
    };
}

function extractVerificationCode(input: {
    subject?: string | null;
    text?: string | null;
    html?: string | null;
}): string | null {
    const subject = input.subject ?? "";
    const text = input.text ?? "";
    const html = input.html ?? "";
    const haystack = [subject, text, html].filter(Boolean).join("\n");
    if (!OPENAI_CODE_SUBJECT_PATTERN.test(subject) && !/openai|chatgpt|chat gpt/i.test(haystack)) {
        return null;
    }
    return HTML_CODE_BLOCK_PATTERN.exec(html)?.[ 1 ] ??
        STANDALONE_CODE_PATTERN.exec(text)?.[ 1 ] ??
        STANDALONE_CODE_PATTERN.exec(subject)?.[ 1 ] ??
        STANDALONE_CODE_PATTERN.exec(html.replace(/<[^>]+>/g, " "))?.[ 1 ] ??
        CODE_PATTERN.exec([subject, text].join("\n"))?.[ 1 ] ??
        null;
}

function mailAddresses(value?: string[] | null): string[] {
    return ( value ?? [] ).flatMap((item) => {
        const matches = item.match(/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/ig) ?? [];
        return matches.map((email) => email.toLowerCase());
    });
}

function mailTargetsExpectedAddress(mail: Awaited<ReturnType<typeof readRemoteMail>>, targetEmail: string, inboxEmail: string): boolean {
    const expected = new Set([normalizeEmail(targetEmail), normalizeEmail(inboxEmail)]);
    const recipients = [
        ...mailAddresses(mail.to),
        ...mailAddresses(mail.cc),
        ...mailAddresses(mail.bcc),
        ...mailAddresses(mail.headerRaw ? [mail.headerRaw] : [])
    ];
    return recipients.length === 0 || recipients.some((email) => expected.has(email));
}

function parseMailTime(value?: string | null): number | null {
    const raw = value?.trim() ?? "";
    if (!raw) return null;
    const direct = Date.parse(raw);
    if (Number.isFinite(direct)) return direct;
    const localMatch = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/.exec(raw);
    if (!localMatch) return null;
    const [, year, month, day, hour, minute, second] = localMatch;
    const local = new Date(
        Number(year),
        Number(month) - 1,
        Number(day),
        Number(hour),
        Number(minute),
        Number(second)
    ).getTime();
    return Number.isFinite(local) ? local : null;
}

async function readLatestVerificationCode(
    mailbox: MailboxRow,
    targetEmail: string,
    sinceMs: number,
    ignoredProviderMailIds: Set<string> = new Set(),
    progress?: OpenAiAuthProgress
): Promise<VerificationCodeCandidate | null> {
    const ids = await listRemoteInboxMessageIds(mailbox.email, 50, mailbox.connection_id);
    const candidates: VerificationCodeCandidate[] = [];
    for (const id of ids) {
        if (ignoredProviderMailIds.has(id)) continue;
        const mail = await readRemoteMail(mailbox.email, id, mailbox.connection_id);
        const code = extractVerificationCode({
            subject: mail.subject ?? null,
            text: mail.text?.content ?? null,
            html: mail.html?.content ?? null
        });
        if (!code) continue;
        if (!mailTargetsExpectedAddress(mail, targetEmail, mailbox.email)) {
            logProgress(progress, "otp_candidate_skipped_recipient", {
                providerMailId: id,
                subject: mail.subject ?? "",
                date: mail.date ?? ""
            });
            continue;
        }
        const parsedMailTime = parseMailTime(mail.date);
        const mailTime = parsedMailTime ?? 0;
        const thresholdMs = Math.floor(( sinceMs - OTP_MAIL_TIME_GRACE_MS ) / 1000) * 1000;
        const fallbackThresholdMs = Math.floor(( sinceMs - OTP_MAIL_FALLBACK_ACCEPT_MS ) / 1000) * 1000;
        if (parsedMailTime !== null && mailTime < fallbackThresholdMs) {
            logProgress(progress, "otp_candidate_skipped_old", {
                providerMailId: id,
                subject: mail.subject ?? "",
                date: mail.date ?? "",
                sinceMs,
                graceMs: OTP_MAIL_TIME_GRACE_MS,
                fallbackAcceptMs: OTP_MAIL_FALLBACK_ACCEPT_MS
            });
            continue;
        }
        if (parsedMailTime !== null && mailTime < thresholdMs) {
            logProgress(progress, "otp_candidate_seen_fallback_window", {
                providerMailId: id,
                subject: mail.subject ?? "",
                date: mail.date ?? "",
                sinceMs,
                fallbackAcceptMs: OTP_MAIL_FALLBACK_ACCEPT_MS,
                codeSuffix: code.slice(-2)
            });
        }
        candidates.push({
            code,
            providerMailId: id,
            mailTime: mailTime || sinceMs - OTP_MAIL_FALLBACK_ACCEPT_MS
        });
        logProgress(progress, "otp_candidate_seen", {
            providerMailId: id,
            subject: mail.subject ?? "",
            date: mail.date ?? "",
            to: mail.to ?? [],
            codeSuffix: code.slice(-2)
        });
    }
    const selected = candidates.sort((a, b) => b.mailTime - a.mailTime)[ 0 ] ?? null;
    if (selected) {
        logProgress(progress, "otp_candidate_selected", {
            providerMailId: selected.providerMailId,
            mailTime: selected.mailTime,
            codeSuffix: selected.code.slice(-2)
        });
        await readRemoteMail(mailbox.email, selected.providerMailId, mailbox.connection_id, true);
        const localMail = getMailByProviderId(mailbox.email, selected.providerMailId, mailbox.connection_id ?? undefined);
        if (localMail) {
            markMailRead(localMail.id);
        }
        logProgress(progress, "otp_mail_marked_read", {
            providerMailId: selected.providerMailId,
            localMailId: localMail?.id ?? null
        });
    }
    return selected;
}

async function waitForVerificationCode(
    mailbox: MailboxRow,
    targetEmail: string,
    sinceMs: number,
    timeoutMs: number,
    ignoredProviderMailIds: Set<string> = new Set(),
    progress?: OpenAiAuthProgress
): Promise<VerificationCodeCandidate> {
    const deadline = Date.now() + timeoutMs;
    let lastLogAt = 0;
    let lastError: unknown = null;
    logProgress(progress, "otp_wait_start", {mailboxEmail: mailbox.email, sinceMs, timeoutMs});
    while (Date.now() < deadline) {
        try {
            const candidate = await readLatestVerificationCode(mailbox, targetEmail, sinceMs, ignoredProviderMailIds, progress);
            if (candidate) {
                logProgress(progress, "otp_found", {
                    mailboxEmail: mailbox.email,
                    providerMailId: candidate.providerMailId,
                    codeSuffix: candidate.code.slice(-2)
                });
                return candidate;
            }
        } catch (error) {
            lastError = error;
            logProgress(progress, "otp_poll_failed", {
                mailboxEmail: mailbox.email,
                error: error instanceof Error ? error.message : String(error),
                remainingMs: Math.max(0, deadline - Date.now())
            }, "warn");
        }
        if (Date.now() - lastLogAt > 15_000) {
            lastLogAt = Date.now();
            logProgress(progress, "otp_waiting", {remainingMs: Math.max(0, deadline - Date.now())});
        }
        await sleep(5_000);
    }
    if (lastError) {
        throw new Error(`等待 OpenAI 邮箱验证码超时；最后一次拉取邮箱失败：${lastError instanceof Error ? lastError.message : String(lastError)}`);
    }
    throw new Error("等待 OpenAI 邮箱验证码超时");
}

class CookieJar {
    private readonly cookies = new Map<string, CookieEntry>();

    header(url: string): string {
        const target = new URL(url);
        const path = target.pathname || "/";
        const values: string[] = [];
        for (const cookie of this.cookies.values()) {
            const hostMatches = cookie.domain.startsWith(".")
                ? target.hostname.endsWith(cookie.domain.slice(1))
                : target.hostname === cookie.domain;
            if (!hostMatches || !path.startsWith(cookie.path)) continue;
            values.push(`${cookie.name}=${cookie.value}`);
        }
        return values.join("; ");
    }

    store(url: string, headers: Headers): void {
        const target = new URL(url);
        const values = typeof headers.getSetCookie === "function"
            ? headers.getSetCookie()
            : ( headers.get("set-cookie") ? [headers.get("set-cookie") as string] : [] );
        for (const value of values) {
            const parts = value.split(";").map((item) => item.trim()).filter(Boolean);
            const first = parts.shift();
            if (!first) continue;
            const index = first.indexOf("=");
            if (index <= 0) continue;
            const name = first.slice(0, index);
            const cookieValue = first.slice(index + 1);
            let domain = target.hostname;
            let path = "/";
            for (const part of parts) {
                const [rawKey, ...rawValue] = part.split("=");
                const key = rawKey.toLowerCase();
                const attrValue = rawValue.join("=");
                if (key === "domain" && attrValue) domain = attrValue.toLowerCase();
                if (key === "path" && attrValue) path = attrValue;
            }
            this.cookies.set(`${domain}|${path}|${name}`, {name, value: cookieValue, domain, path});
        }
    }

    get(name: string): string | null {
        for (const cookie of this.cookies.values()) {
            if (cookie.name === name) return cookie.value;
        }
        return null;
    }
}

class OpenAiAuthClient {
    private readonly jar = new CookieJar();

    constructor(private readonly network: SystemNetworkSettings) {
    }

    async request(url: string, options: RequestOptions = {}): Promise<NetworkFetchResponse> {
        const headers = {
            ...this.baseHeaders(url, options.referer),
            ...options.headers
        };
        const cookie = this.jar.header(url);
        if (cookie) headers.cookie = cookie;
        const body = options.body === undefined
            ? undefined
            : typeof options.body === "string" ? options.body : JSON.stringify(options.body);
        const response = await requestWithNetworkOptions(url, {
            method: options.method ?? "GET",
            headers,
            body
        }, this.network);
        this.jar.store(url, response.headers);
        const location = response.headers.get("location");
        const redirectLimit = options.redirectLimit ?? 0;
        if (location && response.status >= 300 && response.status < 400 && redirectLimit > 0) {
            const next = new URL(location, url).toString();
            return this.request(next, {
                method: "GET",
                referer: url,
                redirectLimit: redirectLimit - 1
            });
        }
        return response;
    }

    private baseHeaders(url: string, referer?: string): Record<string, string> {
        const endpoint = new URL(url);
        return {
            accept: "application/json,text/plain,*/*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            origin: endpoint.origin,
            "user-agent": USER_AGENT,
            "sec-ch-ua": SEC_CH_UA,
            "sec-ch-ua-arch": "\"x86_64\"",
            "sec-ch-ua-bitness": "\"64\"",
            "sec-ch-ua-full-version-list": SEC_CH_UA_FULL,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-model": "\"\"",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-ch-ua-platform-version": "\"10.0.0\"",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            ...( referer ? {referer} : {} ),
            ...makeTraceHeaders()
        };
    }

    cookie(name: string): string | null {
        return this.jar.get(name);
    }
}

function responseText(response: NetworkFetchResponse): string {
    return response.body.toString("utf8");
}

function responseJson(response: NetworkFetchResponse): unknown {
    return jsonOrNull(responseText(response));
}

function callbackParamsFromUrl(url: string): { code: string; state: string; scope: string } | null {
    try {
        const parsed = new URL(url);
        const code = parsed.searchParams.get("code")?.trim() ?? "";
        if (!code) return null;
        return {
            code,
            state: parsed.searchParams.get("state")?.trim() ?? "",
            scope: parsed.searchParams.get("scope")?.trim() ?? ""
        };
    } catch {
        return null;
    }
}

function containsPhoneRequirement(value: unknown): boolean {
    const text = typeof value === "string" ? value : JSON.stringify(value);
    return /add[-_ ]?phone|phone[_-]?verification|phone[_-]?number|phone_required|绑定手机号|手机/i.test(text);
}

function safeUrlSummary(value?: string | null): Record<string, unknown> {
    const raw = value?.trim() ?? "";
    if (!raw) return {};
    try {
        const url = new URL(raw, AUTH_BASE);
        return {
            host: url.host,
            path: url.pathname,
            hasCode: Boolean(url.searchParams.get("code")),
            hasState: Boolean(url.searchParams.get("state"))
        };
    } catch {
        return {raw: raw.slice(0, 120)};
    }
}

function phoneRequirementHint(value: unknown): string {
    const text = typeof value === "string" ? value : JSON.stringify(value);
    const match = /add[-_ ]?phone|phone[_-]?verification|phone[_-]?number|phone_required|绑定手机号|手机/i.exec(text);
    return match?.[ 0 ] ?? "";
}

function isUnsupportedChatGptRegionResponse(response: NetworkFetchResponse): boolean {
    const body = responseText(response).slice(0, 200_000);
    return response.status === 403 ||
        /not available|unsupported (country|region)|not supported in (your )?(country|region)|services are not available|access denied|地区|区域|所在国家|不支持|不可用/i.test(body);
}

async function assertChatGptRegionAvailable(client: OpenAiAuthClient, progress?: OpenAiAuthProgress): Promise<void> {
    logProgress(progress, "chatgpt_region_check_start");
    let response: NetworkFetchResponse;
    try {
        response = await client.request(CHATGPT_BASE, {
            referer: CHATGPT_BASE,
            redirectLimit: 3
        });
    } catch (error) {
        throw new Error(`ChatGPT 代理检测失败：无法通过当前系统代理访问 chatgpt.com，${error instanceof Error ? error.message : String(error)}`);
    }
    if (isUnsupportedChatGptRegionResponse(response)) {
        throw new Error("ChatGPT 代理检测失败：当前代理出口地区不支持访问 ChatGPT，请更换可访问 ChatGPT 的代理后再登录或注册");
    }
    if (response.status >= 500) {
        throw new Error(`ChatGPT 代理检测失败：chatgpt.com 返回 HTTP ${response.status}，请稍后重试或更换代理`);
    }
    logProgress(progress, "chatgpt_region_check_success", {status: response.status});
}

function decodeBase64UrlJson(value: string): Record<string, unknown> | null {
    try {
        const padded = value + "=".repeat(( 4 - value.length % 4 ) % 4);
        const parsed = JSON.parse(Buffer.from(padded.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8"));
        return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
    } catch {
        return null;
    }
}

function firstWorkspaceId(clientSession: string | null): string {
    const firstPart = clientSession?.split(".")[ 0 ] ?? "";
    const payload = firstPart ? decodeBase64UrlJson(firstPart) : null;
    const workspaces = Array.isArray(payload?.workspaces) ? payload.workspaces : [];
    const first = workspaces[ 0 ];
    if (!first || typeof first !== "object" || Array.isArray(first)) return "";
    const id = ( first as Record<string, unknown> ).id;
    return typeof id === "string" ? id : "";
}

class SentinelTokenGenerator {
    private readonly sid = randomUUID();

    private static fnv1a32(text: string): string {
        let hash = 2166136261;
        for (const char of text) {
            hash ^= char.charCodeAt(0);
            hash = Math.imul(hash, 16777619) >>> 0;
        }
        hash ^= hash >>> 16;
        hash = Math.imul(hash, 2246822507) >>> 0;
        hash ^= hash >>> 13;
        hash = Math.imul(hash, 3266489909) >>> 0;
        hash ^= hash >>> 16;
        return ( hash >>> 0 ).toString(16).padStart(8, "0");
    }

    private config(counter = 1, elapsedMs = Math.round(Math.random() * 50) + 5): unknown[] {
        const perf = Math.random() * 49_000 + 1_000;
        return [
            "1920x1080",
            new Date().toString(),
            4294705152,
            counter,
            USER_AGENT,
            "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js",
            null,
            null,
            "en-US",
            elapsedMs,
            "hardwareConcurrency-undefined",
            "documentURI",
            "Object",
            perf,
            this.sid,
            "",
            8,
            Date.now() - perf
        ];
    }

    private b64(data: unknown): string {
        return Buffer.from(JSON.stringify(data, null, 0)).toString("base64");
    }

    requirementsToken(): string {
        return `gAAAAAC${this.b64(this.config())}`;
    }

    proofToken(seed: string, difficulty: string): string {
        const startedAt = Date.now();
        const target = difficulty || "0";
        for (let i = 0; i < 500_000; i += 1) {
            const payload = this.b64(this.config(i, Date.now() - startedAt));
            if (SentinelTokenGenerator.fnv1a32(seed + payload).slice(0, target.length) <= target) {
                return `gAAAAAB${payload}~S`;
            }
        }
        return this.requirementsToken();
    }
}

async function buildSentinelToken(
    client: OpenAiAuthClient,
    deviceId: string,
    flow: string
): Promise<string> {
    const generator = new SentinelTokenGenerator();
    const response = await client.request("https://sentinel.openai.com/backend-api/sentinel/req", {
        method: "POST",
        headers: {
            "content-type": "text/plain;charset=UTF-8",
            origin: "https://sentinel.openai.com"
        },
        body: JSON.stringify({
            p: generator.requirementsToken(),
            id: deviceId,
            flow
        }),
        referer: "https://sentinel.openai.com/backend-api/sentinel/frame.html"
    });
    const body = responseJson(response);
    if (response.status !== 200 || !body || typeof body !== "object") {
        throw new Error(`sentinel token 获取失败：HTTP ${response.status}`);
    }
    const record = body as Record<string, unknown>;
    const token = typeof record.token === "string" ? record.token : "";
    if (!token) throw new Error("sentinel token 响应为空");
    const pow = record.proofofwork && typeof record.proofofwork === "object"
        ? record.proofofwork as Record<string, unknown>
        : {};
    const required = Boolean(pow.required);
    const seed = typeof pow.seed === "string" ? pow.seed : "";
    const difficulty = typeof pow.difficulty === "string" ? pow.difficulty : "0";
    return JSON.stringify({
        p: required && seed ? generator.proofToken(seed, difficulty) : generator.requirementsToken(),
        t: "",
        c: token,
        id: deviceId,
        flow
    });
}

async function extractCallbackParams(
    client: OpenAiAuthClient,
    continueUrl: string,
    deviceId: string,
    progress?: OpenAiAuthProgress,
    options: { fallbackOnPhoneStep?: boolean; logPrefix?: string } = {}
): Promise<{ code: string; state: string; scope: string } | null> {
    const logPrefix = options.logPrefix ?? "oauth";
    const directParams = callbackParamsFromUrl(continueUrl);
    if (directParams) {
        logProgress(progress, `${logPrefix}_callback_direct`, safeUrlSummary(continueUrl));
        return directParams;
    }
    if (options.fallbackOnPhoneStep && containsPhoneRequirement(continueUrl)) {
        throw new Sub2AuthBranchFallbackError(`Sub2 授权登录遇到 add-phone 步骤：${phoneRequirementHint(continueUrl) || "continueUrl"}`);
    }
    let current = new URL(continueUrl || "/sign-in-with-chatgpt/codex/consent", AUTH_BASE).toString();
    for (let i = 0; i < 12; i += 1) {
        const response = await client.request(current, {
            referer: AUTH_BASE,
            redirectLimit: 0
        });
        const location = response.headers.get("location");
        const finalUrl = response.headers.get("x-final-url") ?? "";
        const body = responseText(response);
        const bodyJson = jsonOrNull(body);
        const responseContinueUrl = stringField(asRecord(bodyJson), "continue_url");
        const responseContinueAbsoluteUrl = responseContinueUrl ? new URL(responseContinueUrl, current).toString() : "";
        logProgress(progress, `${logPrefix}_redirect_step`, {
            index: i,
            status: response.status,
            current: safeUrlSummary(current),
            location: safeUrlSummary(location),
            finalUrl: safeUrlSummary(finalUrl),
            bodyContinueUrl: safeUrlSummary(responseContinueAbsoluteUrl),
            contentType: response.headers.get("content-type") ?? "",
            bodyHint: body.slice(0, 160).replace(/\s+/g, " ")
        });
        const phoneHint = phoneRequirementHint(current) ||
            phoneRequirementHint(location ?? "") ||
            phoneRequirementHint(responseContinueAbsoluteUrl) ||
            phoneRequirementHint(body);
        if (options.fallbackOnPhoneStep && phoneHint) {
            logProgress(progress, `${logPrefix}_phone_step_detected`, {
                index: i,
                status: response.status,
                hint: phoneHint,
                current: safeUrlSummary(current),
                location: safeUrlSummary(location),
                bodyContinueUrl: safeUrlSummary(responseContinueAbsoluteUrl)
            }, "warn");
            throw new Sub2AuthBranchFallbackError(`Sub2 授权登录遇到 add-phone 步骤：${phoneHint}`);
        }
        const params = callbackParamsFromUrl(finalUrl) ||
            ( location ? callbackParamsFromUrl(new URL(location, current).toString()) : null ) ||
            ( responseContinueAbsoluteUrl ? callbackParamsFromUrl(responseContinueAbsoluteUrl) : null );
        if (params) {
            logProgress(progress, `${logPrefix}_callback_from_redirect`, {
                index: i,
                source: finalUrl ? "finalUrl" : location ? "location" : "bodyContinueUrl"
            });
            return params;
        }
        const nextUrl = location ? new URL(location, current).toString() : responseContinueAbsoluteUrl;
        if (!nextUrl || ( !responseContinueAbsoluteUrl && ( response.status < 300 || response.status >= 400 ) )) {
            logProgress(progress, `${logPrefix}_redirect_stopped`, {
                index: i,
                status: response.status,
                hasLocation: Boolean(location),
                hasBodyContinueUrl: Boolean(responseContinueAbsoluteUrl)
            }, "warn");
            break;
        }
        current = nextUrl;
    }
    const workspaceId = firstWorkspaceId(client.cookie("oai-client-auth-session"));
    if (!workspaceId) {
        logProgress(progress, `${logPrefix}_callback_failed_no_workspace`, {}, "warn");
        return null;
    }
    logProgress(progress, `${logPrefix}_workspace_select_start`, {workspaceId});
    const workspace = await client.request(`${AUTH_BASE}/api/accounts/workspace/select`, {
        method: "POST",
        body: {workspace_id: workspaceId},
        referer: current,
        headers: {
            "oai-device-id": deviceId
        }
    });
    const workspaceLocation = workspace.headers.get("location");
    logProgress(progress, `${logPrefix}_workspace_select_response`, {
        status: workspace.status,
        location: safeUrlSummary(workspaceLocation)
    });
    if (workspaceLocation) {
        const params = callbackParamsFromUrl(new URL(workspaceLocation, AUTH_BASE).toString());
        if (params) {
            logProgress(progress, `${logPrefix}_callback_from_workspace`);
            return params;
        }
    }
    const workspaceBody = responseJson(workspace);
    const root = workspaceBody && typeof workspaceBody === "object" ? workspaceBody as Record<string, unknown> : {};
    const data = root.data && typeof root.data === "object" && !Array.isArray(root.data) ? root.data as Record<string, unknown> : {};
    const orgs = Array.isArray(data.orgs) ? data.orgs : [];
    const firstOrg = orgs[ 0 ];
    if (!firstOrg || typeof firstOrg !== "object" || Array.isArray(firstOrg)) {
        logProgress(progress, `${logPrefix}_callback_failed_no_org`, {
            workspaceKeys: Object.keys(root).slice(0, 12)
        }, "warn");
        return null;
    }
    const orgRecord = firstOrg as Record<string, unknown>;
    const orgId = typeof orgRecord.id === "string" ? orgRecord.id : "";
    const projects = Array.isArray(orgRecord.projects) ? orgRecord.projects : [];
    const firstProject = projects[ 0 ];
    const projectId = firstProject && typeof firstProject === "object" && !Array.isArray(firstProject)
        ? String(( firstProject as Record<string, unknown> ).id ?? "")
        : "";
    if (!orgId) {
        logProgress(progress, `${logPrefix}_callback_failed_no_org_id`, {}, "warn");
        return null;
    }
    logProgress(progress, `${logPrefix}_organization_select_start`, {orgId, projectId});
    const organization = await client.request(`${AUTH_BASE}/api/accounts/organization/select`, {
        method: "POST",
        body: {
            org_id: orgId,
            ...( projectId ? {project_id: projectId} : {} )
        },
        referer: current,
        headers: {
            "oai-device-id": deviceId
        }
    });
    const organizationLocation = organization.headers.get("location");
    logProgress(progress, `${logPrefix}_organization_select_response`, {
        status: organization.status,
        location: safeUrlSummary(organizationLocation)
    });
    const organizationParams = organizationLocation ? callbackParamsFromUrl(new URL(organizationLocation, AUTH_BASE).toString()) : null;
    if (organizationParams) {
        logProgress(progress, `${logPrefix}_callback_from_organization`);
        return organizationParams;
    }
    logProgress(progress, `${logPrefix}_callback_failed_no_code`, {}, "warn");
    return null;
}

async function registerPassword(
    client: OpenAiAuthClient,
    deviceId: string,
    email: string,
    password: string,
    duckAddressId: number,
    progress?: OpenAiAuthProgress
): Promise<void> {
    logProgress(progress, "register_password_start");
    let response = await client.request(`${AUTH_BASE}/api/accounts/user/register`, {
        method: "POST",
        body: {
            username: email,
            password
        },
        referer: `${AUTH_BASE}/create-account/password`,
        headers: {
            "oai-device-id": deviceId,
            "openai-sentinel-token": await buildSentinelToken(client, deviceId, "username_password_create")
        }
    });
    const body = responseJson(response);
    if (response.status !== 200) {
        throw new Error(`OpenAI 注册密码提交失败：${bodyError(body, response.statusText || `HTTP ${response.status}`)}`);
    }
    setDuckAddressOpenAiPassword(duckAddressId, password);
    logProgress(progress, "register_password_success", {status: response.status});
}

async function verifyPassword(
    client: OpenAiAuthClient,
    deviceId: string,
    email: string,
    password: string,
    progress?: OpenAiAuthProgress
): Promise<PasswordVerifyResult> {
    logProgress(progress, "password_verify_start");
    const response = await client.request(`${AUTH_BASE}/api/accounts/password/verify`, {
        method: "POST",
        body: {password},
        referer: `${AUTH_BASE}/log-in/password`,
        headers: {
            "oai-device-id": deviceId,
            "openai-sentinel-token": await buildSentinelToken(client, deviceId, "password_verify")
        }
    });
    const body = responseJson(response);
    if (response.status !== 200) {
        throw new Error(`OpenAI 密码校验失败：${bodyError(body, response.statusText || `HTTP ${response.status}`)}`);
    }
    logProgress(progress, "password_verify_success", {status: response.status, ...summarizeAuthBody(body)});
    const nextUrl = continueUrl(body);
    const nextPageType = pageType(body);
    const requiresEmailOtp = !requiresAccountProfile(nextPageType) &&
        ( requiresEmailOtpStep(body) || !callbackParamsFromUrl(nextUrl || "") );
    let otpRequestedAtMs: number | undefined;
    if (requiresEmailOtp) {
        otpRequestedAtMs = Date.now();
        await sendEmailOtp(client, deviceId, nextUrl || `${AUTH_BASE}/email-verification`, progress);
    }
    return {
        nextUrl: nextUrl || `${AUTH_BASE}/sign-in-with-chatgpt/codex/consent`,
        pageType: nextPageType,
        requiresEmailOtp,
        otpRequestedAtMs
    };
}

async function sendEmailOtp(
    client: OpenAiAuthClient,
    deviceId: string,
    referer: string,
    progress?: OpenAiAuthProgress
): Promise<void> {
    logProgress(progress, "otp_send_start", {referer});
    let sendOtp = await client.request(`${AUTH_BASE}/api/accounts/email-otp/send`, {
        referer,
        headers: {
            "oai-device-id": deviceId
        }
    });
    let sendBody = responseJson(sendOtp);
    if (sendOtp.status !== 200 && sendOtp.status !== 302) {
        logProgress(progress, "otp_send_retry_with_sentinel", {
            status: sendOtp.status,
            ...summarizeAuthBody(sendBody)
        }, "warn");
        sendOtp = await client.request(`${AUTH_BASE}/api/accounts/email-otp/send`, {
            referer,
            headers: {
                "oai-device-id": deviceId,
                "openai-sentinel-token": await buildSentinelToken(client, deviceId, "authorize_continue")
            }
        });
        sendBody = responseJson(sendOtp);
    }
    if (sendOtp.status !== 200 && sendOtp.status !== 302) {
        throw new Error(`OpenAI 发送验证码失败：${bodyError(sendBody, sendOtp.statusText || `HTTP ${sendOtp.status}`)}`);
    }
    logProgress(progress, "otp_send_success", {status: sendOtp.status});
}

async function createAccountProfile(
    client: OpenAiAuthClient,
    deviceId: string,
    progress?: OpenAiAuthProgress
): Promise<string> {
    const profile = randomProfile();
    logProgress(progress, "create_account_profile_start", {birthdate: profile.birthdate});
    const response = await client.request(`${AUTH_BASE}/api/accounts/create_account`, {
        method: "POST",
        body: profile,
        referer: `${AUTH_BASE}/about-you`,
        headers: {
            "oai-device-id": deviceId,
            "openai-sentinel-token": await buildSentinelToken(client, deviceId, "oauth_create_account")
        }
    });
    const body = responseJson(response);
    if (response.status !== 200 && response.status !== 302) {
        throw new Error(`OpenAI 创建账号资料失败：${bodyError(body, response.statusText || `HTTP ${response.status}`)}`);
    }
    const nextUrl = continueUrl(body);
    logProgress(progress, "create_account_profile_success", {
        status: response.status,
        continueUrl: nextUrl
    });
    return nextUrl;
}

async function exchangeTokens(
    client: OpenAiAuthClient,
    code: string,
    codeVerifier: string
): Promise<OpenAiOAuthSub2Input> {
    const body = new URLSearchParams({
        grant_type: "authorization_code",
        code,
        redirect_uri: PLATFORM_REDIRECT_URI,
        client_id: PLATFORM_CLIENT_ID,
        code_verifier: codeVerifier
    }).toString();
    const response = await client.request(`${AUTH_BASE}/oauth/token`, {
        method: "POST",
        headers: {
            "content-type": "application/x-www-form-urlencoded"
        },
        body,
        referer: PLATFORM_BASE
    });
    const data = responseJson(response);
    if (response.status !== 200 || !data || typeof data !== "object") {
        throw new Error(`OpenAI token 换取失败：${bodyError(data, response.statusText || `HTTP ${response.status}`)}`);
    }
    const record = data as Record<string, unknown>;
    const accessToken = typeof record.access_token === "string" ? record.access_token : "";
    const refreshToken = typeof record.refresh_token === "string" ? record.refresh_token : "";
    const idToken = typeof record.id_token === "string" ? record.id_token : "";
    if (!accessToken) throw new Error("OpenAI token 响应缺少 access_token");
    const payload = decodeJwtPayload(idToken) || decodeJwtPayload(accessToken);
    const now = Math.floor(Date.now() / 1000);
    const expiresAt = typeof record.expires_in === "number"
        ? new Date(( now + record.expires_in ) * 1000).toISOString()
        : typeof payload.exp === "number" ? new Date(payload.exp * 1000).toISOString() : undefined;
    return {
        email: typeof payload.email === "string" ? payload.email : "",
        accessToken,
        refreshToken,
        idToken,
        expiresAt,
        userId: typeof payload.sub === "string" ? payload.sub : undefined,
        accountId: typeof payload[ "https://api.openai.com/account_id" ] === "string"
            ? String(payload[ "https://api.openai.com/account_id" ])
            : undefined,
        planType: "free"
    };
}

async function validateEmailOtp(
    client: OpenAiAuthClient,
    deviceId: string,
    code: string,
    progress?: OpenAiAuthProgress
): Promise<{ nextUrl: string; pageType: string }> {
    logProgress(progress, "otp_validate_start");
    let validate = await client.request(`${AUTH_BASE}/api/accounts/email-otp/validate`, {
        method: "POST",
        body: {code},
        referer: `${AUTH_BASE}/email-verification`,
        headers: {
            "oai-device-id": deviceId
        }
    });
    if (validate.status !== 200) {
        logProgress(progress, "otp_validate_retry_with_sentinel", {status: validate.status}, "warn");
        validate = await client.request(`${AUTH_BASE}/api/accounts/email-otp/validate`, {
            method: "POST",
            body: {code},
            referer: `${AUTH_BASE}/email-verification`,
            headers: {
                "oai-device-id": deviceId,
                "openai-sentinel-token": await buildSentinelToken(client, deviceId, "authorize_continue")
            }
        });
    }
    const validateBody = responseJson(validate);
    if (validate.status !== 200) {
        throw new Error(`OpenAI 验证码校验失败：${bodyError(validateBody, validate.statusText || `HTTP ${validate.status}`)}`);
    }
    const nextUrl = validateBody && typeof validateBody === "object"
        ? String(( validateBody as Record<string, unknown> ).continue_url ?? "")
        : "";
    const nextPageType = pageType(validateBody);
    logProgress(progress, "otp_validate_success", {pageType: nextPageType, hasContinueUrl: Boolean(nextUrl)});
    return {nextUrl, pageType: nextPageType};
}

async function waitAndValidateEmailOtp(
    client: OpenAiAuthClient,
    deviceId: string,
    mailbox: MailboxRow,
    targetEmail: string,
    sinceMs: number,
    timeoutMs: number,
    progress?: OpenAiAuthProgress
): Promise<{ nextUrl: string; pageType: string }> {
    const ignoredProviderMailIds = new Set<string>();
    const deadline = Date.now() + timeoutMs;
    let lastError: unknown = null;
    while (Date.now() < deadline) {
        const remainingMs = Math.max(1_000, deadline - Date.now());
        const candidate = await waitForVerificationCode(
            mailbox,
            targetEmail,
            sinceMs,
            remainingMs,
            ignoredProviderMailIds,
            progress
        );
        try {
            return await validateEmailOtp(client, deviceId, candidate.code, progress);
        } catch (error) {
            lastError = error;
            ignoredProviderMailIds.add(candidate.providerMailId);
            logProgress(progress, "otp_validate_failed_try_next", {
                providerMailId: candidate.providerMailId,
                codeSuffix: candidate.code.slice(-2),
                remainingMs: Math.max(0, deadline - Date.now()),
                error: error instanceof Error ? error.message : String(error)
            }, "warn");
        }
    }
    throw new Error(`OpenAI 验证码校验失败，已尝试所有新验证码；最后错误：${lastError instanceof Error ? lastError.message : String(lastError)}`);
}

async function loginWithEmailOtp(
    duckAddress: DuckAddressRow,
    targetEmail: string,
    inboxMailbox: MailboxRow,
    logger?: OpenAiAuthLogger
): Promise<OpenAiLoginResult> {
    const progress: OpenAiAuthProgress = {
        logger,
        operationId: createOperationId(),
        startedAt: Date.now(),
        email: targetEmail,
        inboxEmail: inboxMailbox.email
    };
    const network = getSystemNetworkSettings();
    const client = new OpenAiAuthClient(network);
    const deviceId = randomUUID();
    const {verifier, challenge} = generatePkce();
    await assertChatGptRegionAvailable(client, progress);
    logProgress(progress, "authorize_start");
    const authorizeUrl = new URL(`${AUTH_BASE}/api/accounts/authorize`);
    authorizeUrl.search = new URLSearchParams({
        issuer: AUTH_BASE,
        client_id: PLATFORM_CLIENT_ID,
        audience: PLATFORM_AUDIENCE,
        redirect_uri: PLATFORM_REDIRECT_URI,
        device_id: deviceId,
        screen_hint: "login_or_signup",
        max_age: "0",
        login_hint: targetEmail,
        scope: "openid profile email offline_access",
        response_type: "code",
        response_mode: "query",
        state: randomBytes(24).toString("base64url"),
        nonce: randomBytes(24).toString("base64url"),
        code_challenge: challenge,
        code_challenge_method: "S256",
        auth0Client: PLATFORM_AUTH0_CLIENT
    }).toString();
    const authorize = await client.request(authorizeUrl.toString(), {
        referer: `${PLATFORM_BASE}/`,
        redirectLimit: 8
    });
    if (authorize.status >= 400) {
        throw new Error(`OpenAI authorize 失败：${bodyError(responseJson(authorize), authorize.statusText || `HTTP ${authorize.status}`)}`);
    }
    const authorizeBody = responseJson(authorize);
    const authContinueUrl = continueUrl(authorizeBody);
    const authPageType = pageType(authorizeBody);
    logProgress(progress, "authorize_success", {
        status: authorize.status,
        ...summarizeAuthBody(authorizeBody)
    });

    if (authPageType === "create_account_password") {
        await registerPassword(client, deviceId, targetEmail, randomPassword(), duckAddress.id, progress);
    } else if (authPageType === "login_password") {
        const password = duckAddress.openai_password?.trim() ?? "";
        if (!password) {
            throw new Error("该 Duck 邮箱已注册 OpenAI 账号，但本地没有保存密码；请换一个新的 Duck 邮箱重新推送，或手动找回密码后再处理");
        }
        const passwordResult = await verifyPassword(client, deviceId, targetEmail, password, progress);
        let nextUrl = passwordResult.nextUrl;
        if (requiresAccountProfile(passwordResult.pageType)) {
            nextUrl = await createAccountProfile(client, deviceId, progress);
        } else if (passwordResult.requiresEmailOtp) {
            const validated = await waitAndValidateEmailOtp(
                client,
                deviceId,
                inboxMailbox,
                targetEmail,
                passwordResult.otpRequestedAtMs ?? Date.now(),
                network.openAiOtpTimeoutMs,
                progress
            );
            nextUrl = requiresAccountProfile(validated.pageType)
                ? await createAccountProfile(client, deviceId, progress)
                : validated.nextUrl || nextUrl;
        }
        const callback = await extractCallbackParams(client, nextUrl, deviceId, progress);
        if (!callback) {
            throw new Error("OpenAI 密码登录成功后未拿到 OAuth callback code");
        }
        logProgress(progress, "oauth_callback_success");
        const token = await exchangeTokens(client, callback.code, verifier);
        token.email = token.email || normalizeEmail(targetEmail);
        setDuckAddressOpenAiAuthJson(duckAddress.id, serializeOpenAiAuthJson(token));
        return {token, client, deviceId};
    } else if (authPageType && !/email|otp|verification|login|signup|identifier/i.test(authPageType)) {
        throw new Error(`OpenAI authorize 当前步骤不支持自动邮箱验证码：${authPageType}`);
    }
    if (authContinueUrl) {
        logProgress(progress, "authorize_continue_open", {continueUrl: authContinueUrl});
        const continued = await client.request(new URL(authContinueUrl, AUTH_BASE).toString(), {
            referer: authorizeUrl.toString(),
            redirectLimit: 4
        });
        const continuedBody = responseJson(continued);
        logProgress(progress, "authorize_continue_success", {
            status: continued.status,
            ...summarizeAuthBody(continuedBody)
        });
    }

    const otpRequestedAtMs = Date.now();
    await sendEmailOtp(client, deviceId, authContinueUrl ? new URL(authContinueUrl, AUTH_BASE).toString() : `${AUTH_BASE}/email-verification`, progress);

    const validated = await waitAndValidateEmailOtp(
        client,
        deviceId,
        inboxMailbox,
        targetEmail,
        otpRequestedAtMs,
        network.openAiOtpTimeoutMs,
        progress
    );
    const nextUrl = requiresAccountProfile(validated.pageType)
        ? await createAccountProfile(client, deviceId, progress)
        : validated.nextUrl;

    const callback = await extractCallbackParams(client, nextUrl || validated.nextUrl, deviceId, progress);
    if (!callback) {
        throw new Error("OpenAI 登录成功后未拿到 OAuth callback code");
    }
    logProgress(progress, "oauth_callback_success");

    const token = await exchangeTokens(client, callback.code, verifier);
    token.email = token.email || normalizeEmail(targetEmail);
    if (normalizeEmail(token.email) !== normalizeEmail(targetEmail)) {
        throw new Error(`OpenAI 登录账号 ${token.email} 与目标 Duck 邮箱 ${targetEmail} 不一致`);
    }
    setDuckAddressOpenAiAuthJson(duckAddress.id, serializeOpenAiAuthJson(token));
    logProgress(progress, "token_exchange_success", {tokenEmail: token.email});
    return {token, client, deviceId};
}

async function authorizeSub2AuthLoginWithCurrentSession(
    request: Sub2AuthLoginRequest,
    login: OpenAiLoginResult,
    progress?: OpenAiAuthProgress
): Promise<Sub2AuthLoginCallback> {
    logProgress(progress, "sub2_auth_branch_authorize_start", {
        sessionId: request.sessionId,
        authUrlHost: new URL(request.authUrl).host,
        proxyId: request.proxyId
    });
    const callback = await extractCallbackParams(
        login.client,
        request.authUrl,
        login.deviceId,
        progress,
        {fallbackOnPhoneStep: true, logPrefix: "sub2_auth_branch_oauth"}
    );
    if (!callback) {
        throw new Sub2AuthBranchFallbackError("Sub2 授权登录未拿到 OAuth callback code");
    }
    if (containsPhoneRequirement(callback)) {
        throw new Sub2AuthBranchFallbackError("Sub2 授权登录遇到 add-phone 步骤");
    }
    logProgress(progress, "sub2_auth_branch_callback_success", {
        sessionId: request.sessionId,
        hasState: Boolean(callback.state),
        scope: callback.scope
    });
    return callback;
}

async function pushPreparedOpenAiAccountToSub2(
    login: OpenAiLoginResult,
    data: Sub2DataPayload,
    groupId: number | null | undefined,
    progress: OpenAiAuthProgress
): Promise<{
    data: Sub2DataPayload;
    response: unknown;
    pushMode: Sub2PushMode;
    fallbackReason?: string;
}> {
    try {
        logProgress(progress, "sub2_auth_branch_start");
        const result = await pushSub2DataViaAuthLogin(data, groupId, (request) =>
            authorizeSub2AuthLoginWithCurrentSession(request, login, progress)
        );
        logProgress(progress, "sub2_auth_branch_success");
        return {...result, pushMode: "sub2_auth"};
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (!isSub2AuthBranchFallbackError(error)) {
            throw error;
        }
        logProgress(progress, "sub2_auth_branch_fallback", {error: message}, "warn");
        const fallback = await pushSub2Data(data, groupId);
        return {
            ...fallback,
            pushMode: "fallback_oauth_token",
            fallbackReason: message
        };
    }
}

function resolveDuckLoginTarget(duckAddressId: number): { duckAddress: DuckAddressRow; inboxMailbox: MailboxRow } {
    const duckAddress = getDuckAddressById(duckAddressId);
    if (!duckAddress || duckAddress.status !== "active") {
        throw new Error("Duck 邮箱记录不存在或已不可用");
    }
    const forwardingEmail = duckAddress.forwarding_mailbox_email?.trim();
    if (!forwardingEmail) {
        throw new Error("Duck 邮箱没有绑定目标 Claw 邮箱，无法读取 OpenAI 验证码");
    }
    const inboxMailbox = getMailboxByEmail(forwardingEmail);
    if (!inboxMailbox || inboxMailbox.status === "deleted") {
        throw new Error("Duck 邮箱绑定的目标 Claw 邮箱不存在或已删除");
    }
    // 提前创建客户端，尽早暴露 Claw API Key 配置问题。
    getMailClient(inboxMailbox.email, inboxMailbox.connection_id);
    return {duckAddress, inboxMailbox};
}

export async function pushOpenAiDuckAddressToSub2(
    duckAddressId: number,
    groupId?: number | null,
    logger?: OpenAiAuthLogger
): Promise<OpenAiDuckPushResult> {
    const {duckAddress, inboxMailbox} = resolveDuckLoginTarget(duckAddressId);
    const login = await loginWithEmailOtp(duckAddress, duckAddress.address, inboxMailbox, logger);
    const token = login.token;
    const data = convertOpenAiOAuthToSub2(token);
    const progress: OpenAiAuthProgress = {
        logger,
        operationId: createOperationId(),
        startedAt: Date.now(),
        email: duckAddress.address,
        inboxEmail: inboxMailbox.email
    };
    const result = await pushPreparedOpenAiAccountToSub2(login, data, groupId, progress);
    const telegram = await notifyOpenAiAccessToken(token, logger);
    return {
        email: token.email,
        data: result.data,
        response: result.response,
        pushMode: result.pushMode,
        fallbackReason: result.fallbackReason,
        telegram
    };
}
