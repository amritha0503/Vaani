"""Fit the exposure function to observed flood extent.  python train_exposure.py

Replaces the expert-set coefficients baked by build_terrain.py with a model
fitted against SAR-observed inundation, and overwrites data/exposure.tif.

    build_terrain.py   DEM  -> HAND, slope, TWI, distance-to-drainage
    build_labels.py    S1   -> observed flood mask (and permanent water)
    train_exposure.py  both -> a calibrated exposure surface + honest numbers

Four decisions here are the whole defence of the number, and each is the
difference between a real score and a beautiful, meaningless AUC:

  1. Permanent water is dropped from BOTH classes. The Periyar was already
     there in January. A model trained with the river as a positive learns to
     find rivers, which a river map already does.
  2. The sample is spatially THINNED to one cell per 300 m per class. Adjacent
     30 m cells share terrain and share a speckle realisation; they are not
     independent evidence, and without thinning every metric is inflated.
  3. Validation holds out whole 2 km BLOCKS, never random rows. A random split
     puts a cell's own neighbours in the training set and scores memory as if
     it were skill. Only the blocked number goes on a slide.
  4. Probabilities are ISOTONICALLY CALIBRATED on out-of-fold predictions and
     scored with Brier as well as AUC. The ranker sorts on this number, so
     ordering correctly is not enough -- it has to mean what it says.

What the number means. This is a presence/absence design with equal class
weight, standard practice for susceptibility mapping: read it as "how exposed
is this ground, relative to the rest of the AOI, in an event of this size" --
not "the chance this address floods tonight". The natural prevalence and the
prior shift that converts between the two are printed at the end and stored in
the raster tags.

Elevation is deliberately NOT a feature. It is the strongest single predictor
inside one valley and worthless outside it -- the model would memorise the
altitude of the Periyar floodplain, and the claim we make on stage is that
these features are computable anywhere.
"""
import json
import time

import joblib
import lightgbm as lgb
import numpy as np
import rasterio
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold

from build_terrain import B_RAIN, DST_CRS, RAIN_72H_MM
from build_terrain import exposure as expert_exposure

FEATURES = ["hand_m", "slope_deg", "twi", "dist_drain_m"]
MONOTONE = [-1, -1, +1, -1]      # deeper, flatter, wetter, closer = more exposed
THIN_M = 300.0                   # minimum separation between training points
BLOCK_M = 2000.0                 # spatial CV block edge
FOLDS = 5
SEED = 20180821                  # the flood scene date, so the run repeats
NEG_PER_POS = 1.0
HAND_NODATA_FILL = 50.0          # cells reaching no stream: treat as high ground
MODEL_VER = "lgbm-s1-2018kerala-v1"

PARAMS = dict(n_estimators=300, learning_rate=0.05, num_leaves=15,
              min_child_samples=30, subsample=0.9, subsample_freq=1,
              colsample_bytree=0.9, reg_lambda=1.0, monotone_constraints=MONOTONE,
              random_state=SEED, verbosity=-1)


def log(msg, t0=[time.time()]):
    print(f"[{time.time() - t0[0]:5.1f}s] {msg}", flush=True)


