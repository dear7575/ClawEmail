import {useEffect, useMemo, useState} from "react";
import {CommunicationRulesDrawer} from "./components/CommunicationRulesDrawer";
import {ComposeDrawer} from "./components/ComposeDrawer";
import {InboxView} from "./components/InboxView";
import {ListenersDrawer} from "./components/ListenersDrawer";
import {MailboxesView} from "./components/MailboxesView";
import {Button} from "./components/ui/button";
import {Dialog, DialogContent, DialogDescription, DialogTitle} from "./components/ui/dialog";
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from "./components/ui/select";
import {PrefsBar, usePrefs} from "./i18n";
import {LogOut} from "lucide-react";
import {
    type ClawAuthStatus,
    convertSub2Account,
    createDuckAccount,
    createEventSource,
    createMailbox,
    deleteDuckAccount,
    deleteDuckAddress,
    deleteMailbox,
    disconnectConnection,
    type DuckAccount,
    type DuckAddress,
    fetchConnections,
    fetchDuckAccounts,
    fetchDuckAddresses,
    fetchListeners,
    fetchListenerSettings,
    fetchMail,
    fetchMailboxes,
    fetchMails,
    fetchSub2Groups,
    fetchSub2Settings,
    fetchSystemNetworkSettings,
    fetchTelegramSettings,
    generateDuckAddress,
    getAdminPassword,
    getRuntimeMode,
    type ListenerSettings,
    type ListenerSnapshot,
    type Mailbox,
    type MailDetail,
    type MailSummary,
    pushSub2Account,
    refreshConnection,
    sendConnectionLoginCode,
    sendTelegramNotification,
    setAdminPassword,
    setRuntimeMode,
    type Sub2Group,
    type Sub2Settings,
    type SystemNetworkSettings,
    type TelegramSettings,
    updateDuckAccountToken,
    updateListenerSettings,
    updateSub2Settings,
    updateSystemNetworkSettings,
    updateTelegramSettings,
    verifyAdminPassword,
    verifyConnectionLoginCode
} from "./api";

type View = "dashboard" | "connections" | "mailboxes" | "duck" | "inbox" | "accountPush" | "notifications" | "settings";
type ToastItem = {
    id: number;
    type: "success" | "error";
    message: string;
};
type ListenerStatusRefreshInterval = "manual" | "30" | "60" | "300";

const VIEW_STORAGE_KEY = "claw.currentView";
const LISTENER_RECONNECT_NOTICE_STORAGE_KEY = "claw.listener.reconnectNotice";
const INBOX_AUTO_REFRESH_STORAGE_KEY = "claw.inbox.autoRefresh";
const LISTENER_STATUS_REFRESH_STORAGE_KEY = "claw.listener.statusRefresh";
const LIVE_LISTENER_STATUSES = new Set(["running", "open"]);
const CLAW_LOGIN_NAME_PATTERN = /^[^\s@]+$/;
const CLAW_LOGIN_DOMAIN = "@163.com";
const ALL_SELECT_VALUE = "__all";
const LISTENER_STATUS_REFRESH_MS: Record<ListenerStatusRefreshInterval, number | null> = {
    manual: null,
    "30": 30_000,
    "60": 60_000,
    "300": 300_000
};

function readInitialView(): View {
    if (typeof localStorage === "undefined") return "dashboard";
    const saved = localStorage.getItem(VIEW_STORAGE_KEY);
    return saved === "connections" || saved === "mailboxes" || saved === "inbox" || saved === "settings"
    || saved === "notifications"
    || saved === "accountPush"
    || saved === "duck"
        ? saved
        : "dashboard";
}

function titleForView(view: View): string {
    const map: Record<View, string> = {
        dashboard: "仪表盘",
        connections: "连接管理",
        mailboxes: "Claw 邮箱",
        duck: "Duck 邮箱",
        inbox: "收件管理",
        accountPush: "账号推送",
        notifications: "消息通知",
        settings: "系统设置"
    };
    return map[ view ];
}

function readBooleanPreference(key: string, fallback: boolean): boolean {
    if (typeof localStorage === "undefined") return fallback;
    const saved = localStorage.getItem(key);
    if (saved === "true") return true;
    if (saved === "false") return false;
    return fallback;
}

function readListenerStatusRefreshInterval(): ListenerStatusRefreshInterval {
    if (typeof localStorage === "undefined") return "manual";
    const saved = localStorage.getItem(LISTENER_STATUS_REFRESH_STORAGE_KEY);
    return saved === "30" || saved === "60" || saved === "300" ? saved : "manual";
}

function statusLabel(connection: ClawAuthStatus): string {
    if (connection.connected) return "在线";
    if (connection.status === "disconnected") return "已断开";
    return "未完整";
}

function normalizeLoginName(value: string): string {
    const normalized = value.trim().replace(/＠/g, "@").toLowerCase();
    return normalized.endsWith(CLAW_LOGIN_DOMAIN)
        ? normalized.slice(0, -CLAW_LOGIN_DOMAIN.length)
        : normalized;
}

function loginEmailFromName(value: string): string {
    return `${normalizeLoginName(value)}${CLAW_LOGIN_DOMAIN}`;
}

