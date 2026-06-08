export type FormatConverterOutputFormat =
    | "sub2api"
    | "cpa"
    | "cockpit"
    | "9router"
    | "codex"
    | "axonhub"
    | "codexmanager";

export type SessionSource = {
    value: JsonObject;
    sourceName: string;
    path: string;
};

export type SkippedSession = {
    sourceName: string;
    path?: string;
    reason: string;
};

export type ConvertedSession = {
    sourceName: string;
    sourcePath?: string;
    email?: string;
    name?: string;
    expiresAt?: string;
    accessTokenExpiresAt?: number;
    cpa: JsonObject;
    cockpit: JsonObject;
    nineRouter: JsonObject;
    codexAuthJson: JsonObject;
    axonHub: JsonObject;
    codexManager: JsonObject;
    sub2apiAccount: JsonObject;
};

export type ConversionResult = {
    sources: SessionSource[];
    converted: ConvertedSession[];
    skipped: SkippedSession[];
};

export type ConversionFilePayload = {
    name: string;
    text: string;
};

export type ConversionFileResult = ConversionResult & {
    inputText: string;
    filesRead: number;
};

type JsonPrimitive = string | number | boolean | null;
type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
type JsonObject = {
    [key: string]: JsonValue | undefined;
};

type ConvertOptions = {
    now?: Date;
    sourceName?: string;
    sourcePath?: string;
};

export const FORMAT_LABELS: Record<FormatConverterOutputFormat, string> = {
    sub2api: "sub2api",
    cpa: "CPA",
    cockpit: "Cockpit",
    "9router": "9router",
    codex: "Codex",
    axonhub: "AxonHub",
    codexmanager: "Codex-Manager"
};

export const FORMAT_OPTIONS: Array<{
    value: FormatConverterOutputFormat;
    label: string;
}> = [
    {value: "sub2api", label: FORMAT_LABELS.sub2api},
    {value: "cpa", label: FORMAT_LABELS.cpa},
    {value: "cockpit", label: FORMAT_LABELS.cockpit},
    {value: "9router", label: FORMAT_LABELS["9router"]},
    {value: "codex", label: FORMAT_LABELS.codex},
    {value: "axonhub", label: FORMAT_LABELS.axonhub},
    {value: "codexmanager", label: FORMAT_LABELS.codexmanager}
];

export const EXAMPLE_SESSION = {
    user: {
        id: "user-example",
        email: "mark@example.com"
    },
    expires: "2026-08-06T14:29:36.155Z",
    account: {
        id: "00000000-0000-4000-9000-000000000000",
        planType: "plus"
    },
    accessToken: "paste-real-access-token-here",
    sessionToken: "paste-real-session-token-here",
    authProvider: "openai"
};

const AXONHUB_PLACEHOLDER_REFRESH_TOKEN = "__missing_refresh_token__";

