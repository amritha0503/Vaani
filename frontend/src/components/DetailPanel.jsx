import { useEffect, useState } from "react";
import { api, bandName } from "../api.js";

function Line({ label, children }) {
  return (
    <div className="grid grid-cols-[110px_minmax(0,1fr)] gap-2 py-[4px]">
      <dt className="text-[11.5px] uppercase tracking-[0.06em] text-muted">
        {label}
      </dt>
      <dd className="min-w-0 font-mono text-[12px] leading-relaxed text-ink-2">
        {children}
      </dd>
    </div>
  );
}

/** The ground read as a line, not a point -- HAND out from the caller in the
 *  direction that actually runs downhill, off the same raster the rank comes
 *  from. Plain SVG: this is one polyline, a charting library would be
 *  overhead for a chart with one series. */
function CrossSection({ callId, lat, lon }) {
  const [sec, setSec] = useState(null);
  const [state, setState] = useState("idle");

  useEffect(() => {
    if (lat == null || lon == null) {
      setState("idle");
      setSec(null);
      return;
    }
    let alive = true;
    setState("loading");
    api
      .section(lat, lon)
      .then((r) => alive && (setSec(r), setState("done")))
      .catch(() => alive && setState("error"));
    return () => {
      alive = false;
    };
  }, [callId, lat, lon]);

  if (lat == null) return null;
  if (state === "loading") return <p className="text-[12px] text-muted">reading terrain…</p>;
  if (state === "error" || !sec) {
    return <p className="text-[12px] text-muted">no cross-section outside the mapped area</p>;
  }

  const pts = sec.points.filter((p) => p.hand_m != null);
  if (pts.length < 2) return null;
  const W = 100, H = 34, PAD = 2;
  const maxH = Math.max(...pts.map((p) => p.hand_m), 0.5);
  const x = (d) => (d / sec.points[sec.points.length - 1].distance_m) * (W - PAD * 2) + PAD;
  const y = (h) => H - PAD - (h / maxH) * (H - PAD * 2);
  const line = pts.map((p) => `${x(p.distance_m).toFixed(1)},${y(p.hand_m).toFixed(1)}`).join(" ");
  const area = `${PAD},${H - PAD} ${line} ${x(pts[pts.length - 1].distance_m).toFixed(1)},${H - PAD}`;

  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="h-9 w-full"
        role="img"
        aria-label={`Ground height above drainage, from ${pts[0].hand_m} metres at the caller out to ${pts[pts.length - 1].hand_m} metres, ${sec.points[sec.points.length - 1].distance_m} metres away toward the drainage line.`}
      >
        <polygon points={area} fill="var(--color-water)" opacity="0.14" />
        <polyline
          points={line}
          fill="none"
          stroke="var(--color-water)"
          strokeWidth="1.4"
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
        />
        <circle cx={x(0)} cy={y(pts[0].hand_m)} r="1.6" fill="var(--color-water)" />
      </svg>
      <div className="mt-0.5 flex justify-between font-mono text-[9.5px] text-muted">
        <span>caller · {pts[0].hand_m.toFixed(1)} m</span>
        <span>toward drainage, {Math.round(sec.direction_deg)}°</span>
        <span>{pts[pts.length - 1].distance_m.toFixed(0)} m out · {pts[pts.length - 1].hand_m.toFixed(1)} m</span>
      </div>
    </div>
  );
}

/** The two honest ways to place a call the audio didn't: the operator's own
 *  GPS (standing in for AML, which this box cannot be provisioned for), or a
 *  click on the exact spot on the map. Never a guess -- a guessed coordinate
 *  sends a boat to where nobody is, so this is the entire alternative. */
function LocationFix({ callId, onPickOnMap }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const useGps = () => {
    if (!("geolocation" in navigator)) {
      setError("This browser has no location support.");
      return;
    }
    setBusy(true);
    setError(null);
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        try {
          await api.setLocation(callId, {
            lat: pos.coords.latitude,
            lon: pos.coords.longitude,
            error_radius_m: pos.coords.accuracy,
            source: "gps",
          });
        } catch (e) {
          setError(e.message);
        } finally {
          setBusy(false);
        }
      },
      (err) => {
        setBusy(false);
        // A browser that already refused this origin never prompts again -- the
        // button just fails instantly and reads as broken. Name that case.
        setError(
          err?.code === 1
            ? "Blocked: allow location for this page in the address-bar site settings, or pick on the map instead."
            : "Location was not shared — pick on the map instead."
        );
      },
      { enableHighAccuracy: true, timeout: 12000 }
    );
  };

  return (
    <div className="mt-1 flex flex-wrap items-center gap-2">
      <button
        onClick={useGps}
        disabled={busy}
        className="rounded-sm border border-line px-1.5 py-0.5 text-[11.5px] text-muted transition-colors hover:border-water hover:text-ink disabled:opacity-50"
      >
        {busy ? "locating…" : "use my GPS"}
      </button>
      <button
        onClick={() => onPickOnMap?.(callId)}
        className="rounded-sm border border-line px-1.5 py-0.5 text-[11.5px] text-muted transition-colors hover:border-water hover:text-ink"
      >
        pick on map
      </button>
      {error && <span className="text-[11.5px] text-band2">{error}</span>}
    </div>
  );
}

