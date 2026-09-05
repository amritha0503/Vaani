"""Sentinel-1 SAR -> observed flood mask for the AOI.  python build_labels.py

Radar is the right instrument here: it sees through the cloud that is, by
definition, present during a flood. Open water is specular -- it reflects the
pulse away from the satellite -- so flooded ground goes dark.

Method (change detection, not a single-date threshold):

    flood scene  (Aug 2018, Kerala flood)   gamma0 VV, dB
    dry  scene   (Feb 2018, same orbit)     gamma0 VV, dB
    water  = flood is dark  AND  it got materially darker than the dry baseline

The "same relative orbit" part is not optional. Backscatter depends on incidence
angle, so differencing two passes with different geometry measures the geometry,
not the water.

RTC (Radiometrically Terrain Corrected) rather than raw GRD: already gamma0,
already terrain-flattened, already in UTM 43N -- which is the grid the exposure
stack uses, so no resampling games.
"""
import json
import time
import warnings
from pathlib import Path

import numpy as np
import planetary_computer as pc
import pystac_client
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rasterio.windows import from_bounds

warnings.filterwarnings("ignore")

AOI = (76.399, 10.019, 76.581, 10.201)
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"

# Kerala flooded 15-17 Aug 2018; 21 Aug is the first clean acquisition after the
# peak and still heavily inundated. Feb is deep dry season, same descending pass.
FLOOD_WINDOW = "2018-08-20/2018-08-22"
DRY_WINDOW = "2018-01-15/2018-03-15"

WATER_DB = -16.0        # gamma0 VV below this is candidate open water
DROP_DB = 2.0           # and it must have darkened by at least this much

# 2.0 dB rather than 3.0: this mask is already a LOWER bound on the true
# extent -- the scene is 4-6 days after the peak, and SAR misses flooded
# vegetation and flooded built-up ground entirely -- so the tolerable error
# is one-sided. 3.0 dB leaves 1,802 usable positives, 2.0 dB leaves 2,003,
# and the HAND separation is unchanged across the whole tested grid.

FLOOD_CACHE, DRY_CACHE = "data/vv_flood_db.tif", "data/vv_dry_db.tif"
SCENES_JSON = Path("data/s1_scenes.json")


def pick(cat, window, orbit=None, pass_dir="descending"):
    items = list(cat.search(collections=["sentinel-1-rtc"], bbox=AOI,
                            datetime=window).items())
    items = [i for i in items
             if i.properties.get("sat:orbit_state") == pass_dir
             and (orbit is None or i.properties.get("sat:relative_orbit") == orbit)]
    if not items:
        raise SystemExit(f"no {pass_dir} RTC scene in {window}"
                         + (f" on orbit {orbit}" if orbit else ""))
    return sorted(items, key=lambda i: i.properties["datetime"])[0]


def read_db(item, ref_tf, ref_shape, ref_crs, cache=None, tries=4):
    """Read VV over the AOI and resample onto the exposure grid, in dB.

    Cached to disk on first fetch. Remote COG tile reads fail intermittently and
    nothing downstream should depend on the network being up twice."""
    if cache and Path(cache).exists():
        with rasterio.open(cache) as c:
            return c.read(1)
    last = None
    for attempt in range(tries):
        try:
            return _fetch_db(item, ref_tf, ref_shape, ref_crs, cache)
        except Exception as e:
            last = e
            print(f"    read failed ({type(e).__name__}), retry {attempt+1}/{tries}")
            time.sleep(3 * (attempt + 1))
    raise last


def _fetch_db(item, ref_tf, ref_shape, ref_crs, cache=None):
    href = pc.sign(item).assets["vv"].href
    with rasterio.open(href) as src:
        w = from_bounds(*rasterio.warp.transform_bounds("EPSG:4326", src.crs, *AOI),
                        transform=src.transform)
        arr = src.read(1, window=w, boundless=True, fill_value=np.nan).astype("float32")
        src_tf = src.window_transform(w)
        src_crs = src.crs
    out = np.full(ref_shape, np.nan, "float32")
    reproject(arr, out, src_transform=src_tf, src_crs=src_crs,
              dst_transform=ref_tf, dst_crs=ref_crs, resampling=Resampling.bilinear,
              src_nodata=np.nan, dst_nodata=np.nan)
    out[out <= 0] = np.nan                      # gamma0 is a power ratio
    db = (10.0 * np.log10(out)).astype("float32")
    if cache:
        with rasterio.open(cache, "w", driver="GTiff", height=ref_shape[0],
                           width=ref_shape[1], count=1, dtype="float32",
                           crs=ref_crs, transform=ref_tf, compress="deflate",
                           nodata=np.nan) as dst:
            dst.write(db, 1)
    return db


