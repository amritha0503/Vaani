"""The fusion ranker.

Band first, lexicographically. Exposure reorders WITHIN a band and can never
move a call across one. That single property is the whole safety argument:
terrain can promote a call, it can never demote a reported life threat.
"""
import time

VULN_WEIGHT = {"child": 1.0, "elderly": 0.8, "disabled": 0.8,
               "pregnant": 0.8, "injured": 0.6}

W_EXPOSURE, W_VULNERABLE, W_AGE = 0.55, 0.25, 0.20
AGE_SATURATES_MIN = 30.0


def band(fields: dict) -> int:
    """The life-threat floor. Nothing below this line can be demoted by terrain."""
    if (fields.get("trapped") or fields.get("medical_critical")
            or fields.get("hazard") == "drowning" or fields.get("hazard_class") == "drowning"):
        return 3
    return int(fields["severity_band"])


def within_band_score(fields: dict, exp: dict, age_min: float) -> float:
    vuln = min(sum(VULN_WEIGHT.get(v, 0.0) for v in fields["vulnerable"]), 1.0)
    # On a bad fix, fall back to the conservative estimate rather than p75.
    p = exp["p50"] if exp.get("wide_error") else exp["p75"]
    return round(W_EXPOSURE * p
                 + W_VULNERABLE * vuln
                 + W_AGE * min(age_min / AGE_SATURATES_MIN, 1.0), 4)


def reason(fields: dict, exp: dict, floored: bool, live_stage: str | None = None,
           live: bool = False) -> str:
    """Every row on the board states why it sits where it sits."""
    if live_stage == "done_abandoned":
        # More specific than "LOCATING", and it must win over that check: a
        # call marked ended is not still on the line, whatever exp still says.
        return "CALL ENDED — no response reached us, ranked on report only"
    if exp.get("pending"):
        # "on the line" is only true of a call actually in progress. An upload
        # sitting unplaced needs an operator to act, not to wait for an agent.
        return ("LOCATING — on the line, ranked on report only" if live else
                "NO LOCATION — no landmark heard, needs placing")
    if not exp["in_aoi"]:
        return "outside mapped area — ranked on report only"
    hand = exp["hand_m"]
    ground = (f"{hand:.1f} m above nearest drainage" if hand is not None
              else "terrain unknown")
    parts = [ground, f"exposure {exp['p75']:.2f}"]
    if floored:
        parts.insert(0, "LIFE THREAT — floor applied")
    if exp.get("wide_error"):
        parts.append("REQUEST LANDMARK")
    elif exp.get("terrain_edge"):
        parts.append("TERRAIN EDGE — ask which floor")
    return " · ".join(parts)


def rank(calls: list[dict], now: float | None = None) -> list[dict]:
    """calls: dicts with keys id, fields, exposure, received_at, override."""
    now = now or time.time()
    scored = []
    for c in calls:
        age_min = (now - c["received_at"]) / 60.0
        b = band(c["fields"])
        floored = b == 3 and int(c["fields"]["severity_band"]) < 3
        b = max(0, min(3, b + c.get("override", 0)))     # operator wins, always
        s = within_band_score(c["fields"], c["exposure"], age_min)
        scored.append({**c, "band": b, "within": s, "floor_applied": floored,
                       "reason": reason(c["fields"], c["exposure"], floored,
                                        c.get("live_stage"),
                                        live=bool(c.get("live_stage")))})
    scored.sort(key=lambda x: (x["band"], x["within"]), reverse=True)
    for i, c in enumerate(scored, 1):
        c["rank"] = i
    return scored