export function App() {
    const {t} = usePrefs();

    const initialAdminPassword = getAdminPassword();
    const [password, setPassword] = useState("");
    const [loginInput, setLoginInput] = useState(initialAdminPassword);
    const [loginError, setLoginError] = useState("");
    const [loginBusy, setLoginBusy] = useState(Boolean(initialAdminPassword));

    const [view, setView] = useState<View>(readInitialView);
    const [connections, setConnections] = useState<ClawAuthStatus[]>([]);
    const [selectedConnectionId, setSelectedConnectionId] = useState("");
    const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
    const [selectedMailbox, setSelectedMailbox] = useState("");
    const [mails, setMails] = useState<MailSummary[]>([]);
    const [selectedMail, setSelectedMail] = useState<MailDetail | null>(null);
    const [duckAccounts, setDuckAccounts] = useState<DuckAccount[]>([]);
    const [duckAddresses, setDuckAddresses] = useState<DuckAddress[]>([]);
    const [selectedDuckAccountId, setSelectedDuckAccountId] = useState("");
    const [duckLabel, setDuckLabel] = useState("");
    const [duckToken, setDuckToken] = useState("");
    const [duckForwardingMailbox, setDuckForwardingMailbox] = useState("");
    const [duckNote, setDuckNote] = useState("");
    const [duckBusy, setDuckBusy] = useState(false);
    const [duckAccountToRemove, setDuckAccountToRemove] = useState<DuckAccount | null>(null);
    const [duckAccountToUpdate, setDuckAccountToUpdate] = useState<DuckAccount | null>(null);
    const [duckAddressToDelete, setDuckAddressToDelete] = useState<DuckAddress | null>(null);
    const [duckTokenUpdate, setDuckTokenUpdate] = useState("");

    const [toasts, setToasts] = useState<ToastItem[]>([]);

    const [suffix, setSuffix] = useState("");
    const [mailboxSyncBusy, setMailboxSyncBusy] = useState(false);
    const [rulesMailbox, setRulesMailbox] = useState<Mailbox | null>(null);
    const [composeOpen, setComposeOpen] = useState(false);

    const [clawLoginName, setClawLoginName] = useState("");
    const [clawLoginCode, setClawLoginCode] = useState("");
    const [clawCodeSent, setClawCodeSent] = useState(false);
    const [clawBusy, setClawBusy] = useState(false);

    const [listenerItems, setListenerItems] = useState<ListenerSnapshot[]>([]);
    const [listenerBusy, setListenerBusy] = useState(false);
    const [listenersDrawerOpen, setListenersDrawerOpen] = useState(false);
    const [showListenerReconnectNotice, setShowListenerReconnectNotice] = useState(() => (
        readBooleanPreference(LISTENER_RECONNECT_NOTICE_STORAGE_KEY, false)
    ));
    const [inboxAutoRefresh, setInboxAutoRefresh] = useState(() => (
        readBooleanPreference(INBOX_AUTO_REFRESH_STORAGE_KEY, true)
    ));
    const [listenerStatusRefreshInterval, setListenerStatusRefreshInterval] =
        useState<ListenerStatusRefreshInterval>(readListenerStatusRefreshInterval);
    const [serverListenerSettings, setServerListenerSettings] = useState<ListenerSettings>({
        logMode: "quiet",
        reconnectMode: "standard"
    });
    const [systemProxyInput, setSystemProxyInput] = useState("");
    const [systemTimeoutInput, setSystemTimeoutInput] = useState("10000");
    const [telegramSettings, setTelegramSettings] = useState<TelegramSettings>({
        enabled: false,
        chatId: "",
        hasBotToken: false,
        botTokenPreview: null
    });
    const [sub2Settings, setSub2Settings] = useState<Sub2Settings>({
        apiUrl: "",
        hasApiKey: false,
        apiKeyPreview: null
    });
    const [telegramEnabledInput, setTelegramEnabledInput] = useState(false);
    const [telegramBotTokenInput, setTelegramBotTokenInput] = useState("");
    const [telegramChatIdInput, setTelegramChatIdInput] = useState("");
    const [sub2ApiUrlInput, setSub2ApiUrlInput] = useState("");
    const [sub2ApiKeyInput, setSub2ApiKeyInput] = useState("");
    const [settingsBusy, setSettingsBusy] = useState(false);
    const [telegramMessage, setTelegramMessage] = useState("");
    const [telegramSendBusy, setTelegramSendBusy] = useState(false);
    const [sub2SourceJson, setSub2SourceJson] = useState("");
    const [sub2PreviewJson, setSub2PreviewJson] = useState("");
    const [sub2Groups, setSub2Groups] = useState<Sub2Group[]>([]);
    const [selectedSub2GroupId, setSelectedSub2GroupId] = useState("");
    const [sub2GroupsBusy, setSub2GroupsBusy] = useState(false);
    const [sub2Busy, setSub2Busy] = useState(false);

    const activeConnections = useMemo(
        () => connections.filter((connection) => connection.status !== "disconnected"),
        [connections]
    );

    const selectedConnection = useMemo(
        () => connections.find((connection) => connection.id === selectedConnectionId) ?? null,
        [connections, selectedConnectionId]
    );

    const activeMailboxes = useMemo(
        () => mailboxes.filter((mailbox) => mailbox.status !== "deleted"),
        [mailboxes]
    );

    const visibleMailboxes = useMemo(() => (
        selectedConnection
            ? activeMailboxes.filter((mailbox) => mailbox.connection_id === selectedConnection.id)
            : activeMailboxes
    ), [activeMailboxes, selectedConnection]);

    const visibleListeners = useMemo(() => (
        selectedConnection
            ? listenerItems.filter((item) => item.connectionId === selectedConnection.id)
            : listenerItems
    ), [listenerItems, selectedConnection]);

    const activeDuckAccounts = useMemo(
        () => duckAccounts.filter((account) => account.status !== "disabled"),
        [duckAccounts]
    );

    const selectedDuckAccount = useMemo(
        () => duckAccounts.find((account) => account.id === selectedDuckAccountId) ?? null,
        [duckAccounts, selectedDuckAccountId]
    );

    const visibleDuckAddresses = useMemo(() => {
        return selectedDuckAccount?.id
            ? duckAddresses.filter((address) => address.account_id === selectedDuckAccount.id)
            : duckAddresses;
    }, [duckAddresses, selectedDuckAccount?.id]);

    const lastDuckForwardingMailbox = useMemo(() => {
        const item = duckAddresses
            .filter((address) => (
                address.account_id === selectedDuckAccount?.id &&
                address.forwarding_mailbox_email &&
                activeMailboxes.some((mailbox) => mailbox.email === address.forwarding_mailbox_email)
            ))
            .sort((a, b) => b.id - a.id)[ 0 ];
        return item?.forwarding_mailbox_email ?? "";
    }, [activeMailboxes, duckAddresses, selectedDuckAccount?.id]);

    const labeledDuckForwardingMailbox = useMemo(() => {
        const label = selectedDuckAccount?.label.trim().toLowerCase();
        if (!label || !label.includes("@")) return "";
        return activeMailboxes.find((mailbox) => mailbox.email.toLowerCase() === label)?.email ?? "";
    }, [activeMailboxes, selectedDuckAccount?.label]);

    const defaultDuckForwardingMailbox = useMemo(() => {
        if (labeledDuckForwardingMailbox) return labeledDuckForwardingMailbox;
        if (lastDuckForwardingMailbox) return lastDuckForwardingMailbox;
        if (selectedConnection?.id) {
            const rootEmail = selectedConnection.rootPrefix && selectedConnection.domain
                ? `${selectedConnection.rootPrefix}@${selectedConnection.domain}`
                : null;
            const rootMailbox = rootEmail
                ? activeMailboxes.find((mailbox) =>
                    mailbox.connection_id === selectedConnection.id && mailbox.email === rootEmail
                )
                : null;
            return rootMailbox?.email
                ?? activeMailboxes.find((mailbox) => mailbox.connection_id === selectedConnection.id)?.email
                ?? "";
        }
        return activeMailboxes[ 0 ]?.email ?? "";
    }, [activeMailboxes, labeledDuckForwardingMailbox, lastDuckForwardingMailbox, selectedConnection]);

    const listenerSummary = useMemo(() => {
        let running = 0;
        let errors = 0;
        for (const item of visibleListeners) {
            if (LIVE_LISTENER_STATUSES.has(item.status)) running++;
            if (item.status === "error" || item.error) errors++;
        }
        return {running, total: visibleListeners.length, errors};
    }, [visibleListeners]);

    function removeToast(id: number) {
        setToasts((items) => items.filter((item) => item.id !== id));
    }

    function notify(type: ToastItem["type"], message: string) {
        if (!message) return;
        const id = Date.now() + Math.floor(Math.random() * 1000);
        setToasts((items) => [...items.slice(-3), {id, type, message}]);
        window.setTimeout(() => removeToast(id), type === "error" ? 6500 : 3800);
    }

    function showStatus(message: string) {
        notify("success", message);
    }

    function showError(message: string) {
        notify("error", message);
    }

    function reportError(err: unknown) {
        showError(err instanceof Error ? err.message : String(err));
    }

    function formatLoginError(err: unknown): string {
        const message = err instanceof Error ? err.message : String(err);
        return message === "unauthorized" ? t("login.error.unauthorized") : message;
    }

    async function handleLogin(nextPassword = loginInput) {
        if (!nextPassword) return;
        setLoginBusy(true);
        setLoginError("");
        try {
            await verifyAdminPassword(nextPassword);
            setAdminPassword(nextPassword);
            setPassword(nextPassword);
        } catch (err) {
            const loginMessage = formatLoginError(err);
            setAdminPassword("");
            setPassword("");
            setLoginError(loginMessage);
            if (loginMessage === t("login.error.unauthorized")) {
                setLoginInput("");
            }
        } finally {
            setLoginBusy(false);
        }
    }

    async function loadConnections(): Promise<ClawAuthStatus[]> {
        const items = await fetchConnections();
        setConnections(items);
        setSelectedConnectionId((current) => {
            if (current && items.some((item) => item.id === current && item.status !== "disconnected")) return current;
            return "";
        });
        return items;
    }

    async function loadMailboxes(sync = false, connectionId = selectedConnection?.id): Promise<Mailbox[]> {
        const items = await fetchMailboxes(sync, sync ? connectionId ?? undefined : undefined);
        setMailboxes(items);
        return items;
    }

    function mailConnectionFilter(mailbox = selectedMailbox): string | undefined {
        if (!mailbox) return undefined;
        return activeMailboxes.find((item) => item.email === mailbox)?.connection_id ?? undefined;
    }

    async function loadMails(mailbox = selectedMailbox, sync = false, connectionId = mailConnectionFilter(mailbox)) {
        const data = await fetchMails(mailbox || undefined, 50, 0, sync, connectionId ?? undefined);
        setMails(data.items);
        if (selectedMail && !data.items.some((mail) => mail.id === selectedMail.id)) {
            setSelectedMail(null);
        }
    }

    async function loadMail(id: number) {
        const detail = await fetchMail(id);
        setSelectedMail(detail);
        setMails((items) => items.map((mail) => (
            mail.id === detail.id ? {...mail, read_at: detail.read_at} : mail
        )));
    }

    async function loadListeners() {
        setListenerBusy(true);
        try {
            const data = await fetchListeners();
            setListenerItems(data);
        } catch (err) {
            reportError(err);
        } finally {
            setListenerBusy(false);
        }
    }

    async function loadDuckAccounts(): Promise<DuckAccount[]> {
        const items = await fetchDuckAccounts();
        setDuckAccounts(items);
        setSelectedDuckAccountId((current) => {
            if (current && items.some((item) => item.id === current && item.status !== "disabled")) return current;
            return items.find((item) => item.status !== "disabled")?.id ?? "";
        });
        return items;
    }

    async function loadDuckAddresses(accountId = selectedDuckAccount?.id): Promise<DuckAddress[]> {
        const items = await fetchDuckAddresses({
            accountId: accountId || undefined
        });
        setDuckAddresses((current) => {
            if (!accountId) return items;
            const others = current.filter((item) => item.account_id !== accountId);
            return [...items, ...others];
        });
        return items;
    }

    useEffect(() => {
        const savedPassword = getAdminPassword();
        if (!savedPassword) return;
        handleLogin(savedPassword);
    }, []);

    useEffect(() => {
        localStorage.setItem(VIEW_STORAGE_KEY, view);
    }, [view]);

    useEffect(() => {
        localStorage.setItem(LISTENER_RECONNECT_NOTICE_STORAGE_KEY, String(showListenerReconnectNotice));
    }, [showListenerReconnectNotice]);

    useEffect(() => {
        localStorage.setItem(INBOX_AUTO_REFRESH_STORAGE_KEY, String(inboxAutoRefresh));
    }, [inboxAutoRefresh]);

    useEffect(() => {
        localStorage.setItem(LISTENER_STATUS_REFRESH_STORAGE_KEY, listenerStatusRefreshInterval);
    }, [listenerStatusRefreshInterval]);

    useEffect(() => {
        if (!password) return;
        setAdminPassword(password);
        loadConnections().catch(reportError);
        loadMailboxes().catch(reportError);
        loadServerListenerSettings().catch(reportError);
        loadSystemNetworkSettings().catch(reportError);
        loadTelegramSettings().catch(reportError);
        loadSub2Settings().catch(reportError);
        loadDuckAccounts()
            .then((items) => {
                const first = items.find((item) => item.status !== "disabled");
                if (first?.id) {
                    return fetchDuckAddresses({accountId: first.id});
                }
                return [];
            })
            .then(setDuckAddresses)
            .catch(reportError);
    }, [password]);

    useEffect(() => {
        if (!password) return;
        if (getRuntimeMode() === "cloudflare") return;
        const events = createEventSource();
        events.addEventListener("mail", () => {
            if (!inboxAutoRefresh) return;
            loadMails().catch(reportError);
        });
        events.addEventListener("cloudflare-mode", () => {
            setRuntimeMode("cloudflare");
            events.close();
            showStatus(t("flash.events.manualSync"));
        });
        events.onerror = () => {
            if (getRuntimeMode() === "cloudflare") return;
            if (!showListenerReconnectNotice) return;
            showStatus(t("flash.events.reconnecting"));
        };
        return () => events.close();
    }, [password, selectedConnection?.id, selectedMailbox, inboxAutoRefresh, showListenerReconnectNotice]);

    useEffect(() => {
        if (!password) return;
        setSelectedMail(null);
        loadMails(selectedMailbox, false, mailConnectionFilter(selectedMailbox)).catch(reportError);
        loadListeners();
    }, [password, selectedConnection?.id]);

    useEffect(() => {
        if (!password) return;
        const intervalMs = LISTENER_STATUS_REFRESH_MS[ listenerStatusRefreshInterval ];
        if (!intervalMs) return;
        const timer = window.setInterval(() => {
            loadListeners();
        }, intervalMs);
        return () => window.clearInterval(timer);
    }, [password, listenerStatusRefreshInterval, selectedConnection?.id]);

    useEffect(() => {
        if (!password) return;
        loadMails(selectedMailbox, false, mailConnectionFilter(selectedMailbox)).catch(reportError);
    }, [password, selectedMailbox]);

    useEffect(() => {
        if (!password || view !== "duck") return;
        loadDuckAddresses(selectedDuckAccount?.id).catch(reportError);
    }, [password, view, selectedDuckAccount?.id]);

    useEffect(() => {
        if (!password || view !== "accountPush") return;
        if (!sub2Settings.apiUrl || !sub2Settings.hasApiKey) return;
        loadSub2Groups().catch(reportError);
    }, [password, view, sub2Settings.apiUrl, sub2Settings.hasApiKey]);

    useEffect(() => {
        if (!password || view !== "duck") return;
        if (selectedDuckAccountId && activeDuckAccounts.some((account) => account.id === selectedDuckAccountId)) return;
        setSelectedDuckAccountId(activeDuckAccounts[ 0 ]?.id ?? "");
    }, [activeDuckAccounts, password, selectedDuckAccountId, view]);

    useEffect(() => {
        if (!password || view !== "duck") return;
        if (!defaultDuckForwardingMailbox) return;
        setDuckForwardingMailbox(defaultDuckForwardingMailbox);
    }, [password, view, defaultDuckForwardingMailbox]);

    async function handleCreateMailbox() {
        try {
            const created = await createMailbox(suffix, selectedConnection?.id ?? undefined);
            setSuffix("");
            showStatus(t("flash.mb.created", {email: created.email}));
            await loadMailboxes();
        } catch (err) {
            reportError(err);
        }
    }

    async function handleDeleteMailbox(mailbox: Mailbox) {
        if (!confirm(t("mb.confirm.delete", {email: mailbox.email}))) return;
        try {
            await deleteMailbox(mailbox.id);
            showStatus(t("flash.mb.deleted", {email: mailbox.email}));
            await loadMailboxes();
            if (selectedMailbox === mailbox.email) {
                setSelectedMailbox("");
                setMails([]);
            }
        } catch (err) {
            reportError(err);
        }
    }

    function handleSelectInboxMailbox(value: string) {
        if (value === ALL_SELECT_VALUE) {
            setSelectedMailbox("");
            return;
        }
        const mailbox = activeMailboxes.find((item) => item.email === value);
        if (mailbox?.connection_id) {
            setSelectedConnectionId(mailbox.connection_id);
        }
        setSelectedMailbox(value);
    }

    async function handleSendClawCode() {
        setClawBusy(true);
        try {
            const loginName = normalizeLoginName(clawLoginName);
            if (!CLAW_LOGIN_NAME_PATTERN.test(loginName)) {
                showError(t("conn.error.emailFormat"));
                return;
            }
            setClawLoginName(loginName);
            await sendConnectionLoginCode(loginEmailFromName(loginName));
            setClawCodeSent(true);
            showStatus(t("flash.code.sent"));
        } catch (err) {
            reportError(err);
        } finally {
            setClawBusy(false);
        }
    }

    async function handleVerifyClawCode() {
        setClawBusy(true);
        try {
            const loginName = normalizeLoginName(clawLoginName);
            const code = clawLoginCode.trim();
            if (!CLAW_LOGIN_NAME_PATTERN.test(loginName)) {
                showError(t("conn.error.emailFormat"));
                return;
            }
            if (!/^\d+$/.test(code)) {
                showError(t("conn.error.codeFormat"));
                return;
            }
            setClawLoginName(loginName);
            const result = await verifyConnectionLoginCode(loginEmailFromName(loginName), code);
            setClawLoginCode("");
            setClawCodeSent(false);
            setClawLoginName("");
            showStatus(t("flash.claw.bound", {n: result.syncedMailboxes}));
            await loadConnections();
            await loadMailboxes();
            loadListeners();
        } catch (err) {
            reportError(err);
        } finally {
            setClawBusy(false);
        }
    }

    async function handleRefreshConnection(connectionId = selectedConnection?.id) {
        if (!connectionId) return;
        setClawBusy(true);
        try {
            const result = await refreshConnection(connectionId);
            showStatus(t("flash.claw.refreshed", {n: result.syncedMailboxes}));
            await loadConnections();
            await loadMailboxes();
            loadListeners();
        } catch (err) {
            reportError(err);
        } finally {
            setClawBusy(false);
        }
    }

    async function handleSyncMailboxes() {
        showStatus(t("flash.mb.syncing"));
        setMailboxSyncBusy(true);
        try {
            let items: Mailbox[];
            if (selectedConnection?.id) {
                items = await loadMailboxes(true, selectedConnection.id);
            } else {
                for (const connection of activeConnections) {
                    if (connection.id && connection.hasDashboardCookie) {
                        await fetchMailboxes(true, connection.id);
                    }
                }
                items = await loadMailboxes(false);
            }
            showStatus(t("flash.mb.synced", {
                n: items.filter((mailbox) => mailbox.status !== "deleted").length
            }));
            loadListeners();
        } catch (err) {
            reportError(err);
        } finally {
            setMailboxSyncBusy(false);
        }
    }

    async function handleDisconnectConnection(connectionId: string) {
        if (!confirm(t("confirm.disconnect"))) return;
        setClawBusy(true);
        try {
            await disconnectConnection(connectionId);
            showStatus(t("flash.claw.severed"));
            await loadConnections();
            loadListeners();
        } catch (err) {
            reportError(err);
        } finally {
            setClawBusy(false);
        }
    }

    async function loadServerListenerSettings() {
        try {
            setServerListenerSettings(await fetchListenerSettings());
        } catch (err) {
            reportError(err);
        }
    }

    function applySystemNetworkSettings(settings: SystemNetworkSettings) {
        setSystemProxyInput(settings.proxyUrl);
        setSystemTimeoutInput(String(settings.timeoutMs));
    }

    async function loadSystemNetworkSettings() {
        try {
            applySystemNetworkSettings(await fetchSystemNetworkSettings());
        } catch (err) {
            reportError(err);
        }
    }

    async function handleSaveSettings() {
        const timeoutMs = Number(systemTimeoutInput);
        if (!Number.isFinite(timeoutMs)) {
            showError("系统请求超时时间必须是数字");
            return;
        }
        setSettingsBusy(true);
        try {
            const [listenerSaved, networkSaved, telegramSaved, sub2Saved] = await Promise.all([
                updateListenerSettings(serverListenerSettings),
                updateSystemNetworkSettings({
                    proxyUrl: systemProxyInput.trim(),
                    timeoutMs
                }),
                updateTelegramSettings({
                    enabled: telegramEnabledInput,
                    chatId: telegramChatIdInput.trim(),
                    botToken: telegramBotTokenInput.trim() || undefined
                }),
                updateSub2Settings({
                    apiUrl: sub2ApiUrlInput.trim(),
                    apiKey: sub2ApiKeyInput.trim() || undefined
                })
            ]);
            setServerListenerSettings(listenerSaved);
            applySystemNetworkSettings(networkSaved);
            applyTelegramSettings(telegramSaved);
            applySub2Settings(sub2Saved);
            showStatus("系统配置已保存");
        } catch (err) {
            reportError(err);
            await Promise.all([
                loadServerListenerSettings(),
                loadSystemNetworkSettings(),
                loadTelegramSettings(),
                loadSub2Settings()
            ]);
        } finally {
            setSettingsBusy(false);
        }
    }

    function applyTelegramSettings(settings: TelegramSettings) {
        setTelegramSettings(settings);
        setTelegramEnabledInput(settings.enabled);
        setTelegramChatIdInput(settings.chatId);
        setTelegramBotTokenInput("");
    }

    async function loadTelegramSettings() {
        try {
            applyTelegramSettings(await fetchTelegramSettings());
        } catch (err) {
            reportError(err);
        }
    }

    function applySub2Settings(settings: Sub2Settings) {
        setSub2Settings(settings);
        setSub2ApiUrlInput(settings.apiUrl);
        setSub2ApiKeyInput("");
    }

    async function loadSub2Settings() {
        try {
            applySub2Settings(await fetchSub2Settings());
        } catch (err) {
            reportError(err);
        }
    }

    async function loadSub2Groups() {
        setSub2GroupsBusy(true);
        try {
            const groups = await fetchSub2Groups();
            setSub2Groups(groups);
            setSelectedSub2GroupId((current) => {
                if (current && groups.some((group) => String(group.id) === current)) return current;
                return groups[ 0 ]?.id ? String(groups[ 0 ].id) : "";
            });
        } catch (err) {
            setSub2Groups([]);
            setSelectedSub2GroupId("");
            reportError(err);
        } finally {
            setSub2GroupsBusy(false);
        }
    }

    function parseSub2SourceInput(): unknown {
        if (!sub2SourceJson.trim()) {
            throw new Error("请输入账号 JSON");
        }
        return JSON.parse(sub2SourceJson);
    }

    async function handleConvertSub2Account() {
        setSub2Busy(true);
        try {
            const data = await convertSub2Account(parseSub2SourceInput());
            setSub2PreviewJson(JSON.stringify(data, null, 2));
            showStatus("账号 JSON 已转换");
        } catch (err) {
            reportError(err);
        } finally {
            setSub2Busy(false);
        }
    }

    async function handlePushSub2Account() {
        const groupId = Number(selectedSub2GroupId);
        if (!Number.isInteger(groupId) || groupId <= 0) {
            showError("请选择要推送到的 Sub2 分组");
            return;
        }
        setSub2Busy(true);
        try {
            const result = await pushSub2Account(parseSub2SourceInput(), groupId);
            setSub2PreviewJson(JSON.stringify(result.data, null, 2));
            showStatus("账号已推送到 Sub2API");
        } catch (err) {
            reportError(err);
        } finally {
            setSub2Busy(false);
        }
    }

    async function handleSendTelegramMessage() {
        const text = telegramMessage.trim();
        if (!text) {
            showError("请输入要发送的消息内容");
            return;
        }
        setTelegramSendBusy(true);
        try {
            await sendTelegramNotification(text);
            setTelegramMessage("");
            showStatus("Telegram 消息已发送");
        } catch (err) {
            reportError(err);
        } finally {
            setTelegramSendBusy(false);
        }
    }

    function handleOpenConnectionListeners(connection: ClawAuthStatus) {
        if (connection.id) {
            setSelectedConnectionId(connection.id);
        }
        setListenersDrawerOpen(true);
    }

    async function handleCreateDuckAccount() {
        setDuckBusy(true);
        try {
            const account = await createDuckAccount({
                label: duckLabel.trim(),
                token: duckToken.trim()
            });
            setDuckLabel("");
            setDuckToken("");
            setSelectedDuckAccountId(account.id);
            showStatus(`Duck Token 已保存 · ${account.label}`);
            await loadDuckAccounts();
            await loadDuckAddresses(account.id);
        } catch (err) {
            reportError(err);
        } finally {
            setDuckBusy(false);
        }
    }

    async function handleGenerateDuckAddress() {
        if (!selectedDuckAccount?.id) return;
        setDuckBusy(true);
        try {
            const row = await generateDuckAddress(selectedDuckAccount.id, {
                forwardingMailboxEmail: duckForwardingMailbox || undefined,
                note: duckNote || undefined
            });
            setDuckNote("");
            showStatus(`已生成 Duck 邮箱 · ${row.address}`);
            await loadDuckAccounts();
            await loadDuckAddresses(selectedDuckAccount.id);
        } catch (err) {
            reportError(err);
            await loadDuckAccounts().catch(() => undefined);
        } finally {
            setDuckBusy(false);
        }
    }

    async function handleDeleteDuckAddress() {
        if (!duckAddressToDelete) return;
        const address = duckAddressToDelete;
        setDuckBusy(true);
        try {
            await deleteDuckAddress(address.id);
            setDuckAddressToDelete(null);
            setDuckAddresses((items) => items.filter((item) => item.id !== address.id));
            showStatus(`Duck 邮箱记录已删除 · ${address.address}`);
        } catch (err) {
            reportError(err);
        } finally {
            setDuckBusy(false);
        }
    }

    async function handleDisableDuckAccount() {
        if (!duckAccountToRemove) return;
        setDuckBusy(true);
        try {
            const removedId = duckAccountToRemove.id;
            await deleteDuckAccount(removedId);
            showStatus(`Duck Token 已删除 · ${duckAccountToRemove.label}`);
            setDuckAccountToRemove(null);
            setDuckAddresses((items) => items.filter((item) => item.account_id !== removedId));
            await loadDuckAccounts();
        } catch (err) {
            reportError(err);
        } finally {
            setDuckBusy(false);
        }
    }

    async function handleUpdateDuckAccountToken() {
        if (!duckAccountToUpdate || !duckTokenUpdate.trim()) return;
        setDuckBusy(true);
        try {
            const account = await updateDuckAccountToken(duckAccountToUpdate.id, duckTokenUpdate.trim());
            setDuckAccountToUpdate(null);
            setDuckTokenUpdate("");
            setDuckAccounts((items) => items.map((item) => item.id === account.id ? account : item));
            showStatus(`Duck Token 已更新 · ${account.label}`);
        } catch (err) {
            reportError(err);
        } finally {
            setDuckBusy(false);
        }
    }

    function handleLogout() {
        setAdminPassword("");
        setPassword("");
        setLoginInput("");
        setLoginError("");
        setConnections([]);
        setDuckAccounts([]);
        setDuckAddresses([]);
        setSelectedDuckAccountId("");
        setDuckAccountToUpdate(null);
        setSelectedConnectionId("");
        setListenerItems([]);
        setListenersDrawerOpen(false);
        setRulesMailbox(null);
        setMailboxes([]);
        setSelectedMailbox("");
        setMails([]);
        setSelectedMail(null);
    }

    if (!password) {
        const stamp = new Date()
            .toLocaleString("sv-SE", {timeZone: "Asia/Shanghai", hour12: false})
            .slice(0, 19);
        return (
            <main className="login-shell">
                <PrefsBar variant="login"/>
                <section className="stage">
                    <div className="brand-row">
                        <span className="mark">C</span>
                        <span className="brand-name">ClawEmail</span>
                        <span className="version">v0.1</span>
                    </div>
                    <div className="pitch">
                        <h1>
                            {t("login.headline.1")}<br/>
                            <span className="lime">{t("login.headline.2")}</span>
                        </h1>
                        <p>{t("login.pitch")}</p>
                    </div>
                    <div className="stamp">
                        {t("login.stamp.session")} · {stamp} utc+8
                        <span style={{marginLeft: 14, color: "var(--accent-fg)"}}>● {t("login.stamp.online")}</span>
                    </div>
                </section>

                <section className="login-form">
                    <div className="field">
                        <label>{t("login.field.password")}</label>
                        <div className="login-password-row">
                            <input
                                type="password"
                                autoFocus
                                value={loginInput}
                                placeholder={t("login.placeholder.password")}
                                disabled={loginBusy}
                                onChange={(event) => {
                                    setLoginInput(event.target.value);
                                    setLoginError("");
                                }}
                                onKeyDown={(event) => {
                                    if (event.key === "Enter") handleLogin();
                                }}
                            />
                            <button
                                className="primary"
                                onClick={() => handleLogin()}
                                disabled={loginBusy || !loginInput}
                            >
                                {loginBusy ? t("login.btn.verifying") : t("login.btn.enter")}
                            </button>
                        </div>
                    </div>
                    {loginError && <div className="err" style={{marginTop: 18}}>{loginError}</div>}
                </section>
            </main>
        );
    }

    const mailTotal = mails.length;
    const readCount = mails.filter((mail) => mail.read_at).length;
    const unreadCount = mailTotal - readCount;
    const onlineConnections = activeConnections.filter((connection) => connection.connected).length;
    const activeDuckAddressCount = duckAddresses.filter((address) => address.status === "active").length;

    return (
        <main className="app-shell resource-shell">
            <header className="app-topbar">
                <div className="topbar-brand">
                    <span className="brand-mark">C</span>
                    <span>ClawEmail</span>
                </div>
                <div className="topbar-actions">
                    <div className="account-pill">
                        <span>{t("rail.admin")}</span>
                    </div>
                    <PrefsBar variant="rail"/>
                    <button className="top-icon danger" title={t("rail.logout")} aria-label={t("rail.logout")}
                            onClick={handleLogout}>
                        <LogOut aria-hidden="true"/>
                    </button>
                </div>
            </header>

            <div className="toast-stack" aria-live="polite" aria-atomic="false">
                {toasts.map((toast) => (
                    <div className={`toast toast-${toast.type}`} key={toast.id}>
                        <span className="toast-dot" aria-hidden="true"/>
                        <span>{toast.message}</span>
                        <button
                            type="button"
                            className="toast-close"
                            onClick={() => removeToast(toast.id)}
                            aria-label="关闭消息"
                        >
                            ×
                        </button>
                    </div>
                ))}
            </div>

            <aside className="rail resource-rail">
                <nav>
                    <button className={view === "dashboard" ? "active" : ""} onClick={() => setView("dashboard")}>
                        <span>仪表盘</span>
                    </button>
                    <button className={view === "inbox" ? "active" : ""} onClick={() => setView("inbox")}>
                        <span>收件管理</span>
                        <span className="count">{unreadCount}</span>
                    </button>
                    <button className={view === "connections" ? "active" : ""} onClick={() => setView("connections")}>
                        <span>连接管理</span>
                        <span className="count">{activeConnections.length}</span>
                    </button>
                    <button className={view === "mailboxes" ? "active" : ""} onClick={() => setView("mailboxes")}>
                        <span>Claw 邮箱</span>
                        <span className="count">{activeMailboxes.length}</span>
                    </button>
                    <button className={view === "duck" ? "active" : ""} onClick={() => setView("duck")}>
                        <span>Duck 邮箱</span>
                        <span className="count">{activeDuckAddressCount}</span>
                    </button>
                    <button className={view === "accountPush" ? "active" : ""} onClick={() => setView("accountPush")}>
                        <span>账号推送</span>
                    </button>
                    <button className={view === "notifications" ? "active" : ""}
                            onClick={() => setView("notifications")}>
                        <span>消息通知</span>
                    </button>
                    <button className={view === "settings" ? "active" : ""} onClick={() => setView("settings")}>
                        <span>系统设置</span>
                    </button>
                </nav>

                <div className="sidebar-status">
                    <strong>全局状态</strong>
                    <div className="health-row">
                        <span>连接在线</span><span>{onlineConnections} / {activeConnections.length}</span></div>
                    <div className="health-row">
                        <span>监听通道</span><span>{listenerSummary.running} / {listenerSummary.total}</span></div>
                    <div className="health-row"><span>Duck 邮箱</span><span>{activeDuckAddressCount}</span></div>
                    <div className="health-row"><span>待处理异常</span><span>{listenerSummary.errors}</span></div>
                    <div className="health-row"><span>邮件总共</span><span>{mailTotal}</span></div>
                    <div className="health-row"><span>已读/未读</span><span>{readCount} / {unreadCount}</span></div>
                </div>
            </aside>

            <section className={`work resource-work ${view === "inbox" ? "inbox-work" : ""}`}>
                <header className="work-head">
                    <div className="meta">
                        <h1 className="h-display">{titleForView(view)}</h1>
                    </div>
                    <div className="actions">
                        {view === "mailboxes" && (
                            <Select
                                value={selectedConnectionId || ALL_SELECT_VALUE}
                                onValueChange={(value) => setSelectedConnectionId(value === ALL_SELECT_VALUE ? "" : value)}
                            >
                                <SelectTrigger className="toolbar-select">
                                    <SelectValue placeholder="全部连接"/>
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={ALL_SELECT_VALUE}>全部连接</SelectItem>
                                    {activeConnections.flatMap((connection) => {
                                        if (!connection.id) return [];
                                        return (
                                            <SelectItem key={connection.id} value={connection.id}>
                                                {connection.label ?? connection.userEmail ?? connection.workspaceName ?? connection.id}
                                            </SelectItem>
                                        );
                                    })}
                                </SelectContent>
                            </Select>
                        )}
                        {view === "inbox" && (
                            <Select
                                value={selectedMailbox || ALL_SELECT_VALUE}
                                onValueChange={handleSelectInboxMailbox}
                            >
                                <SelectTrigger className="toolbar-select mailbox-select">
                                    <SelectValue placeholder="全部邮箱"/>
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={ALL_SELECT_VALUE}>全部邮箱</SelectItem>
                                    {activeMailboxes.map((mailbox) => (
                                        <SelectItem key={mailbox.id} value={mailbox.email}>{mailbox.email}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        )}
                        {view === "inbox" && (
                            <button
                                className="primary"
                                onClick={() => setComposeOpen(true)}
                                disabled={!selectedMailbox}
                            >
                                写信
                            </button>
                        )}
                        {view === "mailboxes" && (
                            <button
                                className={`sync-btn ${mailboxSyncBusy ? "syncing" : ""}`}
                                onClick={handleSyncMailboxes}
                                disabled={mailboxSyncBusy || ( selectedConnectionId ? !selectedConnection?.hasDashboardCookie : activeConnections.length === 0 )}
                                title={t("toolbar.syncHint")}
                                aria-busy={mailboxSyncBusy}
                            >
                                <span className="sync-icon" aria-hidden="true">↻</span>
                                <span>{mailboxSyncBusy ? t("toolbar.syncing") : t("toolbar.sync")}</span>
                            </button>
                        )}
                        {view === "duck" && activeDuckAccounts.length > 0 && selectedDuckAccount && (
                            <>
                                <Select
                                    value={selectedDuckAccount.id}
                                    onValueChange={(value) => setSelectedDuckAccountId(value)}
                                >
                                    <SelectTrigger className="toolbar-select">
                                        <SelectValue placeholder="Duck Token"/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        {activeDuckAccounts.map((account) => (
                                            <SelectItem key={account.id} value={account.id}>
                                                {account.label}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <button
                                    onClick={() => {
                                        setDuckAccountToUpdate(selectedDuckAccount);
                                        setDuckTokenUpdate("");
                                    }}
                                    disabled={duckBusy}
                                >
                                    更新当前 Token
                                </button>
                                <button
                                    className="danger"
                                    onClick={() => setDuckAccountToRemove(selectedDuckAccount)}
                                    disabled={duckBusy}
                                >
                                    删除当前 Token
                                </button>
                            </>
                        )}
                    </div>
                </header>

                {view === "dashboard" && (
                    <section className="dashboard-page">
                        <div className="hero-board">
                            <h2>{onlineConnections} 个连接正在服务 {activeMailboxes.length} 个子邮箱，当前列表共 {mailTotal} 封邮件。</h2>
                            <p>
                                {listenerSummary.errors > 0 ? "存在监听异常，建议先进入连接管理处理。" : "连接和监听状态正常，可以继续管理邮箱或查看收件。"}
                                {selectedMailbox ? ` 当前邮件统计范围：${selectedMailbox}。` : " 当前邮件统计范围：全部邮箱。"}
                            </p>
                            <div className="hero-actions">
                                <button className="primary" onClick={() => setView("connections")}>查看连接</button>
                                <button onClick={() => setView("mailboxes")}>管理邮箱</button>
                                <button onClick={() => setView("inbox")}>查看邮件</button>
                                <button
                                    onClick={() => loadMails(selectedMailbox, true, mailConnectionFilter(selectedMailbox)).catch(reportError)}>刷新统计
                                </button>
                            </div>
                        </div>
                        <div className="stats-grid">
                            <div className="stat-card">
                                <span>连接在线</span><strong>{onlineConnections} / {activeConnections.length}</strong>
                            </div>
                            <div className="stat-card">
                                <span>监听通道</span><strong>{listenerSummary.running} / {listenerSummary.total}</strong>
                            </div>
                            <div className="stat-card"><span>子邮箱总数</span><strong>{activeMailboxes.length}</strong>
                            </div>
                            <div className="stat-card"><span>Duck 邮箱</span><strong>{activeDuckAddressCount}</strong>
                            </div>
                            <div className="stat-card">
                                <span>当前列表总共</span><strong>{mailTotal}</strong>
                            </div>
                            <div className="stat-card">
                                <span>已读/未读</span><strong>{readCount} / {unreadCount}</strong>
                            </div>
                        </div>
                    </section>
                )}

                {view === "connections" && (
                    <section className="connections-page">
                        <div className="connection-bind">
                            <div>
                                <strong>新增连接</strong>
                                <p>连接管理只负责绑定、刷新、断开和监听健康。</p>
                            </div>
                            <div className="login-name-field">
                                <input
                                    type="text"
                                    inputMode="email"
                                    value={clawLoginName}
                                    onChange={(event) => setClawLoginName(normalizeLoginName(event.target.value))}
                                    placeholder={t("conn.input.email")}
                                    disabled={clawBusy}
                                />
                                <span>{CLAW_LOGIN_DOMAIN}</span>
                            </div>
                            {clawCodeSent && (
                                <input
                                    value={clawLoginCode}
                                    onChange={(event) => setClawLoginCode(event.target.value.replace(/\D/g, ""))}
                                    placeholder={t("conn.input.code")}
                                    disabled={clawBusy}
                                />
                            )}
                            <div className="actions">
                                <button onClick={handleSendClawCode} disabled={clawBusy || !clawLoginName}>
                                    {clawCodeSent ? t("conn.action.resendCode") : t("conn.action.sendCode")}
                                </button>
                                {clawCodeSent && (
                                    <button className="primary" onClick={handleVerifyClawCode}
                                            disabled={clawBusy || !clawLoginCode}>
                                        {t("conn.action.bind")}
                                    </button>
                                )}
                            </div>
                        </div>

                        <div className="connection-grid">
                            {connections.map((connection) => (
                                <article className="connection-card"
                                         key={connection.id ?? connection.userEmail ?? "unknown"}>
                                    <div className="card-head">
                                        <div>
                                            <h2>{connection.label ?? connection.userEmail ?? connection.workspaceName ?? "未命名连接"}</h2>
                                            <div
                                                className="subtle">{connection.workspaceName ?? connection.workspaceId ?? "未识别工作区"}</div>
                                        </div>
                                        <span
                                            className={`tag ${connection.connected ? "ok" : connection.status === "disconnected" ? "muted" : "danger"}`}>
                      <span
                          className={`dot ${connection.connected ? "live" : connection.status === "disconnected" ? "" : "danger"}`}/>
                                            {statusLabel(connection)}
                    </span>
                                    </div>
                                    <dl className="connection-meta">
                                        <dt>账号</dt>
                                        <dd>{connection.userEmail ?? "—"}</dd>
                                        <dt>根邮箱</dt>
                                        <dd>{connection.rootPrefix && connection.domain ? `${connection.rootPrefix}@${connection.domain}` : "—"}</dd>
                                        <dt>API Key</dt>
                                        <dd>{connection.apiKeyPrefix ? `${connection.apiKeyPrefix}···${connection.apiKeySuffix}` : "—"}</dd>
                                    </dl>
                                    <div className="card-actions">
                                        <button onClick={() => connection.id && handleRefreshConnection(connection.id)}
                                                disabled={clawBusy || !connection.id}>刷新
                                        </button>
                                        <button onClick={() => handleOpenConnectionListeners(connection)}>监听</button>
                                        <button className="danger"
                                                onClick={() => connection.id && handleDisconnectConnection(connection.id)}
                                                disabled={clawBusy || !connection.id || connection.status === "disconnected"}>断开
                                        </button>
                                    </div>
                                </article>
                            ))}
                        </div>
                    </section>
                )}

                {view === "mailboxes" && (
                    <MailboxesView
                        mailboxes={visibleMailboxes}
                        clawAuth={selectedConnection}
                        suffix={suffix}
                        setSuffix={setSuffix}
                        onCreate={handleCreateMailbox}
                        onDelete={handleDeleteMailbox}
                        onOpen={(mailbox) => {
                            setSelectedConnectionId(mailbox.connection_id ?? "");
                            setSelectedMailbox(mailbox.email);
                            setView("inbox");
                        }}
                        onConfigureRules={(mailbox) => setRulesMailbox(mailbox)}
                    />
                )}

                {view === "duck" && (
                    <section className="duck-page">
                        <div className="duck-bind">
                            <div>
                                <strong>绑定 Duck Token</strong>
                                <p>Token 只保存在后端数据库，前端只显示掩码。接口来自 DuckDuckGo Email Protection
                                    的非公开地址生成请求。</p>
                            </div>
                            <input
                                value={duckLabel}
                                onChange={(event) => setDuckLabel(event.target.value)}
                                placeholder="标签，例如 DDG 主账号"
                                disabled={duckBusy}
                            />
                            <input
                                type="password"
                                value={duckToken}
                                onChange={(event) => setDuckToken(event.target.value)}
                                placeholder="Bearer Token"
                                disabled={duckBusy}
                            />
                            <button
                                className="primary"
                                onClick={handleCreateDuckAccount}
                                disabled={duckBusy || !duckLabel.trim() || !duckToken.trim()}
                            >
                                保存 Token
                            </button>
                        </div>

                        {activeDuckAccounts.length === 0 ? (
                            <div className="empty-state">
                                <span className="big">暂无 Duck Token</span>
                                先从 DuckDuckGo 扩展或页面请求里复制 Authorization Bearer Token，然后保存到这里。
                            </div>
                        ) : (
                            <>
                                <div className="duck-generate">
                                    <div>
                                        <strong>生成 Private Duck Address</strong>
                                        <p>{selectedDuckAccount ? `${selectedDuckAccount.label} · ${selectedDuckAccount.token_prefix ?? "********"}···${selectedDuckAccount.token_suffix ?? "****"}` : "请选择 Duck Token"}</p>
                                    </div>
                                    <Select
                                        value={duckForwardingMailbox || ALL_SELECT_VALUE}
                                        onValueChange={(value) => setDuckForwardingMailbox(value === ALL_SELECT_VALUE ? "" : value)}
                                        disabled={activeMailboxes.length === 0}
                                    >
                                        <SelectTrigger className="toolbar-select mailbox-select">
                                            <SelectValue placeholder="转发目标"/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            {activeMailboxes.map((mailbox) => (
                                                <SelectItem key={mailbox.id}
                                                            value={mailbox.email}>{mailbox.email}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <input
                                        value={duckNote}
                                        onChange={(event) => setDuckNote(event.target.value)}
                                        placeholder="备注，例如注册用途"
                                        disabled={duckBusy}
                                    />
                                    <button
                                        className="primary"
                                        onClick={handleGenerateDuckAddress}
                                        disabled={duckBusy || !selectedDuckAccount}
                                    >
                                        {duckBusy ? "生成中" : "生成地址"}
                                    </button>
                                </div>
                                <div className="duck-note-line">
                                    <span>
                                        Duck 邮箱记录：{selectedDuckAccount?.label ?? "未选择 Token"} · {visibleDuckAddresses.length} 条
                                    </span>
                                </div>

                                <div className="duck-table">
                                    <div className="duck-row head">
                                        <span>Duck 邮箱</span>
                                        <span>目标邮箱</span>
                                        <span>备注</span>
                                        <span>创建于</span>
                                        <span>操作</span>
                                    </div>
                                    {visibleDuckAddresses.length === 0 ? (
                                        <div className="empty-state">
                                            <span className="big">暂无生成记录</span>
                                            点击“生成地址”后会保存到这里。
                                        </div>
                                    ) : visibleDuckAddresses.map((item) => (
                                        <div className="duck-row" key={item.id}>
                                            <div className="email-cell">
                                                <span className="e">{item.address}</span>
                                                <span className="pref">本地记录</span>
                                            </div>
                                            <span className="time-cell">{item.forwarding_mailbox_email ?? "—"}</span>
                                            <span className="time-cell">{item.note ?? "—"}</span>
                                            <span className="time-cell">{item.created_at}</span>
                                            <div className="ops">
                                                <button
                                                    onClick={() => navigator.clipboard.writeText(item.address)}>复制
                                                </button>
                                                <button onClick={() => setDuckAddressToDelete(item)}>删除记录</button>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </>
                        )}
                    </section>
                )}

                {view === "inbox" && (
                    <InboxView
                        selectedMailbox={selectedMailbox}
                        mails={mails}
                        selectedMail={selectedMail}
                        onSelectMail={(id) => loadMail(id).catch(reportError)}
                        onRefresh={() => loadMails(selectedMailbox, true, mailConnectionFilter(selectedMailbox)).catch(reportError)}
                        onDeleted={(id, msg) => {
                            setMails((items) => items.filter((mail) => mail.id !== id));
                            setSelectedMail(null);
                            showStatus(msg);
                        }}
                        onReplied={(msg) => showStatus(msg)}
                        onError={reportError}
                        adminPassword={password}
                    />
                )}

                {view === "accountPush" && (
                    <section className="account-push-page">
                        <div className="push-panel">
                            <div className="push-head">
                                <div>
                                    <strong>ChatGPT 账号推送</strong>
                                    <p>粘贴 https://chatgpt.com/api/auth/session 结构的账号 JSON，系统会转换成 Sub2
                                        数据格式后推送到 Sub2API。</p>
                                </div>
                                <span
                                    className={`tag ${sub2Settings.apiUrl && sub2Settings.hasApiKey ? "ok" : "muted"}`}>
                                    <span
                                        className={`dot ${sub2Settings.apiUrl && sub2Settings.hasApiKey ? "live" : ""}`}/>
                                    {sub2Settings.apiUrl && sub2Settings.hasApiKey ? "已配置" : "未配置"}
                                </span>
                            </div>
                            <div className="push-grid">
                                <div className="push-editor">
                                    <div className="push-editor-head">
                                        <span>原始 JSON</span>
                                        <button
                                            onClick={() => {
                                                setSub2SourceJson("");
                                                setSub2PreviewJson("");
                                            }}
                                            disabled={sub2Busy || ( !sub2SourceJson && !sub2PreviewJson )}
                                        >
                                            清空
                                        </button>
                                    </div>
                                    <textarea
                                        className="push-textarea"
                                        value={sub2SourceJson}
                                        onChange={(event) => setSub2SourceJson(event.target.value)}
                                        placeholder="粘贴 https://chatgpt.com/api/auth/session 结构的账号 JSON 内容"
                                        spellCheck={false}
                                        disabled={sub2Busy}
                                    />
                                </div>
                                <div className="push-editor">
                                    <div className="push-editor-head">
                                        <span>Sub2 JSON 预览</span>
                                        <button
                                            onClick={() => sub2PreviewJson && navigator.clipboard.writeText(sub2PreviewJson)}
                                            disabled={!sub2PreviewJson}
                                        >
                                            复制
                                        </button>
                                    </div>
                                    <textarea
                                        className="push-textarea preview"
                                        value={sub2PreviewJson}
                                        readOnly
                                        placeholder="点击转换后显示 toSub2.json 格式"
                                        spellCheck={false}
                                    />
                                </div>
                            </div>
                            <div className="push-actions">
                                <span>
                                    {sub2Settings.apiUrl
                                        ? `推送地址：${sub2Settings.apiUrl}`
                                        : "请先在系统设置里配置 Sub2API 地址和 APIKey"}
                                </span>
                                <div className="push-group-select">
                                    <span>推送分组</span>
                                    <Select
                                        value={selectedSub2GroupId}
                                        onValueChange={setSelectedSub2GroupId}
                                        disabled={sub2Busy || sub2GroupsBusy || !sub2Groups.length}
                                    >
                                        <SelectTrigger className="toolbar-select push-group-trigger">
                                            <SelectValue placeholder={sub2GroupsBusy ? "加载中" : "选择分组"} />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {sub2Groups.map((group) => (
                                                <SelectItem key={group.id} value={String(group.id)}>
                                                    {group.name ? `${group.name} (#${group.id})` : `分组 #${group.id}`}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    {!sub2GroupsBusy && !sub2Groups.length && <span className="push-group-empty">暂无可用分组</span>}
                                </div>
                                <div>
                                    <button
                                        onClick={loadSub2Groups}
                                        disabled={sub2Busy || sub2GroupsBusy || !sub2Settings.apiUrl || !sub2Settings.hasApiKey}
                                    >
                                        {sub2GroupsBusy ? "刷新中" : "刷新分组"}
                                    </button>
                                    <button onClick={handleConvertSub2Account}
                                            disabled={sub2Busy || !sub2SourceJson.trim()}>
                                        {sub2Busy ? "处理中" : "转换预览"}
                                    </button>
                                    <button
                                        className="primary"
                                        onClick={handlePushSub2Account}
                                        disabled={sub2Busy || !sub2SourceJson.trim() || !selectedSub2GroupId}
                                    >
                                        {sub2Busy ? "推送中" : "推送账号"}
                                    </button>
                                </div>
                            </div>
                            {( !sub2Settings.apiUrl || !sub2Settings.hasApiKey ) && (
                                <div className="empty-state">
                                    <span className="big">Sub2API 尚未配置完整</span>
                                    请先到系统设置里填写 Sub2API 地址和 APIKey。
                                </div>
                            )}
                        </div>
                    </section>
                )}

                {view === "notifications" && (
                    <section className="notifications-page">
                        <div className="notify-panel">
                            <div className="notify-head">
                                <div>
                                    <strong>Telegram 消息发送</strong>
                                    <p>把要转发的内容粘贴到这里，点击发送后由已配置的机器人发送到指定 Chat。</p>
                                </div>
                                <span
                                    className={`tag ${telegramSettings.enabled && telegramSettings.hasBotToken && telegramSettings.chatId ? "ok" : "muted"}`}>
                                    <span
                                        className={`dot ${telegramSettings.enabled && telegramSettings.hasBotToken && telegramSettings.chatId ? "live" : ""}`}/>
                                    {telegramSettings.enabled ? "已启用" : "未启用"}
                                </span>
                            </div>
                            <textarea
                                className="notify-textarea"
                                value={telegramMessage}
                                onChange={(event) => setTelegramMessage(event.target.value)}
                                placeholder="输入要发送到 Telegram 的内容"
                                maxLength={4096}
                                disabled={telegramSendBusy}
                            />
                            <div className="notify-actions">
                                <span>{telegramMessage.trim().length} / 4096</span>
                                <button
                                    className="primary"
                                    onClick={handleSendTelegramMessage}
                                    disabled={telegramSendBusy || !telegramMessage.trim()}
                                >
                                    {telegramSendBusy ? "发送中" : "发送消息"}
                                </button>
                            </div>
                            {( !telegramSettings.enabled || !telegramSettings.hasBotToken || !telegramSettings.chatId ) && (
                                <div className="empty-state">
                                    <span className="big">Telegram 尚未配置完整</span>
                                    请先到系统设置里填写 Bot Token、Chat ID 并启用消息通知。
                                </div>
                            )}
                        </div>
                    </section>
                )}

                {view === "settings" && (
                    <section className="settings-page">
                        <div className="settings-panel">
                            <div>
                                <strong>监听与刷新</strong>
                                <p>这些设置只保存在当前浏览器，用来控制前端提示和刷新频率。</p>
                            </div>
                            <label className="setting-row">
                                <span>
                                    <strong>监听重连提示</strong>
                                    <small>实时通道断开时是否弹出右上角消息。</small>
                                </span>
                                <input
                                    type="checkbox"
                                    checked={showListenerReconnectNotice}
                                    onChange={(event) => setShowListenerReconnectNotice(event.target.checked)}
                                />
                            </label>
                            <label className="setting-row">
                                <span>
                                    <strong>收件自动刷新</strong>
                                    <small>收到实时新邮件事件后自动刷新当前收件列表。</small>
                                </span>
                                <input
                                    type="checkbox"
                                    checked={inboxAutoRefresh}
                                    onChange={(event) => setInboxAutoRefresh(event.target.checked)}
                                />
                            </label>
                            <div className="setting-row">
                                <span>
                                    <strong>监听状态刷新</strong>
                                    <small>控制连接管理里监听状态的自动刷新频率。</small>
                                </span>
                                <Select
                                    value={listenerStatusRefreshInterval}
                                    onValueChange={(value) => setListenerStatusRefreshInterval(value as ListenerStatusRefreshInterval)}
                                >
                                    <SelectTrigger className="toolbar-select">
                                        <SelectValue placeholder="刷新频率"/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="manual">仅手动</SelectItem>
                                        <SelectItem value="30">30 秒</SelectItem>
                                        <SelectItem value="60">60 秒</SelectItem>
                                        <SelectItem value="300">5 分钟</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="setting-row">
                                <span>
                                    <strong>服务端控制台日志</strong>
                                    <small>控制后端是否输出监听连接/断开流水。默认静默，只保留真正异常。</small>
                                </span>
                                <Select
                                    value={serverListenerSettings.logMode}
                                    onValueChange={(value) => setServerListenerSettings({
                                        ...serverListenerSettings,
                                        logMode: value as ListenerSettings["logMode"]
                                    })}
                                    disabled={settingsBusy}
                                >
                                    <SelectTrigger className="toolbar-select">
                                        <SelectValue placeholder="日志模式"/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="quiet">静默</SelectItem>
                                        <SelectItem value="lifecycle">连接/断开</SelectItem>
                                        <SelectItem value="verbose">详细</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="setting-row">
                                <span>
                                    <strong>服务端重连策略</strong>
                                    <small>控制后端邮箱 WebSocket 断线后的重连间隔。</small>
                                </span>
                                <Select
                                    value={serverListenerSettings.reconnectMode}
                                    onValueChange={(value) => setServerListenerSettings({
                                        ...serverListenerSettings,
                                        reconnectMode: value as ListenerSettings["reconnectMode"]
                                    })}
                                    disabled={settingsBusy}
                                >
                                    <SelectTrigger className="toolbar-select">
                                        <SelectValue placeholder="重连策略"/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="standard">标准</SelectItem>
                                        <SelectItem value="slow">慢速</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="setting-row">
                                <span>
                                    <strong>系统代理地址</strong>
                                    <small>容器无法直连外部服务时填写，例如 http://host.docker.internal:7890。Duck 和 Telegram 都会复用该代理。</small>
                                </span>
                                <input
                                    value={systemProxyInput}
                                    onChange={(event) => setSystemProxyInput(event.target.value)}
                                    placeholder="http://host:port"
                                    disabled={settingsBusy}
                                />
                            </div>
                            <div className="setting-row">
                                <span>
                                    <strong>系统请求超时</strong>
                                    <small>访问外部服务时等待响应的最长时间，单位毫秒。</small>
                                </span>
                                <input
                                    type="number"
                                    min={1000}
                                    max={120000}
                                    step={1000}
                                    value={systemTimeoutInput}
                                    onChange={(event) => setSystemTimeoutInput(event.target.value)}
                                    disabled={settingsBusy}
                                />
                            </div>
                            <label className="setting-row">
                                <span>
                                    <strong>Telegram 消息通知</strong>
                                    <small>启用后可在“消息通知”菜单里手动发送内容到 Telegram。</small>
                                </span>
                                <input
                                    type="checkbox"
                                    checked={telegramEnabledInput}
                                    onChange={(event) => setTelegramEnabledInput(event.target.checked)}
                                    disabled={settingsBusy}
                                />
                            </label>
                            <div className="setting-row">
                                <span>
                                    <strong>Telegram Bot Token</strong>
                                    <small>{telegramSettings.botTokenPreview ? `当前已配置：${telegramSettings.botTokenPreview}` : "从 BotFather 获取，保存后不会明文显示。"}</small>
                                </span>
                                <input
                                    type="password"
                                    value={telegramBotTokenInput}
                                    onChange={(event) => setTelegramBotTokenInput(event.target.value)}
                                    placeholder={telegramSettings.hasBotToken ? "留空则保留当前 Token" : "Bot Token"}
                                    disabled={settingsBusy}
                                />
                            </div>
                            <div className="setting-row">
                                <span>
                                    <strong>Telegram Chat ID</strong>
                                    <small>私聊、群组或频道的 Chat ID。机器人需要先能向该 Chat 发言。</small>
                                </span>
                                <input
                                    value={telegramChatIdInput}
                                    onChange={(event) => setTelegramChatIdInput(event.target.value)}
                                    placeholder="Chat ID"
                                    disabled={settingsBusy}
                                />
                            </div>
                            <div className="setting-row">
                                <span>
                                    <strong>Sub2API 地址</strong>
                                    <small>填写 Sub2API 根地址或完整 /admin/accounts/data 导入地址，根地址会自动补齐 /api/v1/admin/accounts/data。</small>
                                </span>
                                <input
                                    value={sub2ApiUrlInput}
                                    onChange={(event) => setSub2ApiUrlInput(event.target.value)}
                                    placeholder="https://sub2.example.com"
                                    disabled={settingsBusy}
                                />
                            </div>
                            <div className="setting-row">
                                <span>
                                    <strong>Sub2API APIKey</strong>
                                    <small>{sub2Settings.apiKeyPreview ? `当前已配置：${sub2Settings.apiKeyPreview}` : "用于调用 Sub2API 管理接口，保存后不会明文显示。"}</small>
                                </span>
                                <input
                                    type="password"
                                    value={sub2ApiKeyInput}
                                    onChange={(event) => setSub2ApiKeyInput(event.target.value)}
                                    placeholder={sub2Settings.hasApiKey ? "留空则保留当前 APIKey" : "APIKey"}
                                    disabled={settingsBusy}
                                />
                            </div>
                            <div className="settings-actions">
                                <button onClick={() => loadListeners()} disabled={listenerBusy}>
                                    {listenerBusy ? "刷新中" : "立即刷新监听状态"}
                                </button>
                                <button
                                    className="primary"
                                    onClick={handleSaveSettings}
                                    disabled={settingsBusy}
                                >
                                    {settingsBusy ? "保存中" : "保存配置"}
                                </button>
                            </div>
                        </div>
                    </section>
                )}
            </section>

            <ComposeDrawer
                open={composeOpen}
                fromMailbox={selectedMailbox}
                onClose={() => setComposeOpen(false)}
                onSent={(msg) => showStatus(msg)}
                onError={reportError}
            />

            <CommunicationRulesDrawer
                open={Boolean(rulesMailbox)}
                mailbox={rulesMailbox}
                onClose={() => setRulesMailbox(null)}
                onSaved={(updated, msg) => {
                    setMailboxes((items) => items.map((item) => item.id === updated.id ? updated : item));
                    setRulesMailbox(null);
                    showStatus(msg);
                }}
                onError={reportError}
            />

            <ListenersDrawer
                open={listenersDrawerOpen}
                busy={listenerBusy}
                items={visibleListeners}
                onClose={() => setListenersDrawerOpen(false)}
                onRefresh={loadListeners}
            />

            <Dialog
                open={Boolean(duckAccountToRemove)}
                onOpenChange={(open) => {
                    if (!duckBusy && !open) setDuckAccountToRemove(null);
                }}
            >
                {duckAccountToRemove ? (
                    <DialogContent
                        className="confirm-dialog"
                        onEscapeKeyDown={(event) => {
                            if (duckBusy) event.preventDefault();
                        }}
                        onPointerDownOutside={(event) => {
                            if (duckBusy) event.preventDefault();
                        }}
                    >
                        <div className="confirm-copy">
                            <DialogTitle>移除 Duck Token</DialogTitle>
                            <DialogDescription>
                                这会从本地数据库删除该 Token 以及它下面的生成记录。此操作不会调用 DuckDuckGo 远端停用地址。
                            </DialogDescription>
                        </div>
                        <div className="confirm-mail">
                            <strong>{duckAccountToRemove.label}</strong>
                            <span className="mono">
                                {duckAccountToRemove.token_prefix
                                    ? `${duckAccountToRemove.token_prefix}···${duckAccountToRemove.token_suffix}`
                                    : "未保存掩码"}
                            </span>
                        </div>
                        <div className="confirm-actions">
                            <Button
                                variant="ghost"
                                onClick={() => setDuckAccountToRemove(null)}
                                disabled={duckBusy}
                            >
                                取消
                            </Button>
                            <Button
                                variant="danger"
                                onClick={handleDisableDuckAccount}
                                disabled={duckBusy}
                            >
                                {duckBusy ? "删除中..." : "确认删除"}
                            </Button>
                        </div>
                    </DialogContent>
                ) : null}
            </Dialog>

            <Dialog
                open={Boolean(duckAddressToDelete)}
                onOpenChange={(open) => {
                    if (!duckBusy && !open) setDuckAddressToDelete(null);
                }}
            >
                {duckAddressToDelete ? (
                    <DialogContent
                        className="confirm-dialog"
                        onEscapeKeyDown={(event) => {
                            if (duckBusy) event.preventDefault();
                        }}
                        onPointerDownOutside={(event) => {
                            if (duckBusy) event.preventDefault();
                        }}
                    >
                        <div className="confirm-copy">
                            <DialogTitle>删除 Duck 邮箱记录</DialogTitle>
                            <DialogDescription>
                                这只会删除本项目保存的生成记录，不会停用 DuckDuckGo 远端邮箱，也不会影响已经生成邮箱的转发能力。
                            </DialogDescription>
                        </div>
                        <div className="confirm-mail">
                            <strong>{duckAddressToDelete.address}</strong>
                            <span className="mono">
                                目标邮箱：{duckAddressToDelete.forwarding_mailbox_email ?? "未记录"}
                            </span>
                        </div>
                        <div className="confirm-actions">
                            <Button
                                variant="ghost"
                                onClick={() => setDuckAddressToDelete(null)}
                                disabled={duckBusy}
                            >
                                取消
                            </Button>
                            <Button
                                variant="danger"
                                onClick={handleDeleteDuckAddress}
                                disabled={duckBusy}
                            >
                                {duckBusy ? "删除中..." : "确认删除"}
                            </Button>
                        </div>
                    </DialogContent>
                ) : null}
            </Dialog>

            <Dialog
                open={Boolean(duckAccountToUpdate)}
                onOpenChange={(open) => {
                    if (!duckBusy && !open) {
                        setDuckAccountToUpdate(null);
                        setDuckTokenUpdate("");
                    }
                }}
            >
                {duckAccountToUpdate ? (
                    <DialogContent
                        className="confirm-dialog"
                        onEscapeKeyDown={(event) => {
                            if (duckBusy) event.preventDefault();
                        }}
                        onPointerDownOutside={(event) => {
                            if (duckBusy) event.preventDefault();
                        }}
                    >
                        <div className="confirm-copy">
                            <DialogTitle>更新 Duck Token</DialogTitle>
                            <DialogDescription>
                                替换当前保存的 Bearer Token。已有 Duck 邮箱记录会保留，后续生成邮箱会使用新 Token。
                            </DialogDescription>
                        </div>
                        <div className="confirm-mail">
                            <strong>{duckAccountToUpdate.label}</strong>
                            <span className="mono">
                                {duckAccountToUpdate.token_prefix
                                    ? `${duckAccountToUpdate.token_prefix}···${duckAccountToUpdate.token_suffix}`
                                    : "未保存掩码"}
                            </span>
                        </div>
                        <div className="confirm-input">
                            <input
                                type="password"
                                autoFocus
                                value={duckTokenUpdate}
                                onChange={(event) => setDuckTokenUpdate(event.target.value)}
                                placeholder="新的 Bearer Token"
                                disabled={duckBusy}
                            />
                        </div>
                        <div className="confirm-actions">
                            <Button
                                variant="ghost"
                                onClick={() => {
                                    setDuckAccountToUpdate(null);
                                    setDuckTokenUpdate("");
                                }}
                                disabled={duckBusy}
                            >
                                取消
                            </Button>
                            <Button
                                variant="primary"
                                onClick={handleUpdateDuckAccountToken}
                                disabled={duckBusy || !duckTokenUpdate.trim()}
                            >
                                {duckBusy ? "更新中..." : "确认更新"}
                            </Button>
                        </div>
                    </DialogContent>
                ) : null}
            </Dialog>
        </main>
    );
}
