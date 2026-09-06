import { api, bandName, usePoll } from "../api.js";
import { Panel } from "./Panel.jsx";

const BANDS = [3, 2, 1, 0];
const BAND_TONE = {
  3: { bar: "bg-band3", text: "text-band3" },
  2: { bar: "bg-band2", text: "text-band2" },
  1: { bar: "bg-band1", text: "text-band1" },
  0: { bar: "bg-band1/50", text: "text-muted" },
};

/**
 * Severity, as the ranker actually computes it.
 *
 * The band is not a score the model produced -- it is the life-threat floor:
 * trapped or medically critical is band 3 whatever else was said, and terrain
 * can reorder inside a band but never move a call across one.
 */
export function SeverityPanel({ calls, onPickBand, activeBand }) {
  const total = calls.length || 1;
  const groups = BANDS.map((b) => ({
    band: b,
    calls: calls.filter((c) => c.band === b),
  }));
  const floored = calls.filter((c) => c.floor_applied).length;

  return (
    <Panel
      title="Classified by severity"
      note={floored ? `${floored} held up by the life-threat floor` : "band first, always"}
    >
      <ul className="space-y-2">
        {groups.map(({ band, calls: rows }) => {
          const pct = Math.round((rows.length / total) * 100);
          const tone = BAND_TONE[band];
          const active = activeBand === band;
          return (
            <li key={band}>
              <button
                onClick={() => onPickBand(active ? null : band)}
                aria-pressed={active}
                disabled={!rows.length}
                className={`w-full rounded-sm border px-3 py-2 text-left transition-colors disabled:opacity-45 ${
                  active ? "border-water bg-raised" : "border-line hover:bg-raised"
                }`}
              >
                <div className="flex items-baseline gap-2">
                  <span className={`font-mono text-[12px] font-bold ${tone.text}`}>
                    B{band}
                  </span>
                  <span className="text-[13px] text-ink-2">{bandName(band)}</span>
                  <span className="ml-auto font-mono text-[13px] tabular-nums text-ink">
                    {rows.length}
                  </span>
                </div>
                <div
                  className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-line/60"
                  role="img"
                  aria-label={`${rows.length} of ${calls.length} calls, ${pct} percent`}
                >
                  <div className={`h-full ${tone.bar}`} style={{ width: `${pct}%` }} />
                </div>
                {rows.length > 0 && (
                  <p className="mt-1.5 truncate text-[12.5px] text-muted">
                    {rows
                      .slice(0, 2)
                      .map((r) => r.transcript)
                      .join(" · ")}
                  </p>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-[12.5px] leading-relaxed text-muted">
        Band comes from the reported life threat, never from terrain. Exposure
        reorders calls inside a band and can never move one across a band
        boundary — click a band to filter the queue to it.
      </p>
    </Panel>
  );
}

/**
 * The board grouped by settlement.
 *
 * Thirty calls from one village is a different response from thirty calls spread
 * across a district, and a flat ranked list hides which one you are in.
 */
export function LocationPanel({ onSelect, calls }) {
  const { data, error } = usePoll(api.clusters, 3000, [calls.length]);
  const groups = data?.groups ?? [];

  return (
    <Panel
      title="Grouped by location"
      note={groups.length ? `${groups.length} places` : "waiting for fixes"}
    >
      {error && (
        <p role="alert" className="text-[12.5px] text-band3">
          {error}
        </p>
      )}
      {!groups.length && !error && (
        <p className="text-[12.5px] leading-relaxed text-muted">
          No calls placed yet. A landmark a caller names is geocoded against 393
          local OSM places; when nothing matches, the call stays here rather than
          being given a coordinate nobody stands at.
        </p>
      )}
      <ul className="space-y-1.5">
        {groups.map((g) => {
          const unplaced = g.lat == null;
          return (
            <li key={g.place}>
              <button
                onClick={() => onSelect(g.ids[0])}
                className="w-full rounded-sm border border-line px-3 py-2 text-left transition-colors hover:bg-raised"
              >
                <div className="flex items-baseline gap-2">
                  <span
                    className={`truncate text-[13.5px] ${unplaced ? "text-muted italic" : "text-ink"}`}
                  >
                    {g.place}
                  </span>
                  {g.kind && (
                    <span className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-muted">
                      {g.kind}
                    </span>
                  )}
                  <span className="ml-auto font-mono text-[12px] tabular-nums text-ink-2">
                    {g.calls} call{g.calls === 1 ? "" : "s"}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap items-baseline gap-x-3 font-mono text-[11.5px] text-muted">
                  {g.life_threat > 0 && (
                    <span className="text-band3">{g.life_threat} life threat</span>
                  )}
                  {g.min_hand_m != null && (
                    <span className="text-water">
                      lowest {g.min_hand_m} m above drainage
                    </span>
                  )}
                  {g.max_exposure > 0 && <span>peak exposure {g.max_exposure}</span>}
                  <span className="ml-auto">best position #{g.best_rank}</span>
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
