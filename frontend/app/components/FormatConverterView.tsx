import {useMemo, useRef, useState} from "react";
import {
    buildOutputDocument,
    type ConversionResult,
    type ConvertedSession,
    convertFiles,
    convertFromText,
    EXAMPLE_SESSION,
    FORMAT_LABELS,
    FORMAT_OPTIONS,
    formatConverterDisplayDate,
    type FormatConverterOutputFormat,
    getFormatConverterTimestampToken,
    sanitizeFormatConverterFileToken
} from "../lib/session-format-converter";
import {Braces, Copy, Download, FileJson, RotateCcw, UploadCloud} from "lucide-react";
import {type Sub2Group, type Sub2Proxy, type Sub2PushJobStatus, type Sub2PushResult, type Sub2Settings} from "../api";
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from "./ui/select";

type StatusTone = "idle" | "ok" | "error";

type ConverterStatus = {
    tone: StatusTone;
    message: string;
};

type FormatConverterViewProps = {
    sub2Settings: Sub2Settings;
    sub2Groups: Sub2Group[];
    selectedSub2GroupId: string;
    sub2GroupsBusy: boolean;
    sub2Proxies: Sub2Proxy[];
    selectedSub2ProxyId: string;
    sub2ProxiesBusy: boolean;
    pushBusy: boolean;
    onSelectedSub2GroupIdChange: (value: string) => void;
    onSelectedSub2ProxyIdChange: (value: string) => void;
    onRefreshSub2Groups: () => Promise<void>;
    onRefreshSub2Proxies: () => Promise<void>;
    onPushSub2Data: (
        data: unknown,
        groupId: number,
        proxyId?: number | null,
        options?: { onPoll?: (job: Sub2PushJobStatus) => void | Promise<void> }
    ) => Promise<Sub2PushResult>;
    onOpenSettings: () => void;
};

const AUTO_PROXY_VALUE = "__auto";

const EMPTY_RESULT: ConversionResult = {
    sources: [],
    converted: [],
    skipped: []
};

function buildOutputText(format: FormatConverterOutputFormat, converted: ConvertedSession[]): string {
    if (!converted.length) return "";
    return JSON.stringify(buildOutputDocument(format, converted), null, 2);
}

function outputNoticeVisible(format: FormatConverterOutputFormat): boolean {
    return ["cpa", "cockpit", "codex", "axonhub", "codexmanager"].includes(format);
}

function formatProxyLabel(proxy: Sub2Proxy): string {
    const name = proxy.name?.trim() || proxy.host || "代理";
    return `${name} (#${proxy.id})`;
}

async function readSelectedJsonFiles(files: FileList | null) {
    if (!files) return [];
    const selected = Array.from(files).filter((file) => file.name.toLowerCase().endsWith(".json"));
    return Promise.all(selected.map(async (file) => ({
        name: file.webkitRelativePath || file.name,
        text: await file.text()
    })));
}

