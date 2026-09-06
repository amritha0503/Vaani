import { useEffect, useMemo, useRef, useState } from "react";
import { api, bandName, usePoll } from "../api.js";

/** lat/lon -> percentage of the surface image, which is a plain equirectangular
 *  clip. No tile server, no projection library: the raster is rendered locally
 *  by the API so the map survives the cable pull. */
const project = (meta, lat, lon) =>
  meta && meta.available
    ? {
        x: ((lon - meta.west) / (meta.east - meta.west)) * 100,
        y: ((meta.north - lat) / (meta.north - meta.south)) * 100,
      }
    : null;

/** The inverse of project(): a click position on the image back to a
 *  coordinate, for "pick on map" placement. Same flat equirectangular clip,
 *  so this is exact, not an approximation. */
const unproject = (meta, xPct, yPct) => ({
  lon: meta.west + (xPct / 100) * (meta.east - meta.west),
  lat: meta.north - (yPct / 100) * (meta.north - meta.south),
});

const pathOf = (meta, coords) => {
  let d = "";
  for (const [lon, lat] of coords) {
    const p = project(meta, lat, lon);
    if (!p) continue;
    d += `${d ? "L" : "M"}${p.x.toFixed(3)} ${p.y.toFixed(3)} `;
  }
  return d;
};