/** Whatever the machine heard, retypeable after listening to the recording.
 *  This is the "operator types the transcript" resilience rung on demand, not
 *  only during a declared ASR outage -- a short or heavily-accented clip in
 *  any language can come back wrong even while ASR is technically "up". */
function TranscriptEditor({ call }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(call.transcript);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setText(call.transcript);
    setEditing(false);
  }, [call.id]);

  const submit = async () => {
    const t = text.trim();
    if (!t) return;
    setBusy(true);
    try {
      await api.setTranscript(call.id, t);
      setEditing(false);
    } finally {
      setBusy(false);
    }
  };

  if (!editing) {
    return (
      <span>
        <span className="text-ink">{call.transcript}</span>{" "}
        <button
          onClick={() => setEditing(true)}
          className="rounded-sm border border-line px-1.5 py-0.5 text-[11px] text-muted transition-colors hover:border-water hover:text-ink"
        >
          edit
        </button>
        {call.transcript_source === "operator" && (
          <span className="text-water"> · corrected by operator</span>
        )}
      </span>
    );
  }

  return (
    <div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={2}
        autoFocus
        aria-label="Corrected transcript"
        className="w-full rounded-sm border border-line bg-ground px-2 py-1.5 text-[13px] text-ink"
      />
      <div className="mt-1 flex gap-2">
        <button
          onClick={submit}
          disabled={busy || !text.trim()}
          className="rounded-sm border border-water/50 bg-water/10 px-2 py-1 text-[12px] text-water disabled:opacity-40"
        >
          {busy ? "re-triaging…" : "re-triage"}
        </button>
        <button
          onClick={() => {
            setText(call.transcript);
            setEditing(false);
          }}
          disabled={busy}
          className="rounded-sm border border-line px-2 py-1 text-[12px] text-muted"
        >
          cancel
        </button>
      </div>
    </div>
  );
}

function AudioPlayer({ callId }) {
  return (
    <audio
      controls
      preload="none"
      src={api.audioUrl(callId)}
      className="h-8 w-full max-w-[280px]"
      aria-label="Caller's recorded voice"
    >
      Your browser cannot play this recording.
    </audio>
  );
}

/** Escalate/demote the band by one, the operator's own read overruling the
 *  model's -- logged to the audit trail every time, never silent. One click,
 *  or one keypress ("]" / "[") while a call is selected: that is the whole
 *  interaction, deliberately, because a dialog box is the thing that does not
 *  get used at 3 a.m. */