function isPlainObject(value: unknown): value is JsonObject {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function getObject(value: unknown): JsonObject {
    return isPlainObject(value) ? value : {};
}

function getString(value: unknown): string | undefined {
    return typeof value === "string" && value.trim() !== "" ? value.trim() : undefined;
}

function firstNonEmpty(...values: unknown[]): string | undefined {
    for (const value of values) {
        const text = getString(value);
        if (text) return text;
    }
    return undefined;
}

function firstPresent<T>(...values: Array<T | undefined>): T | undefined {
    return values.find((value) => value !== undefined);
}

function getNestedObject(record: JsonObject, key: string): JsonObject {
    return getObject(record[key]);
}

function getTokenField(record: JsonObject, camelName: string, snakeName: string): string | undefined {
    const tokens = getNestedObject(record, "tokens");
    const token = getNestedObject(record, "token");
    const credentials = getNestedObject(record, "credentials");
    return firstNonEmpty(
        record[camelName],
        record[snakeName],
        tokens[camelName],
        tokens[snakeName],
        token[camelName],
        token[snakeName],
        credentials[camelName],
        credentials[snakeName]
    );
}

function decodeBase64Url(value: string): string {
    const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
    const binary = atob(padded);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder().decode(bytes);
}

function bytesToBase64Url(bytes: Uint8Array): string {
    let binary = "";
    for (let index = 0; index < bytes.length; index += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
    }
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function encodeBase64UrlJson(value: JsonObject): string {
    return bytesToBase64Url(new TextEncoder().encode(JSON.stringify(value)));
}

function parseJwtPayload(token?: string): JsonObject | undefined {
    if (!token) return undefined;
    const segments = token.split(".");
    if (segments.length < 2) return undefined;

    try {
        const parsed = JSON.parse(decodeBase64Url(segments[1]));
        return isPlainObject(parsed) ? parsed : undefined;
    } catch {
        return undefined;
    }
}

function getOpenAIAuthSection(payload?: JsonObject): JsonObject {
    if (!payload) return {};
    return getObject(payload["https://api.openai.com/auth"]);
}

function getOpenAIProfileSection(payload?: JsonObject): JsonObject {
    if (!payload) return {};
    return getObject(payload["https://api.openai.com/profile"]);
}

function normalizeTimestamp(value: unknown): string | undefined {
    if (value instanceof Date && !Number.isNaN(value.getTime())) {
        return value.toISOString();
    }

    if (typeof value === "number" && Number.isFinite(value)) {
        const milliseconds = value > 1e11 ? value : value * 1000;
        const date = new Date(milliseconds);
        return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
    }

    if (typeof value !== "string" || value.trim() === "") {
        return undefined;
    }

    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

function timestampFromUnixSeconds(value: unknown): string | undefined {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return undefined;

    const date = new Date(numeric * 1000);
    return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

function unixSecondsFromJwtExp(value: unknown): number | undefined {
    const numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric <= 0) return undefined;
    return Math.trunc(numeric);
}

function epochSecondsFromValue(value: unknown): number {
    if (value === undefined || value === null || value === "") return 0;

    const numeric = Number(value);
    if (Number.isFinite(numeric)) {
        return Math.trunc(numeric > 1e11 ? numeric / 1000 : numeric);
    }

    const parsed = Date.parse(String(value));
    return Number.isFinite(parsed) ? Math.trunc(parsed / 1000) : 0;
}

function buildSyntheticCodexIdToken(
    email: string | undefined,
    accountId: string | undefined,
    planType: string | undefined,
    userId: string | undefined,
    expiresAt: string | undefined
): string | undefined {
    if (!accountId) return undefined;

    const now = Math.trunc(Date.now() / 1000);
    const authInfo: JsonObject = {chatgpt_account_id: accountId};
    const expires = epochSecondsFromValue(expiresAt) || now + 90 * 24 * 60 * 60;

    if (planType) authInfo.chatgpt_plan_type = planType;
    if (userId) {
        authInfo.chatgpt_user_id = userId;
        authInfo.user_id = userId;
    }

    const payload: JsonObject = {
        iat: now,
        exp: expires,
        "https://api.openai.com/auth": authInfo
    };
    if (email) payload.email = email;

    return `${encodeBase64UrlJson({alg: "none", typ: "JWT", cpa_synthetic: true})}.${encodeBase64UrlJson(payload)}.synthetic`;
}

function getExpiresIn(expiresAt: string | undefined, now = new Date()): number | undefined {
    if (!expiresAt) return undefined;

    const expiresMs = new Date(expiresAt).getTime();
    if (Number.isNaN(expiresMs)) return undefined;
    return Math.max(0, Math.floor((expiresMs - now.getTime()) / 1000));
}

function getAxonHubLastRefresh(expiresAt: string | undefined, now = new Date()): string | undefined {
    const expiresMs = expiresAt ? new Date(expiresAt).getTime() : NaN;
    if (Number.isNaN(expiresMs)) return normalizeTimestamp(now);
    return new Date(expiresMs - 60 * 60 * 1000).toISOString();
}

function stripUnavailable(value: unknown): unknown {
    if (Array.isArray(value)) {
        return value.map(stripUnavailable).filter((item) => item !== undefined);
    }

    if (isPlainObject(value)) {
        const entries = Object.entries(value)
            .map(([key, item]) => [key, stripUnavailable(item)] as const)
            .filter(([, item]) => item !== undefined);
        return entries.length ? Object.fromEntries(entries) : undefined;
    }

    if (value === undefined || value === null || value === "") {
        return undefined;
    }

    return value;
}

function toJsonObject(value: unknown): JsonObject {
    return isPlainObject(value) ? value : {};
}

function toEmailKey(email: string | undefined): string | undefined {
    if (!email) return undefined;

    return email
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "");
}

/**
 * 生成可用于下载文件名的安全片段。
 *
 * @param value 原始邮箱、账号名或格式名称。
 * @param fallback 原始值为空或清理后为空时使用的默认名称。
 * @returns 去除非法文件名字符后的短文件名片段。
 */
export function sanitizeFormatConverterFileToken(value: string | undefined, fallback = "chatgpt-session"): string {
    const base = firstNonEmpty(value, fallback) || fallback;
    return base
        .replace(/\.[^.]+$/u, "")
        .replace(/[\\/:*?"<>|]+/g, "-")
        .replace(/\s+/g, "-")
        .replace(/-+/g, "-")
        .replace(/^-+|-+$/g, "")
        .toLowerCase()
        .slice(0, 80) || fallback;
}

/**
 * 生成下载文件名中的时间戳片段。
 *
 * @param date 用于格式化的时间，默认使用当前时间。
 * @returns 形如 yyyy-mm-dd_HH-MM-SS 的时间戳字符串。
 */
export function getFormatConverterTimestampToken(date = new Date()): string {
    const pad = (value: number) => String(value).padStart(2, "0");
    return [
        date.getFullYear(),
        pad(date.getMonth() + 1),
        pad(date.getDate())
    ].join("-") + "_" + [
        pad(date.getHours()),
        pad(date.getMinutes()),
        pad(date.getSeconds())
    ].join("-");
}

/**
 * 把 ISO 时间转换为界面表格中的紧凑展示格式。
 *
 * @param value 待展示的时间字符串。
 * @returns 可读时间；无法解析时保留原始值。
 */
export function formatConverterDisplayDate(value: string | undefined): string {
    if (!value) return "";

    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;

    const pad = (item: number) => String(item).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/**
 * 从任意 JSON 结构中递归收集可转换账号对象。
 *
 * @param value 已解析的 JSON 数据。
 * @param sourceName 数据来源名称，通常是粘贴输入或文件名。
 * @returns 包含 accessToken 且能识别账号身份的对象列表。
 */
export function collectSessionLikeObjects(value: unknown, sourceName = "pasted-json"): SessionSource[] {
    const found: SessionSource[] = [];
    const visited = new WeakSet<object>();

    function visit(item: unknown, path: string) {
        if (!isPlainObject(item) && !Array.isArray(item)) return;

        if (isPlainObject(item)) {
            if (visited.has(item)) return;
            visited.add(item);

            const tokens = getNestedObject(item, "tokens");
            const token = getNestedObject(item, "token");
            const credentials = getNestedObject(item, "credentials");
            const meta = getNestedObject(item, "meta");
            const providerSpecificData = getNestedObject(item, "providerSpecificData");
            const accessToken = firstNonEmpty(
                item.accessToken,
                item.access_token,
                tokens.accessToken,
                tokens.access_token,
                token.accessToken,
                token.access_token,
                credentials.accessToken,
                credentials.access_token
            );
            const hasIdentity = isPlainObject(item.user) || firstNonEmpty(
                item.email,
                item.name,
                item.label,
                meta.label,
                tokens.accountId,
                tokens.account_id,
                tokens.chatgptAccountId,
                tokens.chatgpt_account_id,
                providerSpecificData.chatgptAccountId,
                providerSpecificData.chatgpt_account_id,
                item.id
            );
            if (accessToken && hasIdentity) {
                found.push({value: item, sourceName, path});
                return;
            }

            for (const [key, child] of Object.entries(item)) {
                if (key === "accessToken" || key === "access_token" || key === "sessionToken") {
                    continue;
                }
                visit(child, `${path}.${key}`);
            }
            return;
        }

        item.forEach((child, index) => visit(child, `${path}[${index}]`));
    }

    visit(value, "$");
    return found;
}

/**
 * 解析用户粘贴的 JSON 文本并提取账号对象。
 *
 * @param text 用户输入的 JSON 文本。
 * @returns 可转换账号来源列表。
 * @throws 当 JSON 语法不合法时抛出中文错误。
 */
export function parseInputDocuments(text: string): SessionSource[] {
    if (text.trim() === "") return [];

    let parsed: unknown;
    try {
        parsed = JSON.parse(text);
    } catch (error) {
        const message = error instanceof Error ? error.message : "JSON 解析失败";
        throw new Error(`JSON 解析失败：${message}`);
    }

    return collectSessionLikeObjects(parsed);
}

/**
 * 将单个 ChatGPT/Codex session 对象转换为所有支持的目标格式。
 *
 * @param record 单个 session 或 OAuth JSON 对象。
 * @param options 转换上下文，例如当前时间和来源路径。
 * @returns 同一个账号在 CPA、sub2api、Cockpit、9router、Codex、AxonHub、Codex-Manager 下的结构。
 * @throws 当缺少 accessToken 时抛出错误。
 */
export function convertSession(record: JsonObject, options: ConvertOptions = {}): ConvertedSession {
    const accessToken = getTokenField(record, "accessToken", "access_token");
    if (!accessToken) {
        throw new Error("缺少 accessToken");
    }

    const sessionToken = getTokenField(record, "sessionToken", "session_token");
    const refreshToken = getTokenField(record, "refreshToken", "refresh_token");
    const inputIdToken = getTokenField(record, "idToken", "id_token");
    const user = getNestedObject(record, "user");
    const account = getNestedObject(record, "account");
    const meta = getNestedObject(record, "meta");
    const tokens = getNestedObject(record, "tokens");
    const credentials = getNestedObject(record, "credentials");
    const providerSpecificData = getNestedObject(record, "providerSpecificData");

    const payload = parseJwtPayload(accessToken);
    const idPayload = parseJwtPayload(inputIdToken);
    const auth = getOpenAIAuthSection(payload);
    const idAuth = getOpenAIAuthSection(idPayload);
    const profile = getOpenAIProfileSection(payload);
    const accessTokenExpiresAt = unixSecondsFromJwtExp(payload?.exp);
    const expiresAt = firstPresent(
        payload ? timestampFromUnixSeconds(payload.exp) : undefined,
        normalizeTimestamp(record.expires),
        normalizeTimestamp(record.expiresAt),
        normalizeTimestamp(record.expired),
        normalizeTimestamp(record.expires_at)
    );
    const email = firstNonEmpty(
        user.email,
        record.email,
        meta.label,
        record.label,
        credentials.email,
        providerSpecificData.email,
        profile.email,
        idPayload?.email,
        payload?.email
    );
    const accountId = firstNonEmpty(
        account.id,
        record.account_id,
        tokens.accountId,
        tokens.account_id,
        record.chatgptAccountId,
        record.chatgpt_account_id,
        meta.chatgptAccountId,
        meta.chatgpt_account_id,
        tokens.chatgptAccountId,
        tokens.chatgpt_account_id,
        providerSpecificData.chatgptAccountId,
        providerSpecificData.chatgpt_account_id,
        credentials.chatgpt_account_id,
        auth.chatgpt_account_id,
        idAuth.chatgpt_account_id,
        record.provider === "codex" ? record.id : undefined
    );
    const chatgptAccountId = firstNonEmpty(
        record.chatgptAccountId,
        record.chatgpt_account_id,
        meta.chatgptAccountId,
        meta.chatgpt_account_id,
        tokens.chatgptAccountId,
        tokens.chatgpt_account_id,
        providerSpecificData.chatgptAccountId,
        providerSpecificData.chatgpt_account_id,
        credentials.chatgpt_account_id,
        auth.chatgpt_account_id,
        idAuth.chatgpt_account_id
    );
    const workspaceId = firstNonEmpty(
        account.workspaceId,
        account.workspace_id,
        record.workspaceId,
        record.workspace_id,
        meta.workspaceId,
        meta.workspace_id,
        providerSpecificData.workspaceId,
        providerSpecificData.workspace_id,
        credentials.workspace_id,
        payload?.workspace_id,
        idPayload?.workspace_id
    );
    const userId = firstNonEmpty(
        user.id,
        record.user_id,
        record.chatgptUserId,
        providerSpecificData.chatgptUserId,
        providerSpecificData.chatgpt_user_id,
        auth.chatgpt_user_id,
        auth.user_id,
        idAuth.chatgpt_user_id,
        idAuth.user_id
    );
    const planType = firstNonEmpty(
        account.planType,
        account.plan_type,
        record.planType,
        record.plan_type,
        providerSpecificData.chatgptPlanType,
        providerSpecificData.chatgpt_plan_type,
        credentials.plan_type,
        auth.chatgpt_plan_type,
        idAuth.chatgpt_plan_type
    );
    const exportedAt = normalizeTimestamp(options.now || new Date());
    const expiresIn = getExpiresIn(expiresAt, options.now || new Date());
    const sourceName = firstNonEmpty(options.sourceName, "pasted-json") ?? "pasted-json";
    const sourceType = record.provider === "codex" && record.authType === "oauth" ? "9router" : "chatgpt_web_session";
    const name = firstNonEmpty(email, sourceName, "ChatGPT Account");
    const syntheticIdToken = !inputIdToken
        ? buildSyntheticCodexIdToken(email, accountId, planType, userId, expiresAt)
        : undefined;
    const idToken = firstNonEmpty(inputIdToken, syntheticIdToken);

    const cpa = Object.fromEntries(Object.entries({
        type: "codex",
        account_id: accountId,
        chatgpt_account_id: accountId,
        email,
        name,
        plan_type: planType,
        chatgpt_plan_type: planType,
        id_token: idToken,
        id_token_synthetic: Boolean(syntheticIdToken) || undefined,
        access_token: accessToken,
        refresh_token: refreshToken || "",
        session_token: sessionToken,
        last_refresh: exportedAt,
        expired: expiresAt,
        disabled: Boolean(record.disabled) || undefined
    }).filter(([, value]) => value !== undefined && value !== null)) as JsonObject;

    const cockpit: JsonObject = {
        type: "codex",
        id_token: idToken,
        access_token: accessToken,
        refresh_token: refreshToken || "",
        account_id: accountId,
        last_refresh: exportedAt,
        email,
        expired: expiresAt,
        account_note: firstNonEmpty(record.account_note, record.accountInfo, record.account_info, record.note, record.notes, record.remark)
    };

    const sub2apiAccount = toJsonObject(stripUnavailable({
        name: firstNonEmpty(name, email, sourceName, "ChatGPT Account"),
        platform: "openai",
        type: "oauth",
        expires_at: accessTokenExpiresAt,
        auto_pause_on_expired: true,
        concurrency: 10,
        priority: 1,
        credentials: {
            access_token: accessToken,
            chatgpt_account_id: accountId,
            chatgpt_user_id: userId,
            email,
            expires_at: expiresAt,
            expires_in: expiresIn,
            plan_type: planType
        },
        extra: {
            email,
            email_key: toEmailKey(email),
            name,
            auth_provider: firstNonEmpty(record.authProvider, record.auth_provider),
            source: sourceType,
            last_refresh: exportedAt
        }
    }));
    const priority = Number.isFinite(Number(record.priority)) ? Number(record.priority) : 9;
    const isActive = typeof record.isActive === "boolean" ? record.isActive : !Boolean(record.disabled);
    const createdAt = normalizeTimestamp(record.createdAt) || exportedAt;
    const updatedAt = normalizeTimestamp(record.updatedAt) || exportedAt;
    const nineRouter = toJsonObject(stripUnavailable({
        accessToken,
        refreshToken,
        expiresAt,
        testStatus: firstNonEmpty(record.testStatus, record.test_status, "active"),
        expiresIn,
        providerSpecificData: {
            chatgptAccountId: accountId,
            chatgptPlanType: planType
        },
        id: accountId,
        provider: "codex",
        authType: "oauth",
        name,
        email,
        priority,
        isActive,
        createdAt,
        updatedAt
    }));
    const axonHubRefreshToken = refreshToken || AXONHUB_PLACEHOLDER_REFRESH_TOKEN;
    const codexAuthJson: JsonObject = {
        auth_mode: "chatgpt",
        OPENAI_API_KEY: null,
        tokens: {
            id_token: idToken,
            access_token: accessToken,
            refresh_token: refreshToken || "",
            account_id: accountId
        },
        last_refresh: exportedAt
    };
    const axonHub = toJsonObject(stripUnavailable({
        auth_mode: "chatgpt",
        last_refresh: getAxonHubLastRefresh(expiresAt, options.now || new Date()),
        tokens: {
            access_token: accessToken,
            refresh_token: axonHubRefreshToken,
            id_token: idToken
        },
        axonhub_refresh_token_placeholder: refreshToken ? undefined : true,
        axonhub_note: refreshToken ? undefined : "refresh_token is a placeholder; access_token works only until it expires."
    }));
    const codexManagerTokenHints = Object.fromEntries(Object.entries({
        account_id: accountId,
        chatgpt_account_id: chatgptAccountId
    }).filter(([, value]) => value !== undefined && value !== null && value !== "")) as JsonObject;
    const codexManagerMeta = Object.fromEntries(Object.entries({
        label: firstNonEmpty(name, email, sourceName, "ChatGPT Account"),
        workspace_id: workspaceId,
        chatgpt_account_id: chatgptAccountId,
        note: "Imported from ChatGPT session"
    }).filter(([, value]) => value !== undefined && value !== null && value !== "")) as JsonObject;
    const codexManager: JsonObject = {
        tokens: {
            access_token: accessToken,
            refresh_token: refreshToken || "",
            id_token: inputIdToken || "",
            ...codexManagerTokenHints
        },
        meta: codexManagerMeta
    };

    return {
        sourceName,
        sourcePath: options.sourcePath,
        email,
        name,
        expiresAt,
        accessTokenExpiresAt,
        cpa,
        cockpit,
        nineRouter,
        codexAuthJson,
        axonHub,
        codexManager,
        sub2apiAccount
    };
}

/**
 * 构建 sub2api 批量导入文档。
 *
 * @param converted 已转换账号列表。
 * @param now 导出时间。
 * @returns sub2api 需要的 exported_at/proxies/accounts 结构。
 */
export function buildSub2apiDocument(converted: ConvertedSession[], now = new Date()): JsonObject {
    return {
        exported_at: normalizeTimestamp(now),
        proxies: [],
        accounts: converted.map((item) => item.sub2apiAccount)
    };
}

/**
 * 根据当前格式选择最终输出文档。
 *
 * @param format 目标输出格式。
 * @param converted 已转换账号列表。
 * @param now 导出时间。
 * @returns 用于序列化到输出框或下载文件的 JSON 文档。
 */
export function buildOutputDocument(
    format: FormatConverterOutputFormat,
    converted: ConvertedSession[],
    now = new Date()
): unknown {
    if (format === "sub2api") return buildSub2apiDocument(converted, now);
    if (format === "cpa") return converted.length === 1 ? converted[0].cpa : converted.map((item) => item.cpa);
    if (format === "cockpit") return converted.length === 1 ? converted[0].cockpit : converted.map((item) => item.cockpit);
    if (format === "9router") return converted.length === 1 ? converted[0].nineRouter : converted.map((item) => item.nineRouter);
    if (format === "codex") return converted.length === 1 ? converted[0].codexAuthJson : converted.map((item) => item.codexAuthJson);
    if (format === "axonhub") return converted.length === 1 ? converted[0].axonHub : converted.map((item) => item.axonHub);
    if (format === "codexmanager") return converted.length === 1 ? converted[0].codexManager : converted.map((item) => item.codexManager);
    return buildSub2apiDocument(converted, now);
}

/**
 * 转换用户粘贴的 JSON 文本。
 *
 * @param text 用户粘贴的 JSON 文本。
 * @returns 转换成功账号、跳过项和来源对象。
 * @throws 当 JSON 解析失败时抛出错误。
 */
export function convertFromText(text: string): ConversionResult {
    const sources = parseInputDocuments(text);
    const converted: ConvertedSession[] = [];
    const skipped: SkippedSession[] = [];
    const now = new Date();

    sources.forEach((item, index) => {
        try {
            converted.push(convertSession(item.value, {
                now,
                sourceName: item.sourceName,
                sourcePath: item.path || `$[${index}]`
            }));
        } catch (error) {
            skipped.push({
                sourceName: item.sourceName,
                path: item.path,
                reason: error instanceof Error ? error.message : "无法转换"
            });
        }
    });

    if (!sources.length) {
        skipped.push({
            sourceName: "pasted-json",
            path: "$",
            reason: "未找到包含 accessToken 和 user/email 的 session 对象"
        });
    }

    return {sources, converted, skipped};
}

/**
 * 转换用户选择的 JSON 文件内容。
 *
 * @param files 已由浏览器读取出的文件名和文本内容。
 * @returns 转换结果以及回填到输入框的规范化 JSON 文本。
 */
export function convertFiles(files: ConversionFilePayload[]): ConversionFileResult {
    const jsonFiles = files.filter((file) => file.name.toLowerCase().endsWith(".json"));
    const documents: SessionSource[] = [];
    const skipped: SkippedSession[] = [];

    for (const file of jsonFiles) {
        try {
            const parsed = JSON.parse(file.text);
            const found = collectSessionLikeObjects(parsed, file.name);
            if (!found.length) {
                skipped.push({
                    sourceName: file.name,
                    path: "$",
                    reason: "未找到包含 accessToken 和 user/email 的 session 对象"
                });
            }
            documents.push(...found);
        } catch (error) {
            skipped.push({
                sourceName: file.name,
                path: "$",
                reason: error instanceof Error ? error.message : "无法读取文件"
            });
        }
    }

    const now = new Date();
    const converted: ConvertedSession[] = [];
    const convertSkipped = [...skipped];
    documents.forEach((item) => {
        try {
            converted.push(convertSession(item.value, {
                now,
                sourceName: item.sourceName,
                sourcePath: item.path
            }));
        } catch (error) {
            convertSkipped.push({
                sourceName: item.sourceName,
                path: item.path,
                reason: error instanceof Error ? error.message : "无法转换"
            });
        }
    });

    return {
        sources: documents,
        converted,
        skipped: convertSkipped,
        inputText: documents.length === 1
            ? JSON.stringify(documents[0].value, null, 2)
            : JSON.stringify(documents.map((item) => item.value), null, 2),
        filesRead: jsonFiles.length
    };
}