export default function MapPanel({ calls, selected, onSelect, route, pickMode, onPick, onCancelPick }) {
  const { data: meta } = usePoll(api.meta, 60_000);
  const [cut, setCut] = useState(null);
  const [showCut, setShowCut] = useState(true);
  const [beforeAfter, setBeforeAfter] = useState(100); // 100 = all "after" (exposure)
  const wrapRef = useRef(null);

  const onMapClick = (e) => {
    if (!pickMode || !meta?.available) return;
    const rect = wrapRef.current.getBoundingClientRect();
    const xPct = ((e.clientX - rect.left) / rect.width) * 100;
    const yPct = ((e.clientY - rect.top) / rect.height) * 100;
    onPick(unproject(meta, xPct, yPct));
  };

  useEffect(() => {
    if (!pickMode) return;
    const onKey = (e) => e.key === "Escape" && onCancelPick?.();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pickMode, onCancelPick]);

  useEffect(() => {
    let alive = true;
    api
      .impassable()
      .then((g) => alive && setCut(g))
      .catch(() => setCut({ coordinates: [], count: 0, unavailable: true }));
    return () => {
      alive = false;
    };
  }, []);

  const cutPaths = useMemo(
    () => (meta && cut ? cut.coordinates.map((line) => pathOf(meta, line)) : []),
    [meta, cut]
  );
  const routePath = useMemo(() => {
    const geo = route?.route ?? route?.wet_route;
    return meta && geo ? pathOf(meta, geo.coordinates) : null;
  }, [meta, route]);
  const routeWet = !route?.route && route?.wet_route;

  return (
    <section
      className="flex min-h-0 min-w-0 flex-1 flex-col lg:sticky lg:top-0"
      aria-labelledby="map-heading"
    >
      <div className="flex items-baseline gap-3 border-b border-line bg-panel px-4 py-3 sm:px-5">
        <h2 id="map-heading" className="text-[15px] font-semibold text-ink">
          Exposure surface
        </h2>
        <p className="font-mono text-[11.5px] text-muted">HAND · 30 m · rain 250 mm/72 h</p>
        {meta?.available && (
          <label className="ml-auto flex items-center gap-2 text-[11.5px] text-muted">
            <span className="hidden sm:inline">before</span>
            <input
              type="range"
              min="0"
              max="100"
              value={beforeAfter}
              onChange={(e) => setBeforeAfter(Number(e.target.value))}
              aria-label="Slide between plain terrain (before) and the modelled flood exposure (after)"
              className="h-1 w-20 accent-water"
            />
            <span className="hidden sm:inline">after</span>
          </label>
        )}
        {cut && !cut.unavailable && (
          <button
            onClick={() => setShowCut((v) => !v)}
            aria-pressed={showCut}
            className="rounded-sm border border-line px-2 py-1 text-[11.5px] text-muted transition-colors hover:text-ink"
          >
            {showCut ? "Hide" : "Show"} cut roads
          </button>
        )}
      </div>

      {pickMode && (
        <div className="border-b border-line bg-water/10 px-4 py-1.5 text-[12px] text-water sm:px-5">
          Click the map to place this call · Esc to cancel
        </div>
      )}

      <div
        ref={wrapRef}
        onClick={onMapClick}
        className={`relative min-h-[340px] flex-1 overflow-hidden bg-panel lg:min-h-0 ${pickMode ? "cursor-crosshair" : ""}`}
      >
        {meta?.available ? (
          <>
            <img
              src="/surface.png?layer=hand"
              alt="Plain terrain, height above nearest drainage, before any flood model is applied."
              className="absolute inset-0 h-full w-full object-fill"
            />
            <img
              src="/surface.png?layer=exposure"
              alt="Modelled inundation exposure across the Ernakulam area of interest. Low ground along the drainage network is shaded; high ground is left dark."
              className="absolute inset-0 h-full w-full object-fill"
              style={{ clipPath: `inset(0 0 0 ${100 - beforeAfter}%)` }}
            />
            {beforeAfter > 0 && beforeAfter < 100 && (
              <div
                aria-hidden="true"
                className="pointer-events-none absolute inset-y-0 w-px bg-ink/70"
                style={{ left: `${100 - beforeAfter}%` }}
              />
            )}
          </>
        ) : (
          <p className="absolute inset-0 flex items-center justify-center px-6 text-center text-[13px] text-muted">
            No baked surface. Run build_terrain.py, then train_exposure.py.
          </p>
        )}

        <svg
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          className="pointer-events-none absolute inset-0 h-full w-full"
          aria-hidden="true"
        >
          {showCut &&
            cutPaths.map((d, i) => (
              <path
                key={i}
                d={d}
                fill="none"
                stroke="var(--color-band3)"
                strokeWidth="1.6"
                strokeOpacity="0.6"
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
              />
            ))}
          {routePath && (
            <path
              d={routePath}
              fill="none"
              stroke={routeWet ? "var(--color-band3)" : "var(--color-water)"}
              strokeWidth={routeWet ? 2.2 : 2.8}
              strokeDasharray={routeWet ? "5 4" : undefined}
              strokeLinecap="round"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
              style={{
                filter:
                  "drop-shadow(0 0 3px color-mix(in srgb, var(--color-water) 60%, transparent))",
              }}
            />
          )}
        </svg>

        {meta?.available &&
          calls.map((c) => {
            if (c.lat == null || c.lon == null) return null;
            const p = project(meta, c.lat, c.lon);
            if (!p) return null;
            const spanM =
              (meta.east - meta.west) * 111320 * Math.cos((meta.north * Math.PI) / 180);
            const d = ((c.error_radius_m * 2) / spanM) * 100;
            const isSel = selected === c.id;
            return (
              <div key={c.id}>
                {c.error_radius_m > 400 && (
                  <span
                    aria-hidden="true"
                    className="pointer-events-none absolute rounded-full border border-dashed border-band2/50"
                    style={{
                      left: `calc(${p.x}% - ${d / 2}%)`,
                      top: `calc(${p.y}% - ${d / 2}%)`,
                      width: `${d}%`,
                      paddingBottom: `${d}%`,
                    }}
                  />
                )}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelect(c.id);
                  }}
                  aria-label={`Position ${c.rank} on the map, ${bandName(c.band)}. ${c.reason}`}
                  title={`#${c.rank} · ${c.reason}`}
                  className={`absolute grid h-[16px] w-[16px] -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-white/80 font-mono text-[8.5px] font-bold text-white transition-transform hover:scale-150 ${
                    isSel ? "z-10 scale-[1.7] !border-ink" : ""
                  }`}
                  style={{
                    left: `${p.x}%`,
                    top: `${p.y}%`,
                    background:
                      c.band >= 3
                        ? "var(--color-band3)"
                        : c.band === 2
                          ? "var(--color-band2)"
                          : "var(--color-band1)",
                  }}
                >
                  {c.rank}
                </button>
              </div>
            );
          })}

        <div className="absolute bottom-3 left-3 rounded-sm border border-line bg-panel/95 px-3 py-2 font-mono text-[10.5px] leading-relaxed text-muted shadow-sm">
          <p className="tracking-[0.08em]">EXPOSURE INDEX · FITTED TO OBSERVED EXTENT</p>
          <div
            className="my-1 h-[7px] w-[118px] rounded-[1px]"
            style={{
              background:
                "linear-gradient(90deg,var(--color-map-dry),#2C7C90 55%,var(--color-map-wet))",
            }}
            aria-hidden="true"
          />
          <div className="flex justify-between">
            <span>0.0 dry</span>
            <span>1.0 water</span>
          </div>
          {cut && !cut.unavailable && (
            <p className="mt-1.5">
              <span className="text-band3">━</span> {cut.count} road segments
              impassable (≥ {cut.cut})
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
