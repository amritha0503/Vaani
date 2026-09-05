import { useEffect, useState } from "react";
import { api } from "../api.js";

const LADDER = [
  { name: "FULL", tone: "text-ok border-ok/40" },
  { name: "NO MODEL", tone: "text-band2 border-band2/45" },
  { name: "NO SPEECH", tone: "text-band2 border-band2/45" },
  { name: "COLD", tone: "text-band3 border-band3/45" },
];

function Badge({ degradation, connected }) {
  if (!degradation) {
    return (
      <div className="flex items-center gap-2 rounded-sm border border-line px-3 py-1.5 font-mono text-[11px] text-muted">
        <span className="h-2 w-2 rounded-full bg-muted" aria-hidden="true" />
        CONNECTING
      </div>
    );
  }
  const rung = LADDER[degradation.level] ?? LADDER[3];
  const lost = degradation.lost.join(", ");
  return (
    <div
      className={`flex min-w-0 items-center gap-2.5 rounded-sm border bg-raised px-3 py-1.5 font-mono text-[11px] ${rung.tone}`}
      role="status"
      aria-label={
        `System level ${degradation.level}, ${rung.name}. ` +
        (lost ? `Lost: ${lost}. ` : "All capabilities online. ") +
        "Ranking and routing continue."
      }
    >
      <span
        className={`h-2 w-2 flex-none rounded-full ${
          degradation.level === 0 ? "bg-ok" : "bg-band2 pulse"
        }`}
        aria-hidden="true"
      />
      <span className="font-semibold tracking-[0.08em]">
        LEVEL {degradation.level} · {rung.name}
      </span>
      <span className="truncate text-muted" aria-hidden="true">
        {lost ? `lost: ${lost} — ranking and routing continue` : "all capabilities online"}
      </span>
      {!connected && (
        <span className="text-band3" aria-hidden="true">
          · stream dropped
        </span>
      )}
    </div>
  );
}

const TABS = [
  { id: "ops", label: "Operations", hint: "the ranked queue, the surface and the route" },
  { id: "intake", label: "Intake & triage", hint: "upload recordings, dial back, group the board" },
  { id: "audit", label: "Audit log", hint: "every override and system event, in order" },
];

export default function Header({ tab, onTab, degradation, connected }) {
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState(null);

  useEffect(() => {
    if (degradation && degradation.level < 2) setOffline(false);
  }, [degradation]);

  const act = async (name, fn) => {
    setBusy(name);
    try {
      await fn();
    } finally {
      setBusy(null);
    }
  };

  const onTabKey = (e) => {
    const i = TABS.findIndex((t) => t.id === tab);
    if (e.key === "ArrowRight") onTab(TABS[(i + 1) % TABS.length].id);
    if (e.key === "ArrowLeft") onTab(TABS[(i - 1 + TABS.length) % TABS.length].id);
  };

  return (
    <header className="flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-line bg-panel px-4 py-2.5 sm:px-6">
      <div className="flex items-baseline gap-3">
        <span className="text-[17px] font-semibold tracking-tight">
          VAANI<span className="text-water">.</span>
        </span>
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted">
          Ernakulam · emergency response centre
        </span>
      </div>

      <nav
        className="flex gap-1 rounded-sm bg-raised p-0.5"
        role="tablist"
        aria-label="Console sections"
        onKeyDown={onTabKey}
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            tabIndex={tab === t.id ? 0 : -1}
            title={t.hint}
            onClick={() => onTab(t.id)}
            className={`rounded-[3px] px-3 py-1.5 text-[12.5px] font-medium transition-colors ${
              tab === t.id
                ? "bg-panel text-ink shadow-[inset_0_0_0_1px_var(--color-line)]"
                : "text-muted hover:text-ink-2"
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div className="order-last w-full min-w-0 lg:order-none lg:w-auto lg:flex-1">
        <Badge degradation={degradation} connected={connected} />
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={() => act("surge", api.surge)}
          disabled={busy === "surge"}
          className="rounded-sm border border-line bg-raised px-3 py-1.5 text-[12.5px] font-medium text-ink transition-colors hover:border-water disabled:opacity-50"
        >
          {busy === "surge" ? "Injecting…" : "Surge"}
        </button>
        <button
          onClick={() =>
            act("cut", async () => {
              await api.cut(!offline);
              setOffline(!offline);
            })
          }
          aria-pressed={offline}
          className={`rounded-sm border px-3 py-1.5 text-[12.5px] font-medium transition-colors ${
            offline
              ? "border-band3/60 bg-band3/15 text-band3"
              : "border-line bg-raised text-ink hover:border-band3"
          }`}
        >
          {offline ? "Restore network" : "Cut network"}
        </button>
        <button
          onClick={() => act("reset", api.reset)}
          className="rounded-sm border border-line bg-raised px-3 py-1.5 text-[12.5px] font-medium text-muted transition-colors hover:border-line hover:text-ink"
        >
          Reset
        </button>
      </div>
    </header>
  );
}
