import {useEffect, useMemo, useState} from "react";
import {CommunicationRulesDrawer} from "./components/CommunicationRulesDrawer";
import {ComposeDrawer} from "./components/ComposeDrawer";
import {InboxView} from "./components/InboxView";
import {ListenersDrawer} from "./components/ListenersDrawer";
import {MailboxesView} from "./components/MailboxesView";
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from "./components/ui/select";
import {PrefsBar, usePrefs} from "./i18n";
import {
    type ClawAuthStatus,
    createEventSource,
    createMailbox,
    deleteMailbox,
    disconnectConnection,
    fetchConnections,
    fetchListeners,
    fetchMail,
    fetchMailboxes,
    fetchMails,
    getAdminPassword,
    getRuntimeMode,
    type ListenerSnapshot,
    type Mailbox,
    type MailDetail,
    type MailSummary,
    refreshConnection,
    sendConnectionLoginCode,
    setAdminPassword,
    setRuntimeMode,
    verifyAdminPassword,
    verifyConnectionLoginCode
} from "./api";

type View = "dashboard" | "connections" | "mailboxes" | "inbox" | "settings";
const VIEW_STORAGE_KEY = "claw.currentView";
const LIVE_LISTENER_STATUSES = new Set(["running", "open"]);
const CLAW_LOGIN_NAME_PATTERN = /^[^\s@]+$/;
const CLAW_LOGIN_DOMAIN = "@163.com";
const ALL_SELECT_VALUE = "__all";

function readInitialView(): View {
    if (typeof localStorage === "undefined") return "dashboard";
    const saved = localStorage.getItem(VIEW_STORAGE_KEY);
    return saved === "connections" || saved === "mailboxes" || saved === "inbox" || saved === "settings"
        ? saved
        : "dashboard";
}