export function FormatConverterView({
                                        sub2Settings,
                                        sub2Groups,
                                        selectedSub2GroupId,
                                        sub2GroupsBusy,
                                        sub2Proxies,
                                        selectedSub2ProxyId,
                                        sub2ProxiesBusy,
                                        pushBusy,
                                        onSelectedSub2GroupIdChange,
                                        onSelectedSub2ProxyIdChange,
                                        onRefreshSub2Groups,
                                        onRefreshSub2Proxies,
                                        onPushSub2Data,
                                        onOpenSettings
                                    }: FormatConverterViewProps) {
    const [format, setFormat] = useState<FormatConverterOutputFormat>("sub2api");
    const [sourceText, setSourceText] = useState("");
    const [result, setResult] = useState<ConversionResult>(EMPTY_RESULT);
    const [inputStatus, setInputStatus] = useState<ConverterStatus>({
        tone: "idle",
        message: "等待输入。"
    });
    const [outputStatus, setOutputStatus] = useState<ConverterStatus>({
        tone: "idle",
        message: "暂无输出。"
    });
    const [pushJob, setPushJob] = useState<Sub2PushJobStatus | null>(null);
    const [fileBusy, setFileBusy] = useState(false);
    const fileInputRef = useRef<HTMLInputElement | null>(null);
    const outputText = useMemo(() => buildOutputText(format, result.converted), [format, result.converted]);
    const sub2Ready = Boolean(sub2Settings.apiUrl && sub2Settings.hasApiKey);
    const selectedGroupIdNumber = Number(selectedSub2GroupId || sub2Settings.defaultGroupId || 0);
    const selectedProxyIdNumber = Number(selectedSub2ProxyId || 0);
    const selectedProxy = sub2Proxies.find((proxy) => proxy.id === selectedProxyIdNumber) ?? null;

    function applyResult(nextResult: ConversionResult, emptyMessage = "等待输入。") {
        setResult(nextResult);
        if (!nextResult.sources.length && !nextResult.converted.length && !nextResult.skipped.length) {
            setInputStatus({tone: "idle", message: emptyMessage});
            setOutputStatus({tone: "idle", message: "暂无输出。"});
            return;
        }

        if (nextResult.converted.length) {
            setInputStatus({
                tone: "ok",
                message: `解析完成：${nextResult.converted.length} 个账号，跳过 ${nextResult.skipped.length} 项。`
            });
            setOutputStatus({tone: "ok", message: `已生成 ${nextResult.converted.length} 个账号。`});
            return;
        }

        setInputStatus({tone: "error", message: "没有可转换账号。"});
        setOutputStatus({tone: nextResult.skipped.length ? "error" : "idle", message: "暂无输出。"});
    }

    function handleSourceChange(value: string) {
        setSourceText(value);
        if (!value.trim()) {
            applyResult(EMPTY_RESULT);
            return;
        }

        try {
            applyResult(convertFromText(value));
        } catch (error) {
            const message = error instanceof Error ? error.message : "JSON 解析失败";
            const failed: ConversionResult = {
                sources: [],
                converted: [],
                skipped: [{sourceName: "pasted-json", path: "$", reason: message}]
            };
            setResult(failed);
            setInputStatus({tone: "error", message});
            setOutputStatus({tone: "error", message: "暂无输出。"});
        }
    }

    function clearAll() {
        setSourceText("");
        applyResult(EMPTY_RESULT);
    }

    function loadExample() {
        const text = JSON.stringify(EXAMPLE_SESSION, null, 2);
        setSourceText(text);
        handleSourceChange(text);
    }

    async function handleFileChange(files: FileList | null) {
        setFileBusy(true);
        try {
            const payload = await readSelectedJsonFiles(files);
            if (!payload.length) {
                setInputStatus({tone: "error", message: "没有选择 JSON 文件。"});
                return;
            }
            const nextResult = convertFiles(payload);
            setSourceText(nextResult.inputText);
            setResult({
                sources: nextResult.sources,
                converted: nextResult.converted,
                skipped: nextResult.skipped
            });
            setInputStatus({
                tone: nextResult.converted.length ? "ok" : "error",
                message: `读取 ${nextResult.filesRead} 个文件，生成 ${nextResult.converted.length} 个账号，跳过 ${nextResult.skipped.length} 项。`
            });
            setOutputStatus({
                tone: nextResult.converted.length ? "ok" : nextResult.skipped.length ? "error" : "idle",
                message: nextResult.converted.length ? `已生成 ${nextResult.converted.length} 个账号。` : "暂无输出。"
            });
        } catch (error) {
            setInputStatus({tone: "error", message: error instanceof Error ? error.message : "无法读取文件"});
        } finally {
            setFileBusy(false);
            if (fileInputRef.current) fileInputRef.current.value = "";
        }
    }

    async function copyOutput() {
        if (!outputText) return;
        try {
            await navigator.clipboard.writeText(outputText);
            setOutputStatus({tone: "ok", message: "已复制到剪贴板。"});
        } catch (error) {
            setOutputStatus({
                tone: "error",
                message: error instanceof Error ? error.message : "复制失败"
            });
        }
    }

    function downloadOutput() {
        if (!outputText) return;

        const first = result.converted[0];
        const base = sanitizeFormatConverterFileToken(first?.email || first?.name || format);
        const fileName = `${base}.${format}.${getFormatConverterTimestampToken()}.json`;
        const blob = new Blob([outputText], {type: "application/json;charset=utf-8"});
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = fileName;
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        setOutputStatus({tone: "ok", message: `已生成下载文件：${fileName}`});
    }

    async function pushOutput() {
        if (!result.converted.length) {
            setOutputStatus({tone: "error", message: "没有可推送账号。"});
            return;
        }
        if (!sub2Ready) {
            setOutputStatus({tone: "error", message: "请先在系统设置里配置 Sub2API 地址和 APIKey。"});
            return;
        }
        if (!Number.isInteger(selectedGroupIdNumber) || selectedGroupIdNumber <= 0) {
            setOutputStatus({tone: "error", message: "请先选择 Sub2 推送分组。"});
            return;
        }

        try {
            const data = buildOutputDocument("sub2api", result.converted);
            setPushJob(null);
            const pushResult = await onPushSub2Data(
                data,
                selectedGroupIdNumber,
                selectedProxyIdNumber > 0 ? selectedProxyIdNumber : null,
                {
                    onPoll: (job) => {
                        setPushJob(job);
                        if (job.status === "running") {
                            setOutputStatus({
                                tone: "idle",
                                message: `Sub2API 后台推送中：${job.accountCount || result.converted.length} 个账号。`
                            });
                        }
                    }
                }
            );
            const responseCount = Array.isArray(pushResult.response) ? pushResult.response.length : result.converted.length;
            setOutputStatus({tone: "ok", message: `已推送 ${responseCount} 个账号到 Sub2API。`});
        } catch (error) {
            setOutputStatus({tone: "error", message: error instanceof Error ? error.message : "推送失败"});
            setPushJob((current) => current ? {...current, status: "failed", progress: 100} : current);
        }
    }

    const pushProgress = pushJob ? Math.max(0, Math.min(100, Math.round(pushJob.progress || 0))) : 0;

    return (
        <section className="format-converter-page">
            <div className="converter-toolbar">
                <div className="converter-format-tabs" role="group" aria-label="输出格式">
                    {FORMAT_OPTIONS.map((option) => (
                        <button
                            key={option.value}
                            type="button"
                            className={format === option.value ? "active" : ""}
                            aria-pressed={format === option.value}
                            onClick={() => {
                                setFormat(option.value);
                                if (result.converted.length) {
                                    setOutputStatus({
                                        tone: "ok",
                                        message: `已切换为 ${option.label} 输出。`
                                    });
                                }
                            }}
                        >
                            {option.label}
                        </button>
                    ))}
                </div>
                <div className="converter-actions">
                    <Select
                        value={selectedSub2GroupId}
                        onValueChange={onSelectedSub2GroupIdChange}
                        onOpenChange={(open) => {
                            if (open && sub2Ready && !sub2GroupsBusy) {
                                onRefreshSub2Groups().catch((error) => {
                                    setOutputStatus({
                                        tone: "error",
                                        message: error instanceof Error ? error.message : "刷新分组失败"
                                    });
                                });
                            }
                        }}
                        disabled={sub2GroupsBusy || pushBusy || !sub2Ready}
                    >
                        <SelectTrigger className="toolbar-select converter-group-select">
                            <SelectValue placeholder={sub2GroupsBusy ? "加载分组" : "选择分组"}/>
                        </SelectTrigger>
                        <SelectContent>
                            {sub2Groups.map((group) => (
                                <SelectItem key={group.id} value={String(group.id)}>
                                    {group.name ? `${group.name} (#${group.id})` : `分组 #${group.id}`}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <Select
                        value={selectedSub2ProxyId || AUTO_PROXY_VALUE}
                        onValueChange={(value) => onSelectedSub2ProxyIdChange(value === AUTO_PROXY_VALUE ? "" : value)}
                        onOpenChange={(open) => {
                            if (open && sub2Ready && !sub2ProxiesBusy) {
                                onRefreshSub2Proxies().catch((error) => {
                                    setOutputStatus({
                                        tone: "error",
                                        message: error instanceof Error ? error.message : "刷新代理失败"
                                    });
                                });
                            }
                        }}
                        disabled={sub2ProxiesBusy || pushBusy || !sub2Ready}
                    >
                        <SelectTrigger className="toolbar-select converter-proxy-select">
                            <SelectValue placeholder={sub2ProxiesBusy ? "加载代理" : "选择代理"}/>
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value={AUTO_PROXY_VALUE}>自动选择代理</SelectItem>
                            {sub2Proxies.map((proxy) => (
                                <SelectItem key={proxy.id} value={String(proxy.id)}>
                                    {formatProxyLabel(proxy)}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <button type="button" onClick={copyOutput} disabled={!outputText}>
                        <Copy size={16} aria-hidden="true"/>
                        复制
                    </button>
                    <button type="button" onClick={downloadOutput} disabled={!outputText}>
                        <Download size={16} aria-hidden="true"/>
                        下载
                    </button>
                    <button
                        type="button"
                        className="primary"
                        onClick={pushOutput}
                        disabled={pushBusy || !outputText || !sub2Ready || !selectedGroupIdNumber}
                    >
                        <UploadCloud size={16} aria-hidden="true"/>
                        {pushBusy ? "推送中" : "推送"}
                    </button>
                </div>
            </div>

            <div className="converter-workspace">
                <div className="converter-panel">
                    <div className="converter-panel-head">
                        <div>
                            <strong>Session JSON</strong>
                            <p>粘贴 ChatGPT Web session，或选择一个或多个 JSON 文件。</p>
                        </div>
                    </div>
                    <div className="session-guide-box">
                        <strong>Session 数据来源</strong>
                        <p>
                            先在浏览器登录 ChatGPT，然后打开
                            <a href="https://chatgpt.com/api/auth/session" target="_blank" rel="noreferrer">
                                https://chatgpt.com/api/auth/session
                            </a>
                            ，复制页面显示的整段 JSON 后粘贴到下方。
                        </p>
                        <p className="danger-copy">这段 JSON 包含 accessToken 和 sessionToken，等同敏感登录凭证。</p>
                    </div>
                    <input
                        ref={fileInputRef}
                        className="hidden-file-input"
                        type="file"
                        accept=".json,application/json"
                        multiple
                        onChange={(event) => handleFileChange(event.target.files)}
                    />
                    <div className="converter-input-actions">
                        <button type="button" onClick={() => fileInputRef.current?.click()} disabled={fileBusy}>
                            <FileJson size={16} aria-hidden="true"/>
                            {fileBusy ? "读取中" : "选择文件"}
                        </button>
                        <button type="button" onClick={loadExample}>
                            <Braces size={16} aria-hidden="true"/>
                            填入示例结构
                        </button>
                        <button type="button" className="ghost" onClick={clearAll}
                                disabled={!sourceText && !outputText}>
                            <RotateCcw size={16} aria-hidden="true"/>
                            清空
                        </button>
                    </div>
                    <textarea
                        className="converter-textarea"
                        value={sourceText}
                        onChange={(event) => handleSourceChange(event.target.value)}
                        placeholder='{"user":{"email":"mark@example.com"},"expires":"2026-08-06T14:29:36.155Z","account":{"id":"...","planType":"plus"},"accessToken":"...","sessionToken":"..."}'
                        spellCheck={false}
                    />
                    <div className={`converter-status ${inputStatus.tone}`}>{inputStatus.message}</div>
                </div>

                <div className="converter-panel">
                    <div className="converter-panel-head">
                        <div>
                            <strong>转换结果</strong>
                            <p>当前输出为 {FORMAT_LABELS[format]} 导入 JSON。</p>
                        </div>
                    </div>
                    {outputNoticeVisible(format) && (
                        <div className="converter-notice">
                            ChatGPT Web session 一般没有 refresh_token；缺少真实 id_token 时会构造 Codex 可解析的占位
                            JWT claims。Codex 和 Codex-Manager 会保留空 refresh_token；AxonHub 缺少真实 refresh_token
                            时会写入占位值。
                        </div>
                    )}
                    <div className="converter-summary">
                        <div>
                            <strong>{result.converted.length}</strong>
                            <span>账号数</span>
                        </div>
                        <div>
                            <strong>{FORMAT_LABELS[format]}</strong>
                            <span>输出格式</span>
                        </div>
                        <div>
                            <strong>{result.skipped.length}</strong>
                            <span>跳过项</span>
                        </div>
                    </div>

                    <div className={`converter-sub2-state ${sub2Ready ? "ok" : "warn"}`}>
                        <span>
                            {sub2Ready
                                ? `推送地址：${sub2Settings.apiUrl}`
                                : "Sub2API 尚未配置完整，无法推送。"}
                        </span>
                        <span>
                            目标分组：{selectedGroupIdNumber > 0 ? `#${selectedGroupIdNumber}` : "未选择"}
                        </span>
                        <span>
                            账号代理：{selectedProxy ? formatProxyLabel(selectedProxy) : "自动选择"}
                        </span>
                        {!sub2Ready && (
                            <button type="button" onClick={onOpenSettings}>
                                去配置
                            </button>
                        )}
                    </div>

                    {pushJob && (
                        <div className={`converter-push-progress ${pushJob.status}`}>
                            <div className="converter-push-progress-head">
                                <strong>
                                    {pushJob.status === "succeeded"
                                        ? "推送完成"
                                        : pushJob.status === "failed"
                                            ? "推送失败"
                                            : "正在推送"}
                                </strong>
                                <span>{pushProgress}%</span>
                            </div>
                            <div className="converter-push-progress-track" aria-hidden="true">
                                <span style={{width: `${pushProgress}%`}}/>
                            </div>
                            <p>
                                任务 {pushJob.jobId.slice(0, 8)} · {pushJob.accountCount || result.converted.length} 个账号
                            </p>
                        </div>
                    )}

                    <div className="converter-account-table">
                        <div className="converter-account-row head">
                            <span>名称</span>
                            <span>邮箱</span>
                            <span>过期时间</span>
                            <span>来源</span>
                        </div>
                        {result.converted.length ? (
                            result.converted.map((item, index) => (
                                <div className="converter-account-row"
                                     key={`${item.sourceName}-${item.sourcePath ?? index}`}>
                                    <span title={item.name}>{item.name || "-"}</span>
                                    <span title={item.email}>{item.email || "-"}</span>
                                    <span
                                        title={item.expiresAt}>{formatConverterDisplayDate(item.expiresAt) || "-"}</span>
                                    <span title={item.sourceName}>{item.sourceName || "pasted-json"}</span>
                                </div>
                            ))
                        ) : (
                            <div className="converter-empty-row">暂无可转换账号。</div>
                        )}
                    </div>

                    {result.skipped.length > 0 && (
                        <div className="converter-issues">
                            {result.skipped.map((item, index) => (
                                <div key={`${item.sourceName}-${item.path ?? index}`}>
                                    {item.sourceName || "input"} {item.path || ""}: {item.reason}
                                </div>
                            ))}
                        </div>
                    )}

                    <textarea
                        className="converter-textarea preview"
                        value={outputText}
                        readOnly
                        placeholder="转换后会显示 JSON。"
                        spellCheck={false}
                    />
                    <div className={`converter-status ${outputStatus.tone}`}>{outputStatus.message}</div>
                </div>
            </div>
        </section>
    );
}
