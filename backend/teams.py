"""Rescue Teams Management & Proximity Assignment for Ernakulam.

Groups emergency victims geographically and assigns each to the closest rescue team
stationed at strategic hubs across the district (Aluva, Perumbavoor, Kalady,
Kuruppampady, Kunnathunadu).

Within each team's assigned roster, victims are prioritized strictly by the
Golden Invariant (Band 3 life-threats targeted first).
"""
import math
from typing import Optional

# Strategic emergency bases in Ernakulam district
RESCUE_TEAMS = [
    {
        "id": "aluva",
        "name": "Aluva Base",
        "hub": "Aluva Fire & Rescue Station (CIAL)",
        "lat": 10.1557006,
        "lon": 76.403173,
        "radius_km": 12.0,
        "vehicles": ["Rescue Boat", "Emergency Ambulance", "Heavy Transport"],
        "color": "#38bdf8",  # Sky blue
    },
    {
        "id": "perumbavoor",
        "name": "Perumbavoor Base",
        "hub": "Fire and Rescue Station, Perumbavoor",
        "lat": 10.1172853,
        "lon": 76.4933522,
        "radius_km": 12.0,
        "vehicles": ["Rescue Boat", "Amphibious Vehicle", "Medical Van"],
        "color": "#34d399",  # Emerald green
    },
    {
        "id": "kalady",
        "name": "Kalady Base",
        "hub": "Kalady Rescue Station",
        "lat": 10.1708481,
        "lon": 76.4471322,
        "radius_km": 10.0,
        "vehicles": ["Inflatable Rescue Boat", "Medical Rapid Response"],
        "color": "#f59e0b",  # Amber
    },
    {
        "id": "kuruppampady",
        "name": "Kuruppampady Base",
        "hub": "Kuruppampady Rescue Station",
        "lat": 10.1106649,
        "lon": 76.5177301,
        "radius_km": 10.0,
        "vehicles": ["All-Terrain 4x4", "Medical Unit"],
        "color": "#a855f7",  # Purple
    },
    {
        "id": "kunnathunadu",
        "name": "Kunnathunadu Base",
        "hub": "Kunnathunadu Rescue Station",
        "lat": 10.0235539,
        "lon": 76.450597,
        "radius_km": 12.0,
        "vehicles": ["Rescue Boat", "Utility Transport"],
        "color": "#f43f5e",  # Rose
    },
]

TEAMS_BY_ID = {t["id"]: t for t in RESCUE_TEAMS}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points in kilometers."""
    R = 6371.0  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def find_nearest_team(lat: Optional[float], lon: Optional[float]) -> Optional[dict]:
    """Find the closest rescue team to given coordinates and check service radius."""
    if lat is None or lon is None:
        return None

    best_team = None
    min_dist = float("inf")

    for team in RESCUE_TEAMS:
        d = haversine_km(lat, lon, team["lat"], team["lon"])
        if d < min_dist:
            min_dist = d
            best_team = team

    if best_team is None:
        return None

    return {
        "team_id": best_team["id"],
        "team_name": best_team["name"],
        "hub": best_team["hub"],
        "color": best_team["color"],
        "distance_km": round(min_dist, 2),
        "within_radius": min_dist <= best_team["radius_km"],
        "base_lat": best_team["lat"],
        "base_lon": best_team["lon"],
    }


def group_calls_by_teams(ranked_calls: list[dict]) -> dict:
    """Group all emergency calls by their assigned rescue team.

    Each team's targets are sorted strictly by priority:
    (Band descending, Within Score descending) so life threats are always first.
    """
    rosters = {
        t["id"]: {
            **t,
            "assigned_calls": [],
            "life_threats": 0,
            "total_assigned": 0,
            "avg_distance_km": 0.0,
            "closest_distance_km": None,
        }
        for t in RESCUE_TEAMS
    }

    unassigned = []

    for call in ranked_calls:
        # Check manual override assignment first if present
        manual_tid = call.get("manual_team_id")
        assigned = None

        if manual_tid and manual_tid in rosters:
            team_info = rosters[manual_tid]
            dist = (haversine_km(call["lat"], call["lon"], team_info["lat"], team_info["lon"])
                    if call.get("lat") is not None and call.get("lon") is not None else None)
            assigned = {
                "team_id": team_info["id"],
                "team_name": team_info["name"],
                "hub": team_info["hub"],
                "color": team_info["color"],
                "distance_km": round(dist, 2) if dist is not None else None,
                "within_radius": dist <= team_info["radius_km"] if dist is not None else True,
                "base_lat": team_info["lat"],
                "base_lon": team_info["lon"],
                "manual_override": True,
            }
        elif call.get("lat") is not None and call.get("lon") is not None:
            assigned = find_nearest_team(call["lat"], call["lon"])

        if assigned:
            tid = assigned["team_id"]
            call_enriched = {
                **call,
                "assigned_team": assigned,
            }
            rosters[tid]["assigned_calls"].append(call_enriched)
            rosters[tid]["total_assigned"] += 1
            if call.get("band", 0) >= 3:
                rosters[tid]["life_threats"] += 1
        else:
            unassigned.append(call)

    # Compute averages and sort internal targets by priority
    for r in rosters.values():
        r["assigned_calls"].sort(key=lambda x: (x["band"], x["within"]), reverse=True)
        distances = [c["assigned_team"]["distance_km"] for c in r["assigned_calls"]
                     if c.get("assigned_team") and c["assigned_team"].get("distance_km") is not None]
        if distances:
            r["avg_distance_km"] = round(sum(distances) / len(distances), 2)
            r["closest_distance_km"] = round(min(distances), 2)

    return {
        "teams": list(rosters.values()),
        "unassigned_count": len(unassigned),
        "total_assigned": sum(r["total_assigned"] for r in rosters.values()),
    }
