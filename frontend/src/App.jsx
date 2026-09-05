import { useEffect, useRef, useState } from "react";
import { api, useQueue } from "./api.js";
import Header from "./components/Header.jsx";
import Queue from "./components/Queue.jsx";
import MapPanel from "./components/MapPanel.jsx";
import DetailPanel from "./components/DetailPanel.jsx";
import { UploadPanel, TwilioPanel } from "./components/Intake.jsx";
import { SeverityPanel, LocationPanel } from "./components/Groups.jsx";
import { TeamsPanel } from "./components/TeamsPanel.jsx";
import AuditPanel from "./components/Audit.jsx";

export default function App() {
  const { calls, degradation, connected } = useQueue();
  const [tab, setTab] = useState("ops");
  const [selected, setSelected] = useState(null);
  const [bandFilter, setBandFilter] = useState(null);
  const [teamFilter, setTeamFilter] = useState(null);
  const [route, setRoute] = useState(null);
  const [routeState, setRouteState] = useState("idle");
  const [announcement, setAnnouncement] = useState("");
  const [placing, setPlacing] = useState(null); // call id being placed by a map click, or null
  const lastTop = useRef(null);

  const visible = calls
    .filter((c) => bandFilter == null || c.band === bandFilter)
    .filter((c) => teamFilter == null || c.assigned_team?.team_id === teamFilter);
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

  const select = (id) => {
    setSelected(id);
    setTab("ops");
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
        tab={tab}
        onTab={setTab}
        degradation={degradation}
        connected={connected}
      />

      <p aria-live="polite" aria-atomic="true" className="sr-only">
        {announcement}
      </p>

      {tab === "audit" ? (
        <main
          id="panel-audit"
          role="tabpanel"
          aria-labelledby="tab-audit"
          className="min-h-0 flex-1 overflow-y-auto p-3"
        >
          <AuditPanel onSelect={select} />
        </main>
      ) : tab === "teams" ? (
        <main
          id="panel-teams"
          role="tabpanel"
          aria-labelledby="tab-teams"
          className="min-h-0 flex-1 overflow-y-auto p-3"
        >
          <TeamsPanel
            onSelectTeam={(t) => {
              setTeamFilter(t);
              if (t) setTab("ops");
            }}
            activeTeam={teamFilter}
            onSelectCall={select}
          />
        </main>
      ) : tab === "ops" ? (
        <main
          id="panel-ops"
          role="tabpanel"
          aria-labelledby="tab-ops"
          className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] lg:overflow-hidden"
        >
          <div className="flex min-h-0 flex-col border-line lg:border-r">
            {bandFilter != null && (
              <div className="flex items-center gap-3 border-b border-line bg-raised px-5 py-2">
                <p className="font-mono text-[11px] text-ink-2">
                  Filtered to band {bandFilter} — {visible.length} of {calls.length} calls
                </p>
                <button
                  onClick={() => setBandFilter(null)}
                  className="ml-auto rounded-sm border border-line px-2 py-1 font-mono text-[10px] text-muted hover:text-ink"
                >
                  Clear filter
                </button>
              </div>
            )}
            {teamFilter != null && (
              <div className="flex items-center gap-3 border-b border-line bg-raised px-5 py-2">
                <p className="font-mono text-[11px] text-ink-2">
                  Assigned to{" "}
                  <span className="font-bold text-water">
                    {calls.find((c) => c.assigned_team?.team_id === teamFilter)?.assigned_team
                      ?.team_name || teamFilter}
                  </span>{" "}
                  — {visible.length} targets
                </p>
                <button
                  onClick={() => setTeamFilter(null)}
                  className="ml-auto rounded-sm border border-line px-2 py-1 font-mono text-[10px] text-muted hover:text-ink"
                >
                  Clear team filter
                </button>
              </div>
            )}
            <Queue
              calls={visible}
              selected={selected}
              onSelect={setSelected}
              connected={connected}
            />
          </div>
          <div className="flex min-h-0 flex-col">
            <MapPanel
              calls={calls}
              selected={selected}
              onSelect={setSelected}
              route={route}
              pickMode={placing != null}
              onPick={onPick}
              onCancelPick={() => setPlacing(null)}
              activeTeam={teamFilter}
            />
            <DetailPanel call={call} route={route} routeState={routeState} onPickOnMap={pickOnMap} />
          </div>
        </main>
      ) : (
        <main
          id="panel-intake"
          role="tabpanel"
          aria-labelledby="tab-intake"
          className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto p-3 lg:grid-cols-2 xl:grid-cols-4"
        >
          <UploadPanel calls={calls} onSelect={select} onPickOnMap={pickOnMap} />
          <TwilioPanel />
          <SeverityPanel
            calls={calls}
            activeBand={bandFilter}
            onPickBand={(b) => {
              setBandFilter(b);
              if (b != null) setTab("ops");
            }}
          />
          <LocationPanel calls={calls} onSelect={select} />
        </main>
      )}
    </div>
  );
}
