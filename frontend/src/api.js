import { useCallback, useEffect, useRef, useState } from "react";

const json = async (path, init) => {
  const r = await fetch(path, init);
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const body = await r.json();
      detail = body.detail ?? detail;
    } catch {
      /* not json; keep the status text */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return r.json();
};

export const api = {
  queue: () => json("/queue"),
  clusters: () => json("/clusters"),
  teams: () => json("/teams"),
  assignTeam: (callId, teamId) =>
    json(`/calls/${callId}/assign`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ team_id: teamId }),
    }),
  meta: () => json("/surface/meta"),
  impassable: () => json("/roads/impassable"),
  dispatch: (id) => json(`/dispatch/${id}`),
  campRisk: (id) => json(`/calls/${id}/camp-risk`),
  briefing: (id) => json(`/dispatch/${id}/briefing`),
  declareCamp: (camp) =>
    json("/camps", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(camp),
    }),
  audit: (limit = 60) => json(`/audit?limit=${limit}`),
  twilioStatus: () => json("/twilio/status"),
  dial: (to) => json(`/twilio/dial?to=${encodeURIComponent(to)}`, { method: "POST" }),
  surge: () => json("/demo/surge", { method: "POST" }),
  reset: () => json("/demo/reset", { method: "POST" }),
  cut: (offline) => json(`/demo/cut-network?offline=${offline}`, { method: "POST" }),
  override: (id, direction, reason) =>
    json(`/calls/${id}/override`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction, reason }),
    }),
  setLocation: (id, body) =>
    json(`/calls/${id}/location`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  audioUrl: (id) => `/audio/${id}`,
  section: (lat, lon) => json(`/exposure/section?lat=${lat}&lon=${lon}`),
  setTranscript: (id, text) =>
    json(`/calls/${id}/transcript`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }),
  upload: (files) => {
    const body = new FormData();
    for (const f of files) body.append("files", f, f.name);
    return json("/intake/audio", { method: "POST", body });
  },
  removeCall: (id) => json(`/calls/${id}`, { method: "DELETE" }),
};

/**
 * The board, over server-sent events.
 *
 * The stream is the only thing that writes call state, so every panel reads one
 * source and nothing can disagree with anything else on screen. If it drops we
 * say so out loud rather than showing a board that has quietly stopped moving.
 */
export function useQueue() {
  const [state, setState] = useState({
    calls: [],
    degradation: null,
    connected: false,
    everConnected: false,
  });
  const retry = useRef(null);

  useEffect(() => {
    let es;
    let closed = false;
    const connect = () => {
      es = new EventSource("/queue/stream");
      es.onopen = () =>
        setState((s) => ({ ...s, connected: true, everConnected: true }));
      es.onmessage = (e) => {
        const q = JSON.parse(e.data);
        setState((s) => ({
          ...s,
          calls: q.calls,
          degradation: q.degradation,
          connected: true,
          everConnected: true,
        }));
      };
      es.onerror = () => {
        es.close();
        setState((s) => ({ ...s, connected: false }));
        if (!closed) retry.current = setTimeout(connect, 2000);
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retry.current);
      es?.close();
    };
  }, []);

  return state;
}

/** Poll something that has no stream of its own, and pause when the tab is hidden. */
export function usePoll(fn, ms, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const saved = useRef(fn);
  saved.current = fn;

  const run = useCallback(async () => {
    try {
      setData(await saved.current());
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    run();
    const t = setInterval(() => {
      if (!document.hidden) run();
    }, ms);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, refresh: run };
}

export const bandName = (b) =>
  ["no immediate risk", "minor", "serious", "life threat"][b] ?? "unknown";

export const bandClass = (b) =>
  b >= 3
    ? "text-band3 border-band3/45 bg-band3/12"
    : b === 2
      ? "text-band2 border-band2/40 bg-band2/12"
      : "text-band1 border-band1/35 bg-band1/10";

export const fmtAge = (s) =>
  s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
