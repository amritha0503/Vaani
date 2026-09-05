import { useRef, useState } from "react";
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
  const [gpsError, setGpsError] = useState(null);
  const inputRef = useRef(null);

  const useGps = (id) => {
    if (!("geolocation" in navigator)) {
      setGpsError("This browser has no location support.");
      return;
    }
    setGpsBusy(id);
    setGpsError(null);
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        try {
          await api.setLocation(id, {
            lat: pos.coords.latitude,
            lon: pos.coords.longitude,
            error_radius_m: pos.coords.accuracy,
            source: "gps",
          });
        } catch (e) {
          setGpsError(e.message);
        } finally {
          setGpsBusy(null);
        }
      },
      (err) => {
        setGpsBusy(null);
        setGpsError(
          err?.code === 1
            ? "Blocked: allow location for this page in the address-bar site settings, or use \"pick on map\"."
            : "Location was not shared by the browser."
        );
      },
      { enableHighAccuracy: true, timeout: 12000 }
    );
  };

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

  const byId = new Map(calls.map((c) => [c.id, c]));
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
          Each file lands on the board immediately and fills in as it is worked:
          transcribed locally by Whisper, triaged by the spotter, geocoded from any
          landmark it mentions, then re-read by the model. Nothing is uploaded
          anywhere — the audio stays on this machine.
        </p>
      </div>

      {error && (
        <p role="alert" className="mt-3 font-mono text-[11px] text-band3">
          {error}
        </p>
      )}
      {gpsError && (
        <p role="alert" className="mt-3 font-mono text-[11px] text-band2">
          {gpsError}
        </p>
      )}

      {queued.length > 0 && (
        <ul className="mt-4 space-y-px" aria-label="Uploaded recordings">
          {queued.map((q) => {
            const call = byId.get(q.id);
            const st = statusOf(call);
            const needsLocation = call && !call.intake_stage && call.lat == null;
            return (
              <li key={q.id}>
                <button
                  onClick={() => call && onSelect(q.id)}
                  disabled={!call}
                  className="flex w-full items-center gap-3 rounded-[3px] px-2 py-1.5 text-left transition-colors hover:bg-raised disabled:cursor-default"
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink-2">
                    {q.filename}
                  </span>
                  <span className={`font-mono text-[10.5px] ${st.tone}`}>{st.label}</span>
                </button>
                {needsLocation && (
                  <div className="flex items-center gap-2 px-2 pb-1.5">
                    <span className="font-mono text-[10px] text-band2">no landmark heard —</span>
                    <button
                      onClick={() => useGps(q.id)}
                      disabled={gpsBusy === q.id}
                      className="rounded-sm border border-line px-1.5 py-0.5 font-mono text-[10px] text-muted transition-colors hover:border-water hover:text-ink disabled:opacity-50"
                    >
                      {gpsBusy === q.id ? "locating…" : "use my GPS"}
                    </button>
                    <button
                      onClick={() => onPickOnMap?.(q.id)}
                      className="rounded-sm border border-line px-1.5 py-0.5 font-mono text-[10px] text-muted transition-colors hover:border-water hover:text-ink"
                    >
                      pick on map
                    </button>
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
