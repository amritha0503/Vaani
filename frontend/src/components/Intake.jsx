import { useEffect, useRef, useState } from "react";
import { api, usePoll } from "../api.js";

function Panel({ title, note, children, className = "" }) {
  return (
    <section
      className={`flex min-h-0 flex-col rounded-sm border border-line bg-panel ${className}`}
    >
      <div className="flex items-baseline gap-3 border-b border-line px-4 py-2.5">
        <h2 className="text-[12px] font-semibold uppercase tracking-[0.12em] text-ink-2">
          {title}
        </h2>
        {note && <p className="font-mono text-[10.5px] text-muted">{note}</p>}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
    </section>
  );
}

/* ------------------------------------------------------------------ upload */

function statusOf(call) {
  if (!call) return { label: "queued", tone: "text-muted" };
  if (call.intake_stage === "transcribing")
    return { label: "transcribing", tone: "text-water" };
  if (call.intake_stage === "locating")
    return { label: "geocoding landmark", tone: "text-water" };
  if (call.extractor_pending) return { label: "model reading", tone: "text-water" };
  return {
    label: `position ${call.rank} · band ${call.band}`,
    tone: call.band >= 3 ? "text-band3" : "text-ok",
  };
}

export function UploadPanel({ calls, onSelect, onPickOnMap }) {
  const [queued, setQueued] = useState([]); // {id, filename}
  const [error, setError] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [gpsBusy, setGpsBusy] = useState(null); // call id currently locating
  const [gpsStatus, setGpsStatus] = useState({}); // id -> { state: 'locating' | 'granted' | 'denied', error?: string }
  const promptedRef = useRef(new Set());
  const inputRef = useRef(null);

  const byId = new Map(calls.map((c) => [c.id, c]));

  const requestGpsPermission = (id) => {
    if (!("geolocation" in navigator)) {
      setGpsStatus((prev) => ({
        ...prev,
        [id]: { state: "denied", error: "This browser has no geolocation support." },
      }));
      return;
    }
    setGpsBusy(id);
    setGpsStatus((prev) => ({ ...prev, [id]: { state: "locating" } }));

    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        try {
          await api.setLocation(id, {
            lat: pos.coords.latitude,
            lon: pos.coords.longitude,
            error_radius_m: pos.coords.accuracy,
            source: "gps",
          });
          setGpsStatus((prev) => ({
            ...prev,
            [id]: { state: "granted", accuracy: Math.round(pos.coords.accuracy) },
          }));
        } catch (e) {
          setGpsStatus((prev) => ({
            ...prev,
            [id]: { state: "denied", error: e.message },
          }));
        } finally {
          setGpsBusy(null);
        }
      },
      (err) => {
        setGpsBusy(null);
        setGpsStatus((prev) => ({
          ...prev,
          [id]: {
            state: "denied",
            error:
              err?.code === 1
                ? "Permission denied. Allow location access in browser or pick on map."
                : "Unable to retrieve device location.",
          },
        }));
      },
      { enableHighAccuracy: true, timeout: 12000 }
    );
  };

  // Automatically check each completed upload:
  // If no location mentioned in audio, proactively request user permission.
  useEffect(() => {
    queued.forEach((q) => {
      const call = byId.get(q.id);
      if (
        call &&
        !call.intake_stage &&
        !call.extractor_pending &&
        call.lat == null &&
        !promptedRef.current.has(q.id)
      ) {
        promptedRef.current.add(q.id);
        requestGpsPermission(q.id);
      }
    });
  }, [queued, calls]);

  const send = async (files) => {
    const audio = [...files].filter(
      (f) => f.type.startsWith("audio") || /\.(wav|mp3|m4a|ogg|flac|webm)$/i.test(f.name)
    );
    if (!audio.length) {
      setError("Those are not audio files. WAV, MP3, M4A, OGG, FLAC or WEBM.");
      return;
    }
    setError(null);
    try {
      const r = await api.upload(audio);
      setQueued((q) => [...r.queued, ...q]);
    } catch (e) {
      setError(e.message);
    }
  };

  const done = queued.filter((q) => {
    const c = byId.get(q.id);
    return c && !c.extractor_pending && !c.intake_stage;
  }).length;

  return (
    <Panel
      title="Upload recordings"
      note={queued.length ? `${done} of ${queued.length} triaged` : "one worker, in order"}
    >
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          send(e.dataTransfer.files);
        }}
        className={`rounded-sm border border-dashed p-5 text-center transition-colors ${
          dragging ? "border-water bg-water/5" : "border-line"
        }`}
      >
        <p className="text-[13px] text-ink-2">
          Drop call recordings here, or
        </p>
        <button
          onClick={() => inputRef.current?.click()}
          className="mt-2 rounded-sm border border-line bg-raised px-3 py-1.5 text-[12.5px] font-medium text-ink transition-colors hover:border-water"
        >
          Choose audio files
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept="audio/*,.wav,.mp3,.m4a,.ogg,.flac,.webm"
          className="sr-only"
          aria-label="Choose call recordings to upload"
          onChange={(e) => {
            send(e.target.files);
            e.target.value = "";
          }}
        />
        <p className="mx-auto mt-3 max-w-md font-mono text-[10.5px] leading-relaxed text-muted">
          Each file is transcribed and checked for a spoken landmark. If a location is
          mentioned, it is automatically preferred and geocoded. If no location is mentioned,
          the system asks permission to access your device location to place the emergency call.
        </p>
      </div>

      {error && (
        <p role="alert" className="mt-3 font-mono text-[11px] text-band3">
          {error}
        </p>
      )}

      {queued.length > 0 && (
        <ul className="mt-4 space-y-2" aria-label="Uploaded recordings">
          {queued.map((q) => {
            const call = byId.get(q.id);
            const st = statusOf(call);
            const isProcessing = call && (call.intake_stage || call.extractor_pending);
            const hasAudioLocation =
              call && !isProcessing && call.lat != null && call.location_source !== "gps";
            const needsUserLocation =
              call && !isProcessing && call.lat == null;
            const hasGpsLocation =
              call && !isProcessing && call.lat != null && call.location_source === "gps";
            const gpsInfo = gpsStatus[q.id];

            return (
              <li
                key={q.id}
                className="rounded-[4px] border border-line bg-panel p-2 transition-colors hover:border-line/80"
              >
                <button
                  onClick={() => call && onSelect(q.id)}
                  disabled={!call}
                  className="flex w-full items-center gap-3 text-left transition-colors disabled:cursor-default"
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] font-medium text-ink">
                    {q.filename}
                  </span>
                  <span className={`font-mono text-[10.5px] ${st.tone}`}>{st.label}</span>
                </button>

                {/* Case 1: Location mentioned in audio -> PREFER THIS LOCATION */}
                {hasAudioLocation && (
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5 rounded-sm bg-ok/10 px-2 py-1 font-mono text-[10.5px] text-ok">
                    <span>📍</span>
                    <span className="font-semibold">
                      Spoken location detected: "{call.landmark || 'Landmark'}"
                    </span>
                    <span className="text-muted">
                      · preferred & placed
                      {call.hand_m != null ? ` (${call.hand_m.toFixed(1)}m above drainage)` : ""}
                    </span>
                  </div>
                )}

                {/* Case 2: Placed via user device GPS */}
                {hasGpsLocation && (
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5 rounded-sm bg-water/10 px-2 py-1 font-mono text-[10.5px] text-water">
                    <span>🛰️</span>
                    <span className="font-semibold">Placed via user device GPS</span>
                    <span className="text-muted">
                      · ({call.lat.toFixed(4)}, {call.lon.toFixed(4)})
                      {call.error_radius_m ? ` ±${Math.round(call.error_radius_m)}m` : ""}
                    </span>
                  </div>
                )}

                {/* Case 3: No location mentioned in audio -> ASK USER PERMISSION */}
                {needsUserLocation && (
                  <div className="mt-2 rounded-sm border border-band2/40 bg-band2/10 p-2.5">
                    <div className="flex items-start gap-2">
                      <span className="text-[13px] text-band2">⚠️</span>
                      <div className="min-w-0 flex-1">
                        <p className="font-mono text-[11px] font-semibold text-ink">
                          No location mentioned in audio
                        </p>
                        <p className="mt-0.5 font-mono text-[10px] text-muted">
                          Emergency teams need your location to dispatch help. Grant access to your device GPS?
                        </p>
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <button
                            onClick={() => requestGpsPermission(q.id)}
                            disabled={gpsBusy === q.id}
                            className="flex items-center gap-1 rounded-sm bg-water px-2.5 py-1 font-mono text-[10.5px] font-semibold text-ground shadow-sm hover:opacity-90 disabled:opacity-50"
                          >
                            <span>📍</span>
                            {gpsBusy === q.id ? "Requesting access…" : "Allow Location Access"}
                          </button>
                          <button
                            onClick={() => onPickOnMap?.(q.id)}
                            className="rounded-sm border border-line bg-raised px-2 py-1 font-mono text-[10px] text-muted hover:text-ink"
                          >
                            Pick on Map Instead
                          </button>
                        </div>
                        {gpsInfo?.error && (
                          <p className="mt-1.5 font-mono text-[10px] text-band2">
                            {gpsInfo.error}
                          </p>
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ twilio */

function Field({ label, value, ok }) {
  return (
    <div className="flex items-baseline gap-2 py-1">
      <span
        aria-hidden="true"
        className={`font-mono text-[11px] ${ok ? "text-ok" : "text-band2"}`}
      >
        {ok ? "✓" : "○"}
      </span>
      <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
        {label}
      </span>
      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink-2">
        {value ?? <span className="text-band2">not set</span>}
      </span>
    </div>
  );
}

export function TwilioPanel() {
  const { data: st, refresh } = usePoll(api.twilioStatus, 15_000);
  const [to, setTo] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  const ready = st?.configured && st?.public_url_set && st?.from_number;

  const dial = async (e) => {
    e.preventDefault();
    setBusy(true);
    setResult(null);
    try {
      const r = await api.dial(to);
      setResult({ ok: true, text: `dialling ${r.to} — ${r.status}` });
    } catch (err) {
      setResult({ ok: false, text: err.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="Call automation"
      note={ready ? "ready to dial" : "not configured"}
    >
      <div className="rounded-sm border border-line bg-raised/60 p-3">
        <Field label="account" value={st?.account_sid} ok={!!st?.configured} />
        <Field label="from" value={st?.from_number} ok={!!st?.from_number} />
        <Field label="public url" value={st?.webhook_url} ok={!!st?.public_url_set} />
      </div>

      {!ready && (
        <div className="mt-3 space-y-2">
          <p className="font-mono text-[10.5px] leading-relaxed text-muted">
            Twilio is the ingress only — transcription, extraction, geocoding,
            exposure and ranking all stay on this box. To wire it, start a tunnel,
            set three variables in the window that runs the server, then point the
            number&apos;s <em>A call comes in</em> webhook at the URL above.
          </p>
          <pre className="whitespace-pre-wrap break-words rounded-sm border border-line bg-ground px-3 py-2 font-mono text-[10.5px] leading-relaxed text-ink-2">
{`cloudflared tunnel --url http://localhost:8000

$env:VAANI_PUBLIC_URL   = "https://<tunnel>.trycloudflare.com"
$env:TWILIO_ACCOUNT_SID = "AC..."
$env:TWILIO_AUTH_TOKEN  = "..."
$env:TWILIO_NUMBER      = "+1..."`}
          </pre>
          <button
            onClick={refresh}
            className="rounded-sm border border-line bg-raised px-3 py-1.5 text-[12px] text-ink-2 transition-colors hover:border-water hover:text-ink"
          >
            Re-check configuration
          </button>
        </div>
      )}

      <form onSubmit={dial} className="mt-4 space-y-2">
        <label
          htmlFor="dial-to"
          className="block font-mono text-[10px] uppercase tracking-[0.08em] text-muted"
        >
          Call a number back — the same agent runs the conversation
        </label>
        <div className="flex gap-2">
          <input
            id="dial-to"
            type="tel"
            inputMode="tel"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder="+919778167232"
            disabled={!ready}
            className="min-w-0 flex-1 rounded-sm border border-line bg-ground px-3 py-1.5 font-mono text-[12px] text-ink placeholder:text-muted/70 disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!ready || !to || busy}
            className="rounded-sm border border-line bg-raised px-3 py-1.5 text-[12.5px] font-medium text-ink transition-colors hover:border-water disabled:opacity-40"
          >
            {busy ? "Dialling…" : "Dial"}
          </button>
        </div>
        {result && (
          <p
            role="status"
            className={`font-mono text-[11px] ${result.ok ? "text-ok" : "text-band3"}`}
          >
            {result.text}
          </p>
        )}
      </form>
    </Panel>
  );
}

export { Panel };
