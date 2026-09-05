"""Bake the exposure surface.  python build_terrain.py

DEM -> depression fill -> D8 -> flow accumulation -> stream network -> HAND
    -> slope -> exposure probability -> two GeoTIFFs the API reads at startup.

Runs once. The API never executes any of this; it reads the baked rasters.
HAND is implemented here in numpy so the build has no binary dependency.
"""
import heapq
import json
import time

import numpy as np
import rasterio
from rasterio.warp import Resampling, calculate_default_transform, reproject

# ---- configuration ----------------------------------------------------
AOI = (76.40, 10.02, 76.58, 10.20)      # Ernakulam: Aluva - Perumbavoor - Periyar
DST_CRS = "EPSG:32643"                  # UTM 43N, metric
RES_M = 30.0
STREAM_CELLS = 800                      # accumulation threshold -> drainage network
RAIN_72H_MM = 250.0                     # the replayed event

# Exposure coefficients. EXPERT-SET, not fitted. This is the BOOTSTRAP surface:
# it exists so the board is demoable before any label is on disk, and so that
# train_exposure.py has a baseline to beat on the same held-out blocks.
# train_exposure.py OVERWRITES data/exposure.tif with the fitted surface --
# rerun it after every rerun of this script, or you ship the expert set by
# accident. The board reads model_ver off the raster, so it would show.
# z = B0 + B_HAND*hand + B_SLOPE*slope + B_RAIN*(rain/ref - 1)
B0, B_HAND, B_SLOPE, B_RAIN = 2.20, -0.44, -0.030, 0.90
MODEL_VER = "hand-logistic-v0-expertset"

NEIGH = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def log(msg, t0=[time.time()]):
    print(f"[{time.time() - t0[0]:6.1f}s] {msg}", flush=True)


# ---- 1. clip + reproject ----------------------------------------------
def load_dem(path):
    with rasterio.open(path) as src:
        w = rasterio.windows.from_bounds(*AOI, transform=src.transform)
        dem = src.read(1, window=w).astype("float32")
        src_tf = src.window_transform(w)
        tf, wid, hei = calculate_default_transform(
            src.crs, DST_CRS, dem.shape[1], dem.shape[0],
            *rasterio.windows.bounds(w, src.transform), resolution=RES_M)
        out = np.empty((hei, wid), "float32")
        reproject(dem, out, src_transform=src_tf, src_crs=src.crs,
                  dst_transform=tf, dst_crs=DST_CRS, resampling=Resampling.bilinear)
    return out, tf


# ---- 2. priority-flood depression fill --------------------------------
def fill_depressions(dem, eps=1e-4):
    """Priority flood WITH an epsilon gradient.

    A plain fill leaves filled depressions perfectly flat, D8 then finds no
    downhill neighbour inside them, and flow accumulation dies -- which is
    exactly what an empty stream network looks like. Raising each cell a
    hair above its predecessor guarantees a downslope path to the outlet.
    """
    h, w = dem.shape
    filled = np.full(dem.shape, np.inf, "float64")
    seen = np.zeros(dem.shape, bool)
    pq = []
    for r in range(h):
        for c in (0, w - 1):
            heapq.heappush(pq, (float(dem[r, c]), r, c)); seen[r, c] = True
            filled[r, c] = dem[r, c]
    for c in range(w):
        for r in (0, h - 1):
            if not seen[r, c]:
                heapq.heappush(pq, (float(dem[r, c]), r, c)); seen[r, c] = True
                filled[r, c] = dem[r, c]
    while pq:
        e, r, c = heapq.heappop(pq)
        for dr, dc in NEIGH:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w and not seen[rr, cc]:
                seen[rr, cc] = True
                ne = max(float(dem[rr, cc]), e + eps)
                filled[rr, cc] = ne
                heapq.heappush(pq, (ne, rr, cc))
    return filled                                   # float64: eps needs the precision


# ---- 3. D8 flow direction ---------------------------------------------
def d8(filled, cell=RES_M):
    h, w = filled.shape
    pad = np.pad(filled, 1, constant_values=np.inf)
    best_drop = np.zeros((h, w), "float64")
    down = np.full(h * w, -1, "int64")
    idx = np.arange(h * w).reshape(h, w)
    for dr, dc in NEIGH:
        nb = pad[1 + dr:1 + dr + h, 1 + dc:1 + dc + w]
        dist = cell * (2 ** 0.5 if dr and dc else 1.0)
        drop = (filled - nb) / dist
        better = drop > best_drop
        best_drop = np.where(better, drop, best_drop)
        nb_idx = np.roll(np.roll(idx, -dr, axis=0), -dc, axis=1)
        down = np.where(better.ravel(), nb_idx.ravel(), down)
    edge = np.ones((h, w), bool)
    edge[1:-1, 1:-1] = False                 # boundary cells drain out of the AOI
    down[edge.ravel()] = -1
    return down


# ---- 4. flow accumulation --------------------------------------------
def accumulate(filled, down):
    n = filled.size
    acc = np.ones(n, "float64")
    for i in np.argsort(filled.ravel())[::-1]:      # high ground first
        d = down[i]
        if d >= 0:
            acc[d] += acc[i]
    return acc.reshape(filled.shape)


