"""Spoken landmark -> coordinates, offline.

A normal phone call carries no GPS. Real control rooms solve this by asking, so
Vaani asks too -- and then has to turn "near the Malayattoor church" into a point
with an honest error radius.

Geocoding against Nominatim would need the internet, which is the one thing a
flood takes away. So the AOI's named places are extracted from OSM once and
matched locally: 393 landmarks in RAM, fuzzy-matched, no network.

The error radius is the point. A landmark fix is worth a few hundred metres, not
ten, and exposure.Surface already samples across that disc rather than pretending
to a precision it does not have.
"""
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

# How precisely each kind of landmark pins a caller. A village name covers a lot
# of ground; a named bridge is a point you can drive to.
RADIUS_M = {
    "bridge": 120, "railway": 200, "hospital": 250, "clinic": 250,
    "police": 250, "school": 300, "college": 300, "university": 350,
    "place_of_worship": 300, "marketplace": 350, "bus_station": 300,
    "townhall": 350, "suburb": 700, "neighbourhood": 700,
    "hamlet": 900, "village": 1200, "town": 2000,
}
DEFAULT_RADIUS_M = 800

# Words people say around a landmark that carry no identifying information.
STOP = {"near", "nearby", "beside", "next", "to", "the", "at", "in", "on", "by",
        "close", "opposite", "behind", "front", "of", "we", "are", "i", "am",
        "here", "side", "road", "junction", "please", "come", "help"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9\s]", " ", s)


def _tokens(s: str) -> list[str]:
    return [t for t in _norm(s).split() if t and t not in STOP and len(t) > 2]


class Gazetteer:
    def __init__(self, osm_json: str | Path):
        self.places = []
        raw = json.loads(Path(osm_json).read_text(encoding="utf-8"))
        for e in raw.get("elements", []):
            tags = e.get("tags", {})
            name = tags.get("name")
            if not name:
                continue
            lat = e.get("lat") or (e.get("center") or {}).get("lat")
            lon = e.get("lon") or (e.get("center") or {}).get("lon")
            if lat is None or lon is None:
                continue
            kind = (tags.get("amenity") or tags.get("place")
                    or ("railway" if tags.get("railway") else None)
                    or ("bridge" if tags.get("bridge") else None) or "other")
            self.places.append({
                "name": name, "kind": kind, "lat": float(lat), "lon": float(lon),
                "radius_m": RADIUS_M.get(kind, DEFAULT_RADIUS_M),
                "tokens": set(_tokens(name)), "norm": _norm(name),
                "alt": _norm(tags.get("name:en") or tags.get("name:ml") or ""),
            })

    def __len__(self):
        return len(self.places)

    def _score(self, said_tokens: set[str], said_norm: str, p: dict) -> float:
        if not p["tokens"]:
            return 0.0
        overlap = len(said_tokens & p["tokens"]) / len(p["tokens"])
        # a whole place name appearing verbatim in the sentence is near-certain
        contains = 1.0 if p["norm"] and p["norm"] in said_norm else 0.0
        fuzzy = max(
            SequenceMatcher(None, said_norm, p["norm"]).ratio(),
            SequenceMatcher(None, said_norm, p["alt"]).ratio() if p["alt"] else 0.0,
        )
        # token overlap dominates; contains breaks ties; fuzzy catches misspellings
        return 0.55 * overlap + 0.30 * contains + 0.15 * fuzzy

    # Only settlements group the board. Snapping a call to the nearest tagged
    # hospital or bridge makes every call its own "place", which is a list with
    # extra steps -- an operator wants "four calls in Vallam", not four rows.
    SETTLEMENT_W = {"town": 0.8, "village": 1.0, "hamlet": 1.3,
                    "suburb": 1.3, "neighbourhood": 1.8}

    def nearest(self, lat: float, lon: float) -> dict | None:
        """The settlement a coordinate belongs to, for grouping the board."""
        best, bd = None, None
        for p in self.places:
            w = self.SETTLEMENT_W.get(p["kind"])
            if w is None:
                continue
            d = ((p["lat"] - lat) ** 2 + ((p["lon"] - lon) * 0.985) ** 2) ** 0.5 * w
            if bd is None or d < bd:
                best, bd = p, d
        if best is None:
            return None
        km = ((best["lat"] - lat) ** 2 + ((best["lon"] - lon) * 0.985) ** 2) ** 0.5 * 111.32
        return {"name": best["name"], "kind": best["kind"],
                "lat": best["lat"], "lon": best["lon"], "km": round(km, 2)}

    def locate(self, said: str, min_score: float = 0.34) -> dict | None:
        """Best landmark match for a spoken phrase, or None if nothing is close.

        Returning None is a real answer: it means the operator still has to ask.
        Guessing a coordinate here would put a rescue boat somewhere nobody is.
        """
        if not said or not said.strip():
            return None
        toks, norm = set(_tokens(said)), _norm(said)
        if not toks:
            return None
        ranked = sorted(((self._score(toks, norm, p), p) for p in self.places),
                        key=lambda x: x[0], reverse=True)
        best, p = ranked[0]
        if best < min_score:
            return None
        runner = ranked[1][0] if len(ranked) > 1 else 0.0
        # Two landmarks matching equally well is an ambiguous fix, not a good one.
        ambiguous = (best - runner) < 0.08
        return {
            "lat": p["lat"], "lon": p["lon"],
            "error_radius_m": p["radius_m"] * (2.0 if ambiguous else 1.0),
            "matched": p["name"], "kind": p["kind"],
            "confidence": round(best, 3), "ambiguous": ambiguous,
            "runner_up": ranked[1][1]["name"] if ambiguous and len(ranked) > 1 else None,
            "source": "landmark",
        }
