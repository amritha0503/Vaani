import { useEffect, useRef, useState } from "react";
import { api, useQueue } from "./api.js";
import Header from "./components/Header.jsx";
import Queue from "./components/Queue.jsx";
import MapPanel from "./components/MapPanel.jsx";
import DetailPanel from "./components/DetailPanel.jsx";
import { UploadPanel, TwilioPanel } from "./components/Intake.jsx";
import { SeverityPanel, LocationPanel } from "./components/Groups.jsx";
import AuditPanel from "./components/Audit.jsx";

export default function App() {
  const { calls, degradation, connected } = useQueue();
  const [step, setStep] = useState("triage");
  const [selected, setSelected] = useState(null);
  const [bandFilter, setBandFilter] = useState(null);
  const [route, setRoute] = useState(null);
  const [routeState, setRouteState] = useState("idle");
  const [announcement, setAnnouncement] = useState("");
  const [placing, setPlacing] = useState(null); // call id being placed by a map click, or null
  const lastTop = useRef(null);

  const visible = bandFilter == null ? calls : calls.filter((c) => c.band === bandFilter);
  const call = calls.find((c) => c.id === selected) ?? null;

  // The route is fetched once per selected call and cached server-side too, so
  // the line does not blink out every time the queue repaints.
  useEffect(() => {
    if (!selected) {
      setRoute(null);
      setRouteState("idle");
      return;
    }
    let alive = true;
    setRouteState("loading");
    api
      .dispatch(selected)
      .then((r) => alive && (setRoute(r), setRouteState("done")))
      .catch(() => alive && (setRoute(null), setRouteState("error")));
    return () => {
      alive = false;
    };
  }, [selected]);

  // Say what changed, once, for anyone not watching the screen. Only the top of
  // the queue is announced: a screen reader reading twelve reordered rows aloud
  // during a surge is worse than useless.
  useEffect(() => {
    const top = calls[0];
    if (!top || top.id === lastTop.current) return;
    lastTop.current = top.id;
    const threats = calls.filter((c) => c.band >= 3).length;
    setAnnouncement(
      `${calls.length} calls, ${threats} life threat. Now first: ${top.transcript}. ${top.reason}`
    );
  }, [calls]);

  // Picking a call anywhere in the pipeline (Triage's queue, its severity and
  // location groupings, the audit trail) carries it forward to Dispatch,
  // already selected -- the same step where its map, route and full detail live.
  const select = (id) => {
    setSelected(id);
    setStep("dispatch");
  };

  const pickOnMap = (id) => {
    select(id);
    setPlacing(id);
  };

  const onPick = async (coords) => {
    if (!placing) return;
    const id = placing;
    setPlacing(null);
    try {
      await api.setLocation(id, { ...coords, source: "map_click" });
    } catch (e) {
      console.error("placing call on map failed:", e.message);
    }
  };

  return (
    <div className="flex h-full flex-col bg-ground">
      <a
        href="#queue-heading"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-sm focus:bg-raised focus:px-3 focus:py-2 focus:text-[13px]"
      >
        Skip to the rescue queue
      </a>

      <Header
        step={step}
        onStep={setStep}
        degradation={degradation}
        connected={connected}
      />

      <p aria-live="polite" aria-atomic="true" className="sr-only">
        {announcement}
      </p>

      {step === "intake" ? (
        <main
          id="panel-intake"
          role="tabpanel"
          aria-labelledby="tab-intake"
          className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto p-3 lg:grid-cols-2"
        >
          <UploadPanel calls={calls} onSelect={select} onPickOnMap={pickOnMap} />
          <TwilioPanel />
        </main>
      ) : step === "triage" ? (
        <main
          id="panel-triage"
          role="tabpanel"
          aria-labelledby="tab-triage"
          className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] lg:overflow-hidden"
        >
          <div className="flex min-h-0 flex-col border-line lg:border-r">
            {bandFilter != null && (
              <div className="flex items-center gap-3 border-b border-line bg-raised px-5 py-2">
                <p className="text-[12.5px] text-ink-2">
                  Filtered to band {bandFilter} — {visible.length} of {calls.length} calls
                </p>
                <button
                  onClick={() => setBandFilter(null)}
                  className="ml-auto rounded-sm border border-line px-2 py-1 text-[11.5px] text-muted hover:text-ink"
                >
                  Clear filter
                </button>
              </div>
            )}
            <Queue
              calls={visible}
              selected={selected}
              onSelect={select}
              connected={connected}
            />
          </div>
          <div className="flex min-h-0 flex-col gap-3 overflow-y-auto p-3">
            <SeverityPanel
              calls={calls}
              activeBand={bandFilter}
              onPickBand={setBandFilter}
            />
            <LocationPanel calls={calls} onSelect={select} />
          </div>
        </main>
      ) : step === "dispatch" ? (
        <main
          id="panel-dispatch"
          role="tabpanel"
          aria-labelledby="tab-dispatch"
          className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:overflow-hidden"
        >
          <MapPanel
            calls={calls}
            selected={selected}
            onSelect={setSelected}
            route={route}
            pickMode={placing != null}
            onPick={onPick}
            onCancelPick={() => setPlacing(null)}
          />
          <DetailPanel call={call} route={route} routeState={routeState} onPickOnMap={pickOnMap} />
        </main>
      ) : (
        <main
          id="panel-audit"
          role="tabpanel"
          aria-labelledby="tab-audit"
          className="min-h-0 flex-1 overflow-y-auto p-3"
        >
          <AuditPanel onSelect={select} />
        </main>
      )}
    </div>
  );
}