function titleForView(view: View): string {
    const map: Record<View, string> = {
        dashboard: "仪表盘",
        connections: "连接管理",
        mailboxes: "邮箱管理",
        inbox: "收件管理",
        settings: "设置"
    };
    return map[ view ];
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

    const [status, setStatus] = useState("");
    const [error, setError] = useState("");

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

    const activeConnections = useMemo(
        () => connections.filter((connection) => connection.status !== "disconnected"),
        [connections]
    );

    const selectedConnection = useMemo(
        () => connections.find((connection) => connection.id === selectedConnectionId) ?? activeConnections[ 0 ] ?? null,
        [activeConnections, connections, selectedConnectionId]
    );

    const activeMailboxes = useMemo(
        () => mailboxes.filter((mailbox) => mailbox.status !== "deleted"),
        [mailboxes]
    );

    const visibleMailboxes = useMemo(() => (
        selectedConnection?.id
            ? activeMailboxes.filter((mailbox) => mailbox.connection_id === selectedConnection.id)
            : activeMailboxes
    ), [activeMailboxes, selectedConnection?.id]);

    const visibleListeners = useMemo(() => (
        selectedConnection?.id
            ? listenerItems.filter((item) => item.connectionId === selectedConnection.id)
            : listenerItems
    ), [listenerItems, selectedConnection?.id]);

    const listenerSummary = useMemo(() => {
        let running = 0;
        let errors = 0;
        for (const item of visibleListeners) {
            if (LIVE_LISTENER_STATUSES.has(item.status)) running++;
            if (item.status === "error" || item.error) errors++;
        }
        return {running, total: visibleListeners.length, errors};
    }, [visibleListeners]);

    function reportError(err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
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
            setError("");
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
            return items.find((item) => item.status !== "disconnected")?.id ?? "";
        });
        return items;
    }

    async function loadMailboxes(sync = false, connectionId = selectedConnection?.id): Promise<Mailbox[]> {
        setError("");
        const items = await fetchMailboxes(sync, sync ? connectionId ?? undefined : undefined);
        setMailboxes(items);
        return items;
    }

    async function loadMails(mailbox = selectedMailbox, sync = false, connectionId = selectedConnection?.id) {
        setError("");
        const data = await fetchMails(mailbox || undefined, 50, 0, sync, connectionId ?? undefined);
        setMails(data.items);
        if (selectedMail && !data.items.some((mail) => mail.id === selectedMail.id)) {
            setSelectedMail(null);
        }
    }

    async function loadMail(id: number) {
        setError("");
        const detail = await fetchMail(id);
        setSelectedMail(detail);
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

    useEffect(() => {
        const savedPassword = getAdminPassword();
        if (!savedPassword) return;
        handleLogin(savedPassword);
    }, []);

    useEffect(() => {
        localStorage.setItem(VIEW_STORAGE_KEY, view);
    }, [view]);

    useEffect(() => {
        if (!password) return;
        setAdminPassword(password);
        loadConnections().catch(reportError);
        loadMailboxes().catch(reportError);
    }, [password]);

    useEffect(() => {
        if (!status) return;
        const timer = window.setTimeout(() => setStatus(""), 5000);
        return () => window.clearTimeout(timer);
    }, [status]);

    useEffect(() => {
        if (!password) return;
        if (getRuntimeMode() === "cloudflare") return;
        const events = createEventSource();
        events.addEventListener("mail", () => {
            loadMails().catch(reportError);
        });
        events.addEventListener("cloudflare-mode", () => {
            setRuntimeMode("cloudflare");
            events.close();
            setStatus(t("flash.events.manualSync"));
        });
        events.onerror = () => {
            if (getRuntimeMode() === "cloudflare") return;
            setStatus(t("flash.events.reconnecting"));
        };
        return () => events.close();
    }, [password, selectedConnection?.id, selectedMailbox]);

    useEffect(() => {
        if (!password) return;
        setSelectedMail(null);
        setSelectedMailbox("");
        loadMails("", false).catch(reportError);
        loadListeners();
    }, [password, selectedConnection?.id]);

    async function handleCreateMailbox() {
        setStatus("");
        setError("");
        try {
            const created = await createMailbox(suffix, selectedConnection?.id ?? undefined);
            setSuffix("");
            setStatus(t("flash.mb.created", {email: created.email}));
            await loadMailboxes();
        } catch (err) {
            reportError(err);
        }
    }

    async function handleDeleteMailbox(mailbox: Mailbox) {
        if (!confirm(t("mb.confirm.delete", {email: mailbox.email}))) return;
        setStatus("");
        setError("");
        try {
            await deleteMailbox(mailbox.id);
            setStatus(t("flash.mb.deleted", {email: mailbox.email}));
            await loadMailboxes();
            if (selectedMailbox === mailbox.email) {
                setSelectedMailbox("");
                setMails([]);
            }
        } catch (err) {
            reportError(err);
        }
    }

    async function handleSendClawCode() {
        setStatus("");
        setError("");
        setClawBusy(true);
        try {
            const loginName = normalizeLoginName(clawLoginName);
            if (!CLAW_LOGIN_NAME_PATTERN.test(loginName)) {
                setError(t("conn.error.emailFormat"));
                return;
            }
            setClawLoginName(loginName);
            await sendConnectionLoginCode(loginEmailFromName(loginName));
            setClawCodeSent(true);
            setStatus(t("flash.code.sent"));
        } catch (err) {
            reportError(err);
        } finally {
            setClawBusy(false);
        }
    }

    async function handleVerifyClawCode() {
        setStatus("");
        setError("");
        setClawBusy(true);
        try {
            const loginName = normalizeLoginName(clawLoginName);
            const code = clawLoginCode.trim();
            if (!CLAW_LOGIN_NAME_PATTERN.test(loginName)) {
                setError(t("conn.error.emailFormat"));
                return;
            }
            if (!/^\d+$/.test(code)) {
                setError(t("conn.error.codeFormat"));
                return;
            }
            setClawLoginName(loginName);
            const result = await verifyConnectionLoginCode(loginEmailFromName(loginName), code);
            setClawLoginCode("");
            setClawCodeSent(false);
            setClawLoginName("");
            setStatus(t("flash.claw.bound", {n: result.syncedMailboxes}));
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
        setStatus("");
        setError("");
        setClawBusy(true);
        try {
            const result = await refreshConnection(connectionId);
            setStatus(t("flash.claw.refreshed", {n: result.syncedMailboxes}));
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
        setStatus(t("flash.mb.syncing"));
        setError("");
        setMailboxSyncBusy(true);
        try {
            const items = await loadMailboxes(true, selectedConnection?.id);
            setStatus(t("flash.mb.synced", {
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
        setStatus("");
        setError("");
        setClawBusy(true);
        try {
            await disconnectConnection(connectionId);
            setStatus(t("flash.claw.severed"));
            await loadConnections();
            loadListeners();
        } catch (err) {
            reportError(err);
        } finally {
            setClawBusy(false);
        }
    }

    function handleLogout() {
        setAdminPassword("");
        setPassword("");
        setLoginInput("");
        setLoginError("");
        setConnections([]);
        setSelectedConnectionId("");
        setListenerItems([]);
        setListenersDrawerOpen(false);
        setRulesMailbox(null);
        setMailboxes([]);
        setSelectedMailbox("");
        setMails([]);
        setSelectedMail(null);
        setStatus("");
        setError("");
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
                    </div>
                    <div className="actions">
                        <button
                            className="primary"
                            onClick={() => handleLogin()}
                            disabled={loginBusy || !loginInput}
                        >
                            {loginBusy ? t("login.btn.verifying") : t("login.btn.enter")}
                        </button>
                        <span className="kbd">⏎</span>
                    </div>
                    {loginError && <div className="err" style={{marginTop: 18}}>{loginError}</div>}
                </section>
            </main>
        );
    }

    const unreadCount = mails.length;
    const onlineConnections = activeConnections.filter((connection) => connection.connected).length;

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
                            onClick={handleLogout}>⏻
                    </button>
                </div>
            </header>

            <aside className="rail resource-rail">
                <nav>
                    <button className={view === "dashboard" ? "active" : ""} onClick={() => setView("dashboard")}>
                        <span>仪表盘</span>
                    </button>
                    <button className={view === "connections" ? "active" : ""} onClick={() => setView("connections")}>
                        <span>连接管理</span>
                        <span className="count">{activeConnections.length}</span>
                    </button>
                    <button className={view === "mailboxes" ? "active" : ""} onClick={() => setView("mailboxes")}>
                        <span>邮箱管理</span>
                        <span className="count">{activeMailboxes.length}</span>
                    </button>
                    <button className={view === "inbox" ? "active" : ""} onClick={() => setView("inbox")}>
                        <span>收件管理</span>
                        <span className="count">{unreadCount}</span>
                    </button>
                    <button className={view === "settings" ? "active" : ""} onClick={() => setView("settings")}>
                        <span>设置</span>
                    </button>
                </nav>

                <div className="sidebar-status">
                    <strong>全局状态</strong>
                    <div className="health-row">
                        <span>连接在线</span><span>{onlineConnections} / {activeConnections.length}</span></div>
                    <div className="health-row">
                        <span>监听通道</span><span>{listenerSummary.running} / {listenerSummary.total}</span></div>
                    <div className="health-row"><span>待处理异常</span><span>{listenerSummary.errors}</span></div>
                </div>
            </aside>

            <section className={`work resource-work ${view === "inbox" ? "inbox-work" : ""}`}>
                <header className="work-head">
                    <div className="meta">
                        <h1 className="h-display">{titleForView(view)}</h1>
                    </div>
                    <div className="actions">
                        {( view === "mailboxes" || view === "inbox" ) && (
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
                                onValueChange={(value) => setSelectedMailbox(value === ALL_SELECT_VALUE ? "" : value)}
                            >
                                <SelectTrigger className="toolbar-select mailbox-select">
                                    <SelectValue placeholder="全部邮箱"/>
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={ALL_SELECT_VALUE}>全部邮箱</SelectItem>
                                    {visibleMailboxes.map((mailbox) => (
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
                                disabled={!selectedConnection?.hasDashboardCookie || mailboxSyncBusy}
                                title={t("toolbar.syncHint")}
                                aria-busy={mailboxSyncBusy}
                            >
                                <span className="sync-icon" aria-hidden="true">↻</span>
                                <span>{mailboxSyncBusy ? t("toolbar.syncing") : t("toolbar.sync")}</span>
                            </button>
                        )}
                    </div>
                </header>

                {( status || error ) && (
                    <div className="flash-line">
                        {status && <div className="notice">{status}</div>}
                        {error && <div className="err">{error}</div>}
                    </div>
                )}

                {view === "dashboard" && (
                    <section className="dashboard-page">
                        <div className="hero-board">
                            <h2>{onlineConnections} 个连接正在服务 {activeMailboxes.length} 个子邮箱，当前列表有 {mails.length} 封邮件。</h2>
                            <p>{listenerSummary.errors > 0 ? "存在监听异常，建议先进入连接管理处理。" : "连接和监听状态正常，可以继续管理邮箱或查看收件。"}</p>
                            <div className="hero-actions">
                                <button className="primary" onClick={() => setView("connections")}>查看连接</button>
                                <button onClick={() => setView("mailboxes")}>管理邮箱</button>
                                <button onClick={() => setView("inbox")}>查看邮件</button>
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
                            <div className="stat-card"><span>当前邮件</span><strong>{mails.length}</strong></div>
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
                                        <button onClick={() => setListenersDrawerOpen(true)}>监听</button>
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

                {view === "inbox" && (
                    <InboxView
                        selectedMailbox={selectedMailbox}
                        mails={mails}
                        selectedMail={selectedMail}
                        onSelectMail={(id) => loadMail(id).catch(reportError)}
                        onRefresh={() => loadMails(selectedMailbox, true).catch(reportError)}
                        onDeleted={(id, msg) => {
                            setMails((items) => items.filter((mail) => mail.id !== id));
                            setSelectedMail(null);
                            setStatus(msg);
                        }}
                        onReplied={(msg) => setStatus(msg)}
                        onError={reportError}
                        adminPassword={password}
                    />
                )}

                {view === "settings" && (
                    <section className="settings-page">
                        <div className="empty-state">
                            <span className="big">系统设置</span>
                            语言、主题和退出已经放到顶部右侧。后续默认筛选范围、同步策略等偏好放在这里。
                        </div>
                    </section>
                )}
            </section>

            <ComposeDrawer
                open={composeOpen}
                fromMailbox={selectedMailbox}
                onClose={() => setComposeOpen(false)}
                onSent={(msg) => setStatus(msg)}
                onError={reportError}
            />

            <CommunicationRulesDrawer
                open={Boolean(rulesMailbox)}
                mailbox={rulesMailbox}
                onClose={() => setRulesMailbox(null)}
                onSaved={(updated, msg) => {
                    setMailboxes((items) => items.map((item) => item.id === updated.id ? updated : item));
                    setRulesMailbox(null);
                    setStatus(msg);
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
        </main>
    );
}
