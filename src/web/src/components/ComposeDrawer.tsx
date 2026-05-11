import {useEffect, useState} from "react";
import {sendMail} from "../api";
import {usePrefs} from "../i18n";

type Props = {
    open: boolean;
    fromMailbox: string;
    onClose: () => void;
    onSent: (ok: string) => void;
    onError: (msg: string) => void;
};

function splitRecipients(value: string): string[] {
    return value.split(/[,\n;]/).map((item) => item.trim()).filter(Boolean);
}

export function ComposeDrawer({open, fromMailbox, onClose, onSent, onError}: Props) {
    const {t} = usePrefs();
    const [to, setTo] = useState("");
    const [cc, setCc] = useState("");
    const [bcc, setBcc] = useState("");
    const [subject, setSubject] = useState("");
    const [body, setBody] = useState("");
    const [html, setHtml] = useState(false);
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (open) return;
        setTo("");
        setCc("");
        setBcc("");
        setSubject("");
        setBody("");
        setHtml(false);
        setBusy(false);
    }, [open]);

    useEffect(() => {
        if (!open) return;

        function onKey(event: KeyboardEvent) {
            if (event.key === "Escape") onClose();
        }

        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [open, onClose]);

    if (!open) return null;

    async function handleSend() {
        const toList = splitRecipients(to);
        if (!fromMailbox || toList.length === 0) return;
        setBusy(true);
        try {
            await sendMail({
                from: fromMailbox,
                to: toList,
                cc: cc ? splitRecipients(cc) : undefined,
                bcc: bcc ? splitRecipients(bcc) : undefined,
                subject: subject || undefined,
                body: body || undefined,
                html
            });
            onSent(t("flash.compose.sent"));
            onClose();
        } catch (err) {
            onError(err instanceof Error ? err.message : String(err));
        } finally {
            setBusy(false);
        }
    }

    return (
        <>
            <div className="drawer-veil" onClick={onClose}/>
            <aside className="drawer" role="dialog" aria-label={t("compose.title")}>
                <header className="head">
                    <div>
                        <h2>{t("compose.title")}</h2>
                    </div>
                    <button className="ghost icon-btn" onClick={onClose} aria-label="close">×</button>
                </header>

                <div className="body">
                    <div className="field">
                        <label>{t("compose.field.from")}</label>
                        <div className="from-val">{fromMailbox || "—"}</div>
                    </div>
                    <div className="field">
                        <label>{t("compose.field.to")}</label>
                        <textarea
                            rows={1}
                            value={to}
                            onChange={(event) => setTo(event.target.value)}
                            placeholder="alice@example.com, bob@example.com"
                        />
                    </div>
                    <div className="field">
                        <label>{t("compose.field.cc")}</label>
                        <input
                            value={cc}
                            onChange={(event) => setCc(event.target.value)}
                            placeholder={t("compose.placeholder.cc")}
                        />
                    </div>
                    <div className="field">
                        <label>{t("compose.field.bcc")}</label>
                        <input
                            value={bcc}
                            onChange={(event) => setBcc(event.target.value)}
                            placeholder={t("compose.placeholder.cc")}
                        />
                    </div>
                    <div className="field">
                        <label>{t("compose.field.subject")}</label>
                        <input
                            value={subject}
                            onChange={(event) => setSubject(event.target.value)}
                            placeholder={t("compose.placeholder.subject")}
                        />
                    </div>
                    <div className="field">
                        <label>{t("compose.field.body")}</label>
                        <textarea
                            value={body}
                            onChange={(event) => setBody(event.target.value)}
                            placeholder={t("time.dash")}
                        />
                    </div>
                </div>

                <footer className="actions">
                    <div className="left">
            <span className="opts">
              <label>
                <input type="checkbox" checked={html} onChange={(event) => setHtml(event.target.checked)}/>
                  {t("compose.opt.html")}
              </label>
            </span>
                    </div>
                    <div className="right">
                        <button className="ghost" onClick={onClose}
                                disabled={busy}>{t("compose.action.cancel")}</button>
                        <button
                            className="primary"
                            onClick={handleSend}
                            disabled={busy || !fromMailbox || splitRecipients(to).length === 0}
                        >
                            {busy ? t("compose.action.sending") : t("compose.action.transmit")}
                        </button>
                    </div>
                </footer>
            </aside>
        </>
    );
}
