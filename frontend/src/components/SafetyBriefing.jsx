import { useEffect, useState } from "react";
import { api } from "../api.js";

export function SafetyBriefing({ callId }) {
  const [data, setData] = useState(null);
  const [state, setState] = useState("idle");

  useEffect(() => {
    if (!callId) {
      setData(null);
      setState("idle");
      return;
    }
    let alive = true;
    setState("loading");
    api
      .briefing(callId)
      .then((res) => {
        if (alive) {
          setData(res);
          setState("done");
        }
      })
      .catch(() => {
        if (alive) setState("error");
      });
    return () => {
      alive = false;
    };
  }, [callId]);

  if (state === "loading") {
    return (
      <div className="rounded-sm border border-line bg-raised/50 p-2 font-mono text-[10px] text-muted">
        Computing responder safety briefing…
      </div>
    );
  }

  if (state === "error" || !data) {
    return null;
  }

  const { warnings = [], route_hazards = [], generated_from = [] } = data;

  if (warnings.length === 0) {
    return (
      <div className="mt-1 flex items-center gap-2 rounded-sm border border-ok/30 bg-ok/5 px-2.5 py-1.5 font-mono text-[10.5px]">
        <span className="font-bold text-ok">✓ NO ACUTE HAZARDS</span>
        <span className="text-muted">Standard dispatch precautions apply</span>
      </div>
    );
  }

  return (
    <div className="mt-1.5 rounded-sm border border-band3/30 bg-band3/5 p-2.5">
      <div className="flex flex-wrap items-center justify-between gap-1 border-b border-band3/20 pb-1.5">
        <span className="font-mono text-[10.5px] font-bold uppercase tracking-[0.08em] text-band3">
          ⚠ Responder Safety Briefing
        </span>
        {generated_from.length > 0 && (
          <span className="font-mono text-[9px] text-muted">
            Audit rule triggers: {generated_from.join(" · ")}
          </span>
        )}
      </div>

      <ul className="mt-2 space-y-1.5 font-mono text-[11px] leading-relaxed text-ink">
        {warnings.map((warning, idx) => (
          <li
            key={idx}
            className="flex items-start gap-2 rounded-[2px] bg-ground/70 px-2 py-1 border border-line/60"
          >
            <span className="font-bold text-band3">▸</span>
            <span>{warning}</span>
          </li>
        ))}
      </ul>

      {route_hazards.length > 0 && (
        <div className="mt-2 pt-1.5 border-t border-line/50 font-mono text-[10px] text-muted">
          <span className="font-semibold text-ink-2">Submerged route segments: </span>
          {route_hazards.map((h, idx) => (
            <span key={idx} className="mr-2 inline-block">
              {h.name} {h.hand_m != null ? `(${h.hand_m} m HAND)` : "(submerged)"}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export default SafetyBriefing;