if __name__ == "__main__":
    with rasterio.open("data/hand.tif") as ref:
        ref_tf, ref_shape, ref_crs = ref.transform, ref.shape, ref.crs

    # Re-thresholding cached scenes must not need the network. Once the two VV
    # windows and the scene ids are on disk this script runs fully offline.
    offline = (SCENES_JSON.exists() and Path(FLOOD_CACHE).exists()
               and Path(DRY_CACHE).exists())
    if offline:
        meta = json.loads(SCENES_JSON.read_text())
        flood = dry = None
        print("scene metadata from data/s1_scenes.json (no network)")
    else:
        cat = pystac_client.Client.open(STAC)
        flood = pick(cat, FLOOD_WINDOW)
        orbit = flood.properties.get("sat:relative_orbit")
        dry = pick(cat, DRY_WINDOW, orbit=orbit)
        meta = {"flood_scene": flood.id, "dry_scene": dry.id,
                "relative_orbit": orbit,
                "flood_datetime": flood.properties["datetime"],
                "dry_datetime": dry.properties["datetime"],
                "dry_orbit": dry.properties.get("sat:relative_orbit")}
        SCENES_JSON.write_text(json.dumps(meta, indent=2))

    orbit = meta["relative_orbit"]
    print(f"flood scene : {meta['flood_scene']}")
    print(f"              {meta['flood_datetime'][:16]}  orbit {orbit}")
    print(f"dry scene   : {meta['dry_scene']}")
    print(f"              {meta['dry_datetime'][:16]}  orbit {meta['dry_orbit']}")
    if meta["dry_orbit"] != orbit:
        print("  WARNING: orbits differ - the difference measures geometry too")

    print("\nreading VV windows onto the 30 m exposure grid...")
    f_db = read_db(flood, ref_tf, ref_shape, ref_crs, FLOOD_CACHE)
    d_db = read_db(dry, ref_tf, ref_shape, ref_crs, DRY_CACHE)

    valid = np.isfinite(f_db) & np.isfinite(d_db)
    drop = d_db - f_db
    water = valid & (f_db < WATER_DB) & (drop > DROP_DB)
    permanent = valid & (f_db < WATER_DB) & (d_db < WATER_DB)   # river year-round

    print(f"\n  valid cells      {valid.sum():>8,}")
    print(f"  flood VV dB      p05 {np.nanpercentile(f_db[valid],5):6.1f}  "
          f"p50 {np.nanpercentile(f_db[valid],50):6.1f}")
    print(f"  dry   VV dB      p05 {np.nanpercentile(d_db[valid],5):6.1f}  "
          f"p50 {np.nanpercentile(d_db[valid],50):6.1f}")
    print(f"  NEW water        {water.sum():>8,}  ({100*water.sum()/max(valid.sum(),1):.2f}% of AOI)")
    print(f"  permanent water  {permanent.sum():>8,}")

    prof = dict(driver="GTiff", height=ref_shape[0], width=ref_shape[1], count=1,
                dtype="uint8", crs=ref_crs, transform=ref_tf, compress="deflate",
                nodata=255)
    lab = np.where(valid, water.astype("uint8"), 255).astype("uint8")
    with rasterio.open("data/flood_mask.tif", "w", **prof) as dst:
        dst.write(lab, 1)
        dst.update_tags(source="Sentinel-1 RTC gamma0 VV change detection",
                        flood_scene=meta["flood_scene"], dry_scene=meta["dry_scene"],
                        relative_orbit=str(orbit),
                        water_db=str(WATER_DB), drop_db=str(DROP_DB),
                        note="1=inundated 0=dry 255=no data")
    # Permanent water is neither a flood example nor a dry example: the river
    # was already there in January. Training on it teaches river-finding.
    with rasterio.open("data/permanent_water.tif", "w", **prof) as dst:
        dst.write(np.where(valid, permanent.astype("uint8"), 255).astype("uint8"), 1)
        dst.update_tags(source="open water in BOTH scenes",
                        water_db=str(WATER_DB),
                        note="1=permanent water 0=not 255=no data",
                        why="excluded from BOTH classes in train_exposure.py")

    print("\nwrote data/flood_mask.tif + data/permanent_water.tif")
