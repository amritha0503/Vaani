import { api, usePoll } from "../api.js";
import { Panel } from "./Panel.jsx";

const TONE = {
  overridden: "text-band2",
  relocated: "text-water",
  dispatched: "text-ok",
  degraded: "text-band3",
  call_ended: "text-band2",
  sms_failed: "text-band2",
  asr_failed: "text-band2",
  crew_brief_sent: "text-ok",
  crew_brief_failed: "text-band2",
};

/** The few fields worth a glance, per action -- everything else in the payload
 *  is still on disk and in the raw JSON, just not worth a column here. */
function detail(action, p) {
  switch (action) {
    case "overridden":
      return `${p.direction > 0 ? "escalated" : "demoted"} by ${p.direction > 0 ? "+" : ""}${p.direction} — ${p.reason ?? ""}`;
    case "relocated":
      return `${p.source} · ${p.lat?.toFixed(4)}, ${p.lon?.toFixed(4)} · ±${Math.round(p.error_radius_m ?? 0)} m`;
    case "ranked":
    case "re-extracted":
      return `${p.extractor ?? ""} · band ${p.severity_band ?? "?"}${p.trapped ? " · trapped" : ""}${p.located ? ` · ${p.located}` : ""}`;
    case "dispatched":
      return p.reason === "boat_or_air_required"
        ? `boat/air required from ${p.depot ?? "?"}`
        : `${p.depot ?? "?"} · ${p.length_m ?? "?"} m · +${p.detour_m ?? 0} m detour`;
    case "call_turn":
      return `${p.stage}${p.landmark ? ` · ${p.landmark}` : ""}`;
    case "call_ended":
      return p.why ?? "";
    case "call_status":
      return `twilio: ${p.status}`;
    case "sms_sent":
      return `to ${p.to} · ${p.status ?? p.sid ?? ""}`;
    case "sms_failed":
      return `to ${p.to} — ${p.error ?? ""}`;
    case "dialled":
      return `to ${p.to} · ${p.status ?? ""}`;
    case "crew_brief_sent":
      return `to ${p.to} · via MSG91`;
    case "crew_brief_failed":
      return `to ${p.to} — ${p.detail ?? ""}`;
    case "intake":
      return `${p.count} file${p.count === 1 ? "" : "s"}`;
    case "degraded":
      return p.offline ? "network cut for rehearsal" : "network restored";
    case "startup":
      return p.surface ? `surface: ${p.surface}` : p.roads != null ? `${p.roads} road edges, ${p.depots} depots` : "";
    default: {
      const s = JSON.stringify(p);
      return s.length > 70 ? s.slice(0, 70) + "…" : s;
    }
  }
}

const timeAgo = (ts) => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
};

export default function AuditPanel({ onSelect }) {
  const { data, error } = usePoll(() => api.audit(300), 4000);
  const rows = data ?? [];

  return (
    <Panel
      title="Audit log"
      note={rows.length ? `${rows.length} events` : "waiting for the first event"}
    >
      {error && (
        <p role="alert" className="text-[12.5px] text-band3">
          {error}
        </p>
      )}
      {!rows.length && !error && (
        <p className="text-[12.5px] leading-relaxed text-muted">
          Every ranking decision, override, dispatch and system-state change is
          written here as it happens — this is the accountability trail behind
          the board, not a summary of it.
        </p>
      )}
      <ul className="space-y-px">
        {rows.map((r) => {
          let payload = {};
          try {
            payload = JSON.parse(r.payload);
          } catch {
            /* leave empty */
          }
          const clickable = r.call_id && onSelect;
          const Tag = clickable ? "button" : "div";
          return (
            <li key={r.id}>
              <Tag
                onClick={clickable ? () => onSelect(r.call_id) : undefined}
                className={`grid w-full grid-cols-[68px_120px_60px_minmax(0,1fr)] items-baseline gap-2 rounded-[3px] px-2 py-1.5 text-left font-mono text-[12px] ${
                  clickable ? "transition-colors hover:bg-raised" : ""
                }`}
              >
                <span className="text-muted">{timeAgo(r.ts)}</span>
                <span className={TONE[r.action] ?? "text-ink-2"}>{r.action}</span>
                <span className="truncate text-muted">{r.call_id ?? "—"}</span>
                <span className="truncate text-ink-2">{detail(r.action, payload)}</span>
              </Tag>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