function OverridePanel({ call }) {
  const [busy, setBusy] = useState(false);

  const send = async (direction) => {
    setBusy(true);
    try {
      await api.override(
        call.id,
        direction,
        direction > 0 ? "operator: escalated on scene knowledge" : "operator: demoted on scene knowledge"
      );
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    const onKey = (e) => {
      if (e.target instanceof HTMLElement && /input|textarea/i.test(e.target.tagName)) return;
      if (e.key === "]") send(1);
      if (e.key === "[") send(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [call.id]);

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={() => send(-1)}
        disabled={busy || call.band === 0}
        title="Demote one band — key [ "
        className="rounded-sm border border-line bg-raised px-2.5 py-1.5 text-[12px] font-medium text-ink-2 transition-colors hover:border-band2 disabled:opacity-35"
      >
        ▼ demote
      </button>
      <button
        onClick={() => send(1)}
        disabled={busy || call.band === 3}
        title="Escalate one band — key ]"
        className="rounded-sm border border-line bg-raised px-2.5 py-1.5 text-[12px] font-medium text-ink-2 transition-colors hover:border-band3 disabled:opacity-35"
      >
        ▲ escalate
      </button>
      {call.override !== 0 && call.override != null && (
        <span className="text-[11.5px] text-band2">operator-adjusted</span>
      )}
      <span className="ml-auto font-mono text-[10.5px] text-muted">[ / ] to override</span>
    </div>
  );
}

export default function DetailPanel({ call, route, routeState, onPickOnMap }) {
  if (!call) {
    return (
      <section
        aria-labelledby="detail-heading"
        className="border-t border-line bg-panel px-5 py-4"
      >
        <h2 id="detail-heading" className="sr-only">
          Call detail
        </h2>
        <p className="text-[13px] text-muted">
          Select a call to see the ground beneath it, and the road to it.
        </p>
      </section>
    );
  }

  const ground = call.exposure_pending
    ? "locating — no fix yet, ranked on report only"
    : call.hand_m == null
      ? "outside the mapped area — ranked on report only"
      : `${call.hand_m.toFixed(1)} m above nearest drainage · exposure ${call.exposure_p75}`;

  const live = call.live_stage && !String(call.live_stage).startsWith("done");
  const lowConfidence =
    call.has_audio &&
    call.asr_conf != null &&
    (call.asr_conf < 0.75 || (call.asr_duration_s != null && call.asr_duration_s < 3));

  return (
    <section
      aria-labelledby="detail-heading"
      className="max-h-[46vh] overflow-y-auto border-t border-line bg-panel px-5 py-3"
    >
      <h2 id="detail-heading" className="sr-only">
        Call detail
      </h2>

      <OverridePanel call={call} />

      <dl className="mt-2.5">
        <Line label="position">
          {call.rank} · band {call.band} ({bandName(call.band)})
          {call.floor_applied && (
            <span className="text-band3"> · life-threat floor applied</span>
          )}
        </Line>
        <Line label="ground">
          <span className={call.hand_m == null ? "text-band2" : "text-water"}>
            {ground}
          </span>
        </Line>
        {call.lat != null && (
          <Line label="cross-section">
            <CrossSection callId={call.id} lat={call.lat} lon={call.lon} />
          </Line>
        )}
        <Line label="location">
          {call.lat == null ? (
            <>
              <span className="text-band2">
                {live ? "not yet known — agent is asking" : "no landmark heard — not yet placed"}
              </span>
              <LocationFix callId={call.id} onPickOnMap={onPickOnMap} />
            </>
          ) : (
            <>
              {call.lat.toFixed(5)}, {call.lon.toFixed(5)} · ±{call.error_radius_m} m ·{" "}
              {call.location_source}
              {call.landmark && ` (${call.landmark})`}
              {call.place && ` · ${call.place.name}`}
            </>
          )}
        </Line>
        <Line label="extracted by">
          {call.extractor}
          {call.extractor_pending
            ? " — spotter now, model still reading"
            : call.extractor === "keyword"
              ? " (model unavailable — spotter)"
              : ""}
          {call.extractor === "llm+floor" && call.extractor_note && (
            <span className="text-band3"> · {call.extractor_note}</span>
          )}
          <span className="text-muted"> · surface {call.model_ver}</span>
        </Line>
        <Line label="dispatch">
          {routeState === "loading" && <span className="text-muted">routing…</span>}
          {routeState === "error" && (
            <span className="text-band2">no road graph — run build_roads.py</span>
          )}
          {routeState === "done" && route && (
            <>
              {route.route ? (
                <>
                  <span className="text-ink">{route.depot.name}</span> ·{" "}
                  {(route.length_m / 1000).toFixed(1)} km · ~{route.eta_min} min
                  {route.detour_m > 0 ? (
                    <span className="text-water">
                      {" "}
                      · +{route.detour_m} m around {route.blocked_on_direct.length}{" "}
                      flooded segment
                      {route.blocked_on_direct.length === 1 ? "" : "s"}
                    </span>
                  ) : (
                    " · direct road is clear"
                  )}
                </>
              ) : route.reason === "boat_or_air_required" ? (
                <>
                  <span className="font-bold text-band3">BOAT OR AIR REQUIRED</span> ·
                  every road approach from {route.depot.name} crosses water
                </>
              ) : (
                <span className="text-muted">{route.detail ?? route.reason}</span>
              )}
            </>
          )}
        </Line>
        <Line label="transcript">
          <TranscriptEditor call={call} />
          {lowConfidence && (
            <p className="mt-1 font-sans text-band2">
              LOW-CONFIDENCE TRANSCRIPTION ({Math.round((call.asr_conf ?? 0) * 100)}%
              {call.asr_duration_s != null && `, ${call.asr_duration_s.toFixed(1)}s clip`}) —
              listen to the recording below and correct it if it's wrong
            </p>
          )}
        </Line>
        {call.has_audio && (
          <Line label="recording">
            <AudioPlayer callId={call.id} />
          </Line>
        )}
      </dl>
    </section>
  );
}
