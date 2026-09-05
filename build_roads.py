"""Road graph for the AOI, weighted by the exposure surface.  python build_roads.py

The dispatch claim is "one surface, two jobs": the raster that ranked the caller
also prices the road. That only means anything if the weights come off the same
GeoTIFF, so this script bakes exposure straight onto the edges.

    Overpass  -> ways + nodes, cached to data/osm_roads.json
              -> split at junctions into edges that carry their own polyline
              -> sample exposure.tif and hand.tif ALONG each edge
              -> data/roadgraph.json, which the API loads once at startup

Sampling along the edge, not at its midpoint, is the whole point: a road is
impassable if it dips through a flooded low spot anywhere on its length, and a
midpoint sample is exactly the test that misses that.

Fetched once and cached. Nothing here runs during a demo, and dispatch.py never
touches the network -- routing has to survive the cable pull like everything else.
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer

AOI = (76.399, 10.019, 76.581, 10.201)          # west, south, east, north
OVERPASS = "https://overpass-api.de/api/interpreter"
CACHE = Path("data/osm_roads.json")
OUT = Path("data/roadgraph.json")

# What a rescue vehicle can actually use. No paths, no tracks, no steps.
DRIVABLE = ("motorway|trunk|primary|secondary|tertiary|unclassified|residential|"
            "living_street|service|road")
STEP_M = 30.0                                   # sample interval along an edge = one cell

QUERY = f"""[out:json][timeout:300];
way["highway"~"^({DRIVABLE})(_link)?$"]({AOI[1]},{AOI[0]},{AOI[3]},{AOI[2]});
out body;
>;
out skel qt;
nwr["amenity"~"^(fire_station|police|hospital)$"]({AOI[1]},{AOI[0]},{AOI[3]},{AOI[2]});
out center;
"""


def log(msg, t0=[time.time()]):
    print(f"[{time.time() - t0[0]:6.1f}s] {msg}", flush=True)


def fetch(tries=3):
    if CACHE.exists():
        log(f"using cached {CACHE} ({CACHE.stat().st_size / 1e6:.1f} MB)")
        return json.loads(CACHE.read_text(encoding="utf-8"))
    body = urllib.parse.urlencode({"data": QUERY}).encode()
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                OVERPASS, body, {"User-Agent": "vaani-hackathon/1.0"})
            with urllib.request.urlopen(req, timeout=300) as r:
                raw = r.read()
            CACHE.write_bytes(raw)
            log(f"fetched {len(raw) / 1e6:.1f} MB from Overpass")
            return json.loads(raw)
        except Exception as e:                  # Overpass rate-limits; back off
            last = e
            log(f"  overpass failed ({type(e).__name__}), retry {attempt + 1}/{tries}")
            time.sleep(10 * (attempt + 1))
    raise SystemExit(f"could not reach Overpass: {last}")


class Sampler:
    """exposure and HAND along a polyline, read off the baked rasters."""

    def __init__(self, exposure_tif, hand_tif):
        with rasterio.open(exposure_tif) as src:
            self.exp = src.read(1)
            self.tf, self.crs = src.transform, src.crs
            self.h, self.w = src.shape
        with rasterio.open(hand_tif) as src:
            self.hand = src.read(1)
        self._fwd = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)

    def along(self, pts):
        """pts: [(lat, lon), ...] -> (exp_max, exp_p90, hand_min, length_m)"""
        lat = np.array([p[0] for p in pts])
        lon = np.array([p[1] for p in pts])
        x, y = self._fwd.transform(lon, lat)
        seg = np.hypot(np.diff(x), np.diff(y))
        length = float(seg.sum())
        # densify: one sample per cell, so a short dip cannot hide between vertices
        xs, ys = [x[0]], [y[0]]
        for i, d in enumerate(seg):
            n = max(1, int(d // STEP_M))
            t = np.linspace(0, 1, n + 1)[1:]
            xs.extend(x[i] + t * (x[i + 1] - x[i]))
            ys.extend(y[i] + t * (y[i + 1] - y[i]))
        col, row = ~self.tf * (np.array(xs), np.array(ys))
        row = np.clip(np.round(row).astype(int), 0, self.h - 1)
        col = np.clip(np.round(col).astype(int), 0, self.w - 1)
        e = self.exp[row, col]
        hd = self.hand[row, col]
        e = e[np.isfinite(e)]
        hd = hd[np.isfinite(hd)]
        return (float(e.max()) if e.size else 0.0,
                float(np.percentile(e, 90)) if e.size else 0.0,
                float(hd.min()) if hd.size else None,
                length)


if __name__ == "__main__":
    raw = fetch()
    els = raw["elements"]
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in els
             if e["type"] == "node" and "lat" in e}
    ways = [e for e in els if e["type"] == "way" and e.get("nodes")
            and "highway" in e.get("tags", {})]
    log(f"{len(ways):,} ways, {len(nodes):,} nodes")

    # A node shared by two ways is a junction; so is either end of a way. Edges
    # run junction to junction and carry every shape point in between, which
    # keeps the graph small without losing the geometry the map draws.
    seen, junction = set(), set()
    for w in ways:
        ns = w["nodes"]
        junction.add(ns[0]); junction.add(ns[-1])
        for n in ns:
            if n in seen:
                junction.add(n)
            seen.add(n)
    log(f"{len(junction):,} junction nodes")

    smp = Sampler("data/exposure.tif", "data/hand.tif")
    edges, used = [], set()
    for w in ways:
        ns = [n for n in w["nodes"] if n in nodes]
        if len(ns) < 2:
            continue
        cls = w["tags"]["highway"]
        name = w["tags"].get("name", "")
        run = [ns[0]]
        for n in ns[1:]:
            run.append(n)
            if n in junction or n == ns[-1]:
                if len(run) >= 2 and run[0] != run[-1]:
                    pts = [nodes[i] for i in run]
                    exp_max, exp_p90, hand_min, length = smp.along(pts)
                    edges.append({
                        "u": run[0], "v": run[-1],
                        "len_m": round(length, 1),
                        "exp_max": round(exp_max, 3), "exp_p90": round(exp_p90, 3),
                        "hand_min_m": None if hand_min is None else round(hand_min, 1),
                        "hw": cls, "name": name,
                        "pts": [[round(a, 6), round(b, 6)] for a, b in pts],
                    })
                    used.update((run[0], run[-1]))
                run = [n]
    log(f"{len(edges):,} edges between {len(used):,} routable nodes")

    depots = []
    for e in els:
        t = e.get("tags", {})
        a = t.get("amenity")
        if a not in ("fire_station", "police", "hospital"):
            continue
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None:
            continue
        depots.append({"name": t.get("name") or a.replace("_", " ").title(),
                       "kind": a, "lat": float(lat), "lon": float(lon)})
    # A rescue base first, a police station next, a hospital only as a fallback.
    order = {"fire_station": 0, "police": 1, "hospital": 2}
    depots.sort(key=lambda d: order[d["kind"]])
    log(f"{len(depots)} candidate depots "
        f"({sum(d['kind'] == 'fire_station' for d in depots)} fire stations)")

    with rasterio.open("data/exposure.tif") as src:
        model_ver = src.tags().get("model_ver", "unversioned")

    OUT.write_text(json.dumps({
        "meta": {"aoi": AOI, "model_ver": model_ver, "step_m": STEP_M,
                 "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "note": "exposure sampled along each edge from data/exposure.tif"},
        "nodes": {str(n): [round(nodes[n][0], 6), round(nodes[n][1], 6)] for n in used},
        "edges": edges,
        "depots": depots[:12],
    }, separators=(",", ":")), encoding="utf-8")
    log(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")

    e = np.array([x["exp_max"] for x in edges])
    for cut in (0.4, 0.5, 0.6, 0.7):
        print(f"  exposure cut {cut:.1f}: {100 * (e >= cut).mean():5.1f}% of edges "
              f"({(e >= cut).sum():,}) would be impassable")
