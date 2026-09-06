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
      <div className="flex items-center gap-2 rounded-sm border border-line px-3 py-1.5 font-mono text-[12px] text-muted">
        <span className="h-2 w-2 rounded-full bg-muted" aria-hidden="true" />
        CONNECTING
      </div>
    );
  }
  const rung = LADDER[degradation.level] ?? LADDER[3];
  const lost = degradation.lost.join(", ");
  return (
    <div
      className={`flex min-w-0 items-center gap-2.5 rounded-sm border bg-raised px-3 py-1.5 font-mono text-[12px] ${rung.tone}`}
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

const STEPS = [
  { id: "intake", n: 1, label: "Intake", hint: "a call arrives — upload a recording or dial one back" },
  { id: "triage", n: 2, label: "Triage & rank", hint: "the ranked queue, by severity and by place" },
  { id: "dispatch", n: 3, label: "Dispatch", hint: "the surface, the route, and the call's full detail" },
  { id: "audit", n: 4, label: "Audit", hint: "every override and system event, in order" },
];

export default function Header({ step, onStep, degradation, connected }) {
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

  const onStepKey = (e) => {
    const i = STEPS.findIndex((s) => s.id === step);
    if (e.key === "ArrowRight") onStep(STEPS[(i + 1) % STEPS.length].id);
    if (e.key === "ArrowLeft") onStep(STEPS[(i - 1 + STEPS.length) % STEPS.length].id);
  };

  return (
    <header className="flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-line bg-panel px-4 py-3 sm:px-6">
      <div className="flex items-baseline gap-3">
        <span className="text-[18px] font-semibold tracking-tight text-ink">
          VAANI<span className="text-water">.</span>
        </span>
        <span className="hidden text-[12px] text-muted sm:inline">
          Ernakulam · emergency response centre
        </span>
      </div>

      <nav
        className="flex gap-1 rounded-sm bg-raised p-0.5"
        role="tablist"
        aria-label="How a call moves through this system"
        onKeyDown={onStepKey}
      >
        {STEPS.map((s) => (
          <button
            key={s.id}
            role="tab"
            id={`tab-${s.id}`}
            aria-selected={step === s.id}
            aria-controls={`panel-${s.id}`}
            tabIndex={step === s.id ? 0 : -1}
            title={s.hint}
            onClick={() => onStep(s.id)}
            className={`flex items-center gap-1.5 rounded-[3px] px-3 py-1.5 text-[13px] font-medium transition-colors ${
              step === s.id
                ? "bg-panel text-ink shadow-[inset_0_0_0_1px_var(--color-line)]"
                : "text-muted hover:text-ink-2"
            }`}
          >
            <span
              className={`font-mono text-[11px] ${step === s.id ? "text-water" : "text-muted"}`}
            >
              {s.n}
            </span>
            {s.label}
          </button>
        ))}
      </nav>

      <div className="order-last w-full min-w-0 lg:order-none lg:w-auto lg:flex-1">
        <Badge degradation={degradation} connected={connected} />
      </div>

      <div className="flex items-center gap-2 rounded-sm border border-line-soft px-2 py-1.5">
        <span className="font-mono text-[9.5px] uppercase tracking-[0.1em] text-muted">
          Demo
        </span>
        <button
          onClick={() => act("surge", api.surge)}
          disabled={busy === "surge"}
          className="rounded-sm px-2.5 py-1 text-[12px] text-ink-2 transition-colors hover:bg-raised disabled:opacity-50"
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
          className={`rounded-sm px-2.5 py-1 text-[12px] transition-colors ${
            offline ? "bg-band3/15 text-band3" : "text-ink-2 hover:bg-raised"
          }`}
        >
          {offline ? "Restore network" : "Cut network"}
        </button>
        <button
          onClick={() => act("reset", api.reset)}
          className="rounded-sm px-2.5 py-1 text-[12px] text-muted transition-colors hover:bg-raised hover:text-ink-2"
        >
          Reset
        </button>
      </div>
    </header>
  );
}
