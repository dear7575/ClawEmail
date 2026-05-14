import type { ClawAuthStatus, Mailbox } from "../api";
import { usePrefs } from "../i18n";
import { parseServerTime } from "../time";

type Props = {
  mailboxes: Mailbox[];
  clawAuth: ClawAuthStatus | null;
  suffix: string;
  setSuffix: (value: string) => void;
  onCreate: () => void;
  onDelete: (mailbox: Mailbox) => void;
  onOpen: (mailbox: Mailbox) => void;
  onConfigureRules: (mailbox: Mailbox) => void;
};

function relTime(value: string, t: (key: string, vars?: Record<string, string | number>) => string): string {
  if (!value) return "—";
  const date = parseServerTime(value);
  if (Number.isNaN(date.getTime())) return value;
  const diff = Date.now() - date.getTime();
  const min = Math.round(diff / 60000);
  if (min < 1) return t("time.justNow");
  if (min < 60) return t("time.mAgo", { n: min });
  const h = Math.round(min / 60);
  if (h < 24) return t("time.hAgo", { n: h });
  const d = Math.round(h / 24);
  return t("time.dAgo", { n: d });
}

function ruleLabel(mailbox: Mailbox, t: (key: string) => string): string {
  if (mailbox.comm_level === 0) return t("mb.rules.personal");
  if (mailbox.comm_level === 1) return t("mb.rules.internal");
  if (mailbox.comm_level === 2 && mailbox.ext_receive_type === 1) {
    return t("mb.rules.receiveAll");
  }
  if (mailbox.comm_level === 2) return t("mb.rules.external");
  return t("mb.rules.unknown");
}

function statusLabel(status: string, t: (key: string) => string): string {
  if (status === "active") return t("mb.status.active");
  if (status === "deleted") return t("mb.status.deleted");
  if (status === "disabled") return t("mb.status.disabled");
  return status || t("mb.status.unknown");
}

export function MailboxesView({
  mailboxes,
  clawAuth,
  suffix,
  setSuffix,
  onCreate,
  onDelete,
  onOpen,
  onConfigureRules
}: Props) {
  const { t } = usePrefs();
  const rootPrefix = clawAuth?.hasDashboardCookie ? clawAuth.rootPrefix : null;
  const domain = clawAuth?.hasDashboardCookie ? clawAuth.domain : null;
  const canCreate = Boolean(rootPrefix && domain);

  const isPrimary = (m: Mailbox): boolean => {
    if (!clawAuth) return false;
    const rootEmail = clawAuth.rootPrefix && clawAuth.domain
      ? `${clawAuth.rootPrefix}@${clawAuth.domain}`
      : null;
    return (
      m.id === clawAuth.parentMailboxId ||
      m.email === rootEmail
    );
  };

  return (
    <div className="stagger">
      <div className="create-bar">
        <span className="label">{t("mb.forge")}</span>
        <div className="composer">
          {canCreate ? (
            <>
              <span>{rootPrefix}.</span>
              <input
                value={suffix}
                onChange={(event) => setSuffix(event.target.value.toLowerCase().replace(/[^a-z0-9]/g, ""))}
                placeholder={t("mb.placeholder.suffix")}
              />
              <span>@{domain}</span>
            </>
          ) : (
            <span>{t("mb.root.pending")}</span>
          )}
        </div>
        <span className="hint">{t("mb.hint")}</span>
        <button
          className="primary"
          onClick={onCreate}
          disabled={!suffix || !canCreate}
        >
          {t("mb.create")}
        </button>
      </div>

      {mailboxes.length === 0 ? (
        <div className="empty-state">
          <span className="big">{t("mb.empty.head")}</span>
          {t("mb.empty.body")}
        </div>
      ) : (
        <div className="mb-table">
          <div className="mb-row head">
            <span>{t("mb.head.mailbox")}</span>
            <span>{t("mb.head.status")}</span>
            <span>{t("mb.head.rules")}</span>
            <span>{t("mb.head.created")}</span>
            <span style={{ textAlign: "right" }}>{t("mb.head.ops")}</span>
          </div>
          {mailboxes.map((mailbox) => (
            <div className="mb-row" key={mailbox.id}>
              <div className="email-cell">
                <span className="e">{mailbox.email}</span>
                <span className="pref">
                  {isPrimary(mailbox)
                    ? t("mb.row.primary")
                    : t("mb.row.prefix", { p: mailbox.prefix })}
                </span>
              </div>
              <div>
                <span className={`tag ${mailbox.status === "active" ? "ok" : "muted"}`}>
                  <span className={`dot ${mailbox.status === "active" ? "live" : ""}`} />
                  {statusLabel(mailbox.status, t)}
                </span>
              </div>
              <div>
                <span className={`tag ${mailbox.comm_level === 2 && mailbox.ext_receive_type === 1 ? "ok" : "muted"}`}>
                  <span className={`dot ${mailbox.comm_level === 2 && mailbox.ext_receive_type === 1 ? "live" : ""}`} />
                  {ruleLabel(mailbox, t)}
                </span>
              </div>
              <div className="time-cell">{relTime(mailbox.created_at, t)}</div>
              <div className="ops">
                <button onClick={() => onOpen(mailbox)}>{t("mb.row.open")}</button>
                <button
                  onClick={() => onConfigureRules(mailbox)}
                  disabled={!clawAuth?.hasDashboardCookie}
                >
                  {t("mb.row.rules")}
                </button>
                <button
                  className="danger"
                  onClick={() => onDelete(mailbox)}
                  disabled={isPrimary(mailbox)}
                >
                  {t("mb.row.delete")}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