# ---- 5. HAND ----------------------------------------------------------
def hand(filled, down, streams):
    """Height above the elevation of the first stream cell reached downstream.
    Processing low ground first guarantees each cell's target is already known."""
    flat, strm = filled.ravel(), streams.ravel()
    ref = np.full(filled.size, np.nan, "float64")
    for i in np.argsort(flat):                      # low ground first
        if strm[i]:
            ref[i] = flat[i]
        else:
            d = down[i]
            if d >= 0:
                ref[i] = ref[d]
    return np.maximum(flat - ref, 0).reshape(filled.shape).astype("float32")


# ---- 6. exposure ------------------------------------------------------
def exposure(hand_m, slope_deg, rain_mm=RAIN_72H_MM):
    z = (B0 + B_HAND * np.nan_to_num(hand_m, nan=50.0)
         + B_SLOPE * slope_deg + B_RAIN * (rain_mm / 250.0 - 1.0))
    return (1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))).astype("float32")


def write(path, arr, tf, **tags):
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0],
                       width=arr.shape[1], count=1, dtype="float32",
                       crs=DST_CRS, transform=tf, compress="deflate",
                       nodata=np.nan) as dst:
        dst.write(arr.astype("float32"), 1)
        dst.update_tags(**{k: str(v) for k, v in tags.items()})


if __name__ == "__main__":
    log("clip + reproject")
    dem, tf = load_dem("data/dem_raw.tif")
    log(f"grid {dem.shape[1]}x{dem.shape[0]} @ {RES_M:.0f} m  "
        f"elev {dem.min():.1f}-{dem.max():.1f} m")

    log("fill depressions (priority flood)")
    filled = fill_depressions(dem)
    log(f"raised {(filled > dem + 0.01).sum():,} cells")
    log(f"flats resolved: every non-edge cell has a downslope neighbour")

    log("D8 flow direction")
    down = d8(filled)

    log("flow accumulation")
    acc = accumulate(filled, down)

    streams = acc >= STREAM_CELLS
    log(f"stream network: {streams.sum():,} cells "
        f"({100 * streams.mean():.2f}% of grid)")

    log("HAND")
    h = hand(filled, down, streams)

    gy, gx = np.gradient(filled, RES_M)
    slope = np.degrees(np.arctan(np.hypot(gx, gy))).astype("float32")

    log("exposure")
    exp = exposure(h, slope)

    write("data/hand.tif", h, tf, layer="HAND", units="metres",
          stream_threshold_cells=STREAM_CELLS)

    # covariate stack for training -- same grid, same order as FEATURES below
    twi = np.log((acc * RES_M * RES_M) / (np.tan(np.radians(slope)) + 0.001))
    dist = np.full_like(h, np.nan)
    try:
        from scipy.ndimage import distance_transform_edt
        dist = (distance_transform_edt(~streams) * RES_M).astype("float32")
    except ImportError:
        print("  (scipy missing: distance-to-drainage band left as NaN)")
    stack = np.stack([h, slope, twi.astype("float32"), dist,
                      filled.astype("float32")])
    with rasterio.open("data/covariates.tif", "w", driver="GTiff",
                       height=h.shape[0], width=h.shape[1], count=5,
                       dtype="float32", crs=DST_CRS, transform=tf,
                       compress="deflate", nodata=np.nan) as dst:
        dst.write(stack)
        for i, n in enumerate(["hand_m", "slope_deg", "twi", "dist_drain_m",
                               "elev_m"], 1):
            dst.set_band_description(i, n)
    with rasterio.open("data/streams.tif", "w", driver="GTiff",
                       height=h.shape[0], width=h.shape[1], count=1,
                       dtype="uint8", crs=DST_CRS, transform=tf,
                       compress="deflate") as dst:
        dst.write(streams.astype("uint8"), 1)
    write("data/exposure.tif", exp, tf, layer="inundation_probability",
          model_ver=MODEL_VER, rain_72h_mm=RAIN_72H_MM,
          coefficients=json.dumps({"b0": B0, "hand": B_HAND,
                                   "slope": B_SLOPE, "rain": B_RAIN}),
          provenance="expert-set coefficients; NOT fitted to observed extents")

    log("done")
    print("  data/exposure.tif here is the EXPERT-SET bootstrap surface.")
    print("  Run build_labels.py then train_exposure.py to replace it.")
    print(f"\n  HAND      p05 {np.nanpercentile(h, 5):6.1f} m   "
          f"p50 {np.nanpercentile(h, 50):6.1f} m   p95 {np.nanpercentile(h, 95):6.1f} m")
    print(f"  slope     p50 {np.nanpercentile(slope, 50):6.1f} deg")
    print(f"  exposure  p05 {np.nanpercentile(exp, 5):6.2f}     "
          f"p50 {np.nanpercentile(exp, 50):6.2f}     p95 {np.nanpercentile(exp, 95):6.2f}")