def thin(mask, block_px, rng):
    """One cell per block, chosen at random. Returns flat indices.

    Thinning costs rows and buys the right to quote the metrics."""
    idx = np.flatnonzero(mask.ravel())
    if idx.size == 0:
        return idx
    w = mask.shape[1]
    nbx = int(np.ceil(w / block_px))
    block = (idx // w // block_px) * nbx + (idx % w // block_px)
    order = rng.permutation(idx.size)
    _, first = np.unique(block[order], return_index=True)
    return idx[order[first]]


def block_ids(idx, width, block_px):
    nbx = int(np.ceil(width / block_px))
    return (idx // width // block_px) * nbx + (idx % width // block_px)


def score(y, p):
    return {"n": int(y.size), "positives": int(y.sum()),
            "auc": float(roc_auc_score(y, p)),
            "brier": float(brier_score_loss(y, p))}


if __name__ == "__main__":
    log("load rasters")
    with rasterio.open("data/covariates.tif") as src:
        cov = src.read()
        names = list(src.descriptions)
        tf, shape, px = src.transform, src.shape, abs(src.transform.a)
    with rasterio.open("data/flood_mask.tif") as src:
        lab = src.read(1)
        lab_tags = src.tags()
    with rasterio.open("data/permanent_water.tif") as src:
        perm = src.read(1) == 1

    bands = {n: cov[i] for i, n in enumerate(names)}
    X_full = np.stack([bands[f].ravel() for f in FEATURES], 1).astype("float64")
    no_hand = ~np.isfinite(X_full[:, 0])
    X_full[no_hand, 0] = HAND_NODATA_FILL

    valid = (lab != 255) & np.isfinite(bands["hand_m"]) & ~perm
    pos = valid & (lab == 1)
    neg = valid & (lab == 0)
    prevalence = float(pos.sum()) / float(pos.sum() + neg.sum())
    log(f"labels: {pos.sum():,} inundated / {neg.sum():,} dry  "
        f"(dropped {perm.sum():,} permanent-water and {no_hand.sum():,} no-HAND cells)")
    log(f"natural prevalence {100 * prevalence:.2f}% of the AOI")

    log(f"spatial thinning to one point per {THIN_M:.0f} m per class")
    rng = np.random.default_rng(SEED)
    thin_px = max(1, int(round(THIN_M / px)))
    ip, ineg = thin(pos, thin_px, rng), thin(neg, thin_px, rng)
    keep = min(ineg.size, int(round(NEG_PER_POS * ip.size)))
    ineg = rng.choice(ineg, size=keep, replace=False)
    idx = np.concatenate([ip, ineg])
    y = np.concatenate([np.ones(ip.size, "int8"), np.zeros(ineg.size, "int8")])
    X = X_full[idx]
    log(f"training points: {ip.size:,} positive + {ineg.size:,} negative "
        f"(thinned from {pos.sum():,} / {neg.sum():,})")

    groups = block_ids(idx, shape[1], max(1, int(round(BLOCK_M / px))))
    log(f"spatial CV: {len(np.unique(groups))} blocks of {BLOCK_M / 1000:.0f} km, "
        f"{FOLDS} folds, whole blocks held out")

    # ---- blocked cross-validation ------------------------------------
    oof = np.full(y.size, np.nan)
    fold_of = np.full(y.size, -1)
    fold_auc = []
    for k, (tr, te) in enumerate(GroupKFold(n_splits=FOLDS).split(X, y, groups)):
        m = lgb.LGBMClassifier(**PARAMS).fit(X[tr], y[tr])
        oof[te] = m.predict_proba(X[te])[:, 1]
        fold_of[te] = k
        a = roc_auc_score(y[te], oof[te]) if len(np.unique(y[te])) > 1 else float("nan")
        fold_auc.append(float(a))
        log(f"  fold {k}: train {tr.size:,}  held out {te.size:,} "
            f"({int(y[te].sum())} positive)  AUC {a:.3f}")

    # Calibrate out of fold as well: isotonic fitted on the OTHER folds, so the
    # calibrated Brier is not scored on the mapping's own training data.
    oof_cal = np.empty_like(oof)
    for k in range(FOLDS):
        te = fold_of == k
        iso_k = IsotonicRegression(out_of_bounds="clip").fit(oof[~te], y[~te])
        oof_cal[te] = iso_k.predict(oof[te])

    raw, cal = score(y, oof), score(y, oof_cal)

    # ---- the baseline it has to beat ---------------------------------
    # The expert-set logistic currently baked into exposure.tif, scored on the
    # same points. If the fit does not beat this, keep the expert set and say so.
    base = score(y, expert_exposure(X[:, 0], X[:, 1]).astype("float64"))
    hand_only = score(y, 1.0 / (1.0 + X[:, 0]))

    # ---- final model + calibration -----------------------------------
    log("fit final model on all training points")
    model = lgb.LGBMClassifier(**PARAMS).fit(X, y)
    iso = IsotonicRegression(out_of_bounds="clip").fit(oof, y)

    gain = model.booster_.feature_importance("gain")
    importance = {f: round(float(g / max(gain.sum(), 1e-9)), 3)
                  for f, g in zip(FEATURES, gain)}

    log("predict across the AOI")
    p = np.clip(iso.predict(model.predict_proba(X_full)[:, 1]), 1e-4, 1 - 1e-4)
    # The rainfall knob stays where it was -- a documented scenario shift in
    # logit space, expert-set, because one event gives rainfall no variance to
    # fit on. At the baked 250 mm it is exactly zero.
    z = np.log(p / (1 - p)) + B_RAIN * (RAIN_72H_MM / 250.0 - 1.0)
    exp = (1.0 / (1.0 + np.exp(-z))).reshape(shape).astype("float32")

    metrics = {
        "model_ver": MODEL_VER,
        "features": FEATURES,
        "monotone_constraints": dict(zip(FEATURES, MONOTONE)),
        "design": f"presence/absence 1:{NEG_PER_POS:g}, thinned to {THIN_M:.0f} m",
        "n_train": int(y.size), "n_positive": int(y.sum()),
        "natural_prevalence": round(prevalence, 5),
        "prior_shift_to_event_probability":
            round(float(np.log(prevalence / (1 - prevalence))), 3),
        "cv": f"GroupKFold {FOLDS} x {BLOCK_M / 1000:.0f} km blocks",
        "blocks": int(len(np.unique(groups))),
        "fold_auc": [round(a, 3) for a in fold_auc],
        "blocked_auc": round(cal["auc"], 3),
        "blocked_brier": round(cal["brier"], 4),
        "blocked_brier_uncalibrated": round(raw["brier"], 4),
        "baseline_expertset": {"auc": round(base["auc"], 3),
                               "brier": round(base["brier"], 4)},
        "baseline_hand_only_auc": round(hand_only["auc"], 3),
        "feature_importance_gain": importance,
        "labels": {k: lab_tags.get(k) for k in
                   ("flood_scene", "dry_scene", "relative_orbit",
                    "water_db", "drop_db")},
        "rain_72h_mm": RAIN_72H_MM,
        "seed": SEED,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    with rasterio.open("data/exposure.tif", "w", driver="GTiff",
                       height=shape[0], width=shape[1], count=1, dtype="float32",
                       crs=DST_CRS, transform=tf, compress="deflate",
                       nodata=np.nan) as dst:
        dst.write(exp, 1)
        dst.update_tags(
            layer="inundation_exposure_index",
            model_ver=MODEL_VER,
            features=",".join(FEATURES),
            design=metrics["design"],
            validation=metrics["cv"],
            blocked_auc=str(metrics["blocked_auc"]),
            blocked_brier=str(metrics["blocked_brier"]),
            natural_prevalence=str(metrics["natural_prevalence"]),
            rain_72h_mm=str(RAIN_72H_MM),
            flood_scene=lab_tags.get("flood_scene", "?"),
            dry_scene=lab_tags.get("dry_scene", "?"),
            provenance="fitted to Sentinel-1 observed extent, 21 Aug 2018 Kerala; "
                       "balanced presence/absence design -- a susceptibility index, "
                       "not an absolute event probability; rainfall term expert-set")
    joblib.dump({"model": model, "isotonic": iso, "features": FEATURES,
                 "hand_nodata_fill": HAND_NODATA_FILL, "metrics": metrics},
                "data/exposure_model.joblib")
    with open("data/exposure_model.json", "w") as fh:
        json.dump(metrics, fh, indent=2)

    # ---- the report you read out -------------------------------------
    log("done")
    print()
    print(f"  model            {MODEL_VER}")
    print(f"  design           {metrics['design']}, "
          f"{y.size:,} points in {metrics['blocks']} blocks")
    print(f"  held-out blocks  AUC {cal['auc']:.3f}   Brier {cal['brier']:.4f}"
          f"   (uncalibrated Brier {raw['brier']:.4f})")
    print("  per-fold AUC     " + "  ".join(f"{a:.3f}" for a in fold_auc))
    print(f"  expert-set       AUC {base['auc']:.3f}   Brier {base['brier']:.4f}"
          "   <- what this replaces")
    print(f"  HAND alone       AUC {hand_only['auc']:.3f}")
    print("  gain             " +
          "  ".join(f"{f} {v:.2f}" for f, v in importance.items()))
    print()
    print(f"  exposure  p05 {np.nanpercentile(exp, 5):.2f}   "
          f"p50 {np.nanpercentile(exp, 50):.2f}   "
          f"p95 {np.nanpercentile(exp, 95):.2f}")
    h = bands["hand_m"]
    for lo, hi in [(0, 0.5), (0.5, 2), (2, 5), (5, 10), (10, 20), (20, 1e9)]:
        m = np.isfinite(h) & (h >= lo) & (h < hi)
        if m.sum():
            print(f"    HAND {lo:>4.1f} - {min(hi, 999):>5.1f} m   n {m.sum():>7,}"
                  f"   exposure p50 {np.median(exp[m]):.2f}")
    print()
    print("  Read it honestly: a relative exposure index from a balanced design.")
    print(f"  Natural prevalence in the AOI is {100 * prevalence:.2f}%; to read it as")
    print("  the probability of inundation in an event of this size, shift the "
          f"logit by {metrics['prior_shift_to_event_probability']:+.2f}.")
    print("  The SAR mask is a lower bound -- post-peak scene, and SAR misses "
          "flooded")
    print("  vegetation and built-up ground -- so this surface under-predicts.")
