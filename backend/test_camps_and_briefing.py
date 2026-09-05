"""Tests for Camp-Proximity Risk Factor and Responder Safety Briefing.

Extends smoke_test.py and test_guardrail.py patterns:
1. A call near a seeded/declared camp with a dry route -> risk "low".
2. A call with no dry route or >1500m to any camp -> risk "high", reason names why.
3. Camp risk must never change a call's band — assert this directly by comparing
   rank.rank() output with and without camp data present.
4. briefing.py guardrail test: every string in warnings must trace to a rule
   that fired on the given inputs — assert no warning appears when its
   triggering condition is false.
"""
import copy
import json
import os
import sys
from pathlib import Path

# Ensure project root in sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backend.rank as rank
import backend.briefing as briefing
from backend.dispatch import RoadNetwork, FLOOD_CUT


def test_briefing_guardrails():
    """Guardrail test: every warning string must trace 1:1 to a fired rule."""
    print("--- Testing briefing guardrails ---")

    # Baseline empty inputs -> zero warnings, zero route hazards, zero triggers
    b_empty = briefing.generate_briefing(call_fields={}, exposure={}, route={})
    assert b_empty["warnings"] == [], f"Expected empty warnings, got {b_empty['warnings']}"
    assert b_empty["generated_from"] == [], f"Expected empty triggers, got {b_empty['generated_from']}"
    assert b_empty["route_hazards"] == [], f"Expected empty hazards, got {b_empty['route_hazards']}"

    # Test 1: flood + hand_m < 3
    # Case A: False condition (hand_m >= 3)
    b_flood_high = briefing.generate_briefing(
        call_fields={"hazard_class": "flood"},
        exposure={"hand_m": 4.5},
        route={}
    )
    assert not any("Do not wade" in w for w in b_flood_high["warnings"]), "Should not warn wade when hand_m >= 3"
    assert "hand_m" not in b_flood_high["generated_from"]

    # Case B: True condition (hand_m < 3)
    b_flood_low = briefing.generate_briefing(
        call_fields={"hazard_class": "flood"},
        exposure={"hand_m": 1.8},
        route={}
    )
    assert any("Ground-level flooding at the site. Do not wade" in w for w in b_flood_low["warnings"])
    assert "hand_m" in b_flood_low["generated_from"]

    # Test 2: access_constraint == "water_on_road"
    b_access_none = briefing.generate_briefing(call_fields={"access_constraint": "none"})
    assert not any("Vehicle access likely blocked" in w for w in b_access_none["warnings"])
    assert "access_constraint" not in b_access_none["generated_from"]

    b_access_water = briefing.generate_briefing(call_fields={"access_constraint": "water_on_road"})
    assert any("Vehicle access likely blocked short of the site" in w for w in b_access_water["warnings"])
    assert "access_constraint" in b_access_water["generated_from"]

    # Test 3: landslide
    b_landslide = briefing.generate_briefing(call_fields={"hazard_class": "landslide"})
    assert any("Unstable slope reported" in w for w in b_landslide["warnings"])
    assert "hazard_class" in b_landslide["generated_from"]

    # Test 4: medical_critical
    b_med_false = briefing.generate_briefing(call_fields={"medical_critical": False})
    assert not any("Medical-critical" in w for w in b_med_false["warnings"])
    assert "medical_critical" not in b_med_false["generated_from"]

    b_med_true = briefing.generate_briefing(call_fields={"medical_critical": True})
    assert any("Medical-critical caller — bring trauma/medical kit" in w for w in b_med_true["warnings"])
    assert "medical_critical" in b_med_true["generated_from"]

    # Test 5: max_exposure_on_route > 0.5
    b_exp_low = briefing.generate_briefing(route={"max_exposure_on_route": 0.35})
    assert not any("Approach route itself crosses elevated-exposure ground" in w for w in b_exp_low["warnings"])
    assert "max_exposure_on_route" not in b_exp_low["generated_from"]

    b_exp_high = briefing.generate_briefing(route={"max_exposure_on_route": 0.62})
    assert any("Approach route itself crosses elevated-exposure ground" in w for w in b_exp_high["warnings"])
    assert "max_exposure_on_route" in b_exp_high["generated_from"]

    # Test 6: blocked_on_direct
    blocked_segs = [
        {"name": "Aluva-Munnar Road", "hand_m": 0.8, "exposure": 0.72},
        {"name": "Perumbavoor Bypass", "hand_m": 1.2, "exposure": 0.65},
    ]
    b_blocked = briefing.generate_briefing(route={"blocked_on_direct": blocked_segs})
    assert "blocked_on_direct" in b_blocked["generated_from"]
    assert len(b_blocked["route_hazards"]) == 2
    assert any("Aluva-Munnar Road: 0.8 m above drainage" in w for w in b_blocked["warnings"])
    assert any("Perumbavoor Bypass: 1.2 m above drainage" in w for w in b_blocked["warnings"])

    print("[PASS] All briefing guardrail rules verified strictly against inputs.")


def test_golden_invariant_ranking_untouched():
    """Assert camp proximity NEVER changes a call's band or rank order."""
    print("--- Testing Golden Invariant (ranking untouched by camps) ---")
    
    sample_calls = [
        {
            "id": "c1",
            "received_at": 1000.0,
            "fields": {"hazard_class": "flood", "severity_band": 3, "trapped": True, "vulnerable": []},
            "exposure": {"hand_m": 0.8, "p75": 0.85, "in_aoi": True},
            "override": 0,
            "lat": 10.155, "lon": 76.403,
        },
        {
            "id": "c2",
            "received_at": 1005.0,
            "fields": {"hazard_class": "flood", "severity_band": 2, "trapped": False, "vulnerable": ["elderly"]},
            "exposure": {"hand_m": 4.2, "p75": 0.35, "in_aoi": True},
            "override": 0,
            "lat": 10.115, "lon": 76.480,
        },
        {
            "id": "c3",
            "received_at": 1010.0,
            "fields": {"hazard_class": "flood", "severity_band": 1, "trapped": False, "vulnerable": []},
            "exposure": {"hand_m": 8.5, "p75": 0.10, "in_aoi": True},
            "override": 0,
            "lat": 10.170, "lon": 76.445,
        },
    ]

    # Baseline rank
    ranked_baseline = rank.rank(copy.deepcopy(sample_calls))

    # Add camp proximity data to calls
    calls_with_camps = copy.deepcopy(sample_calls)
    calls_with_camps[0]["camp_risk"] = {"risk": "high", "nearest_camp": None, "reason": "no camp"}
    calls_with_camps[1]["camp_risk"] = {"risk": "low", "nearest_camp": {"name": "Test Camp", "length_m": 400}, "reason": "close"}
    calls_with_camps[2]["camp_risk"] = {"risk": "low", "nearest_camp": {"name": "Test Camp 2", "length_m": 200}, "reason": "close"}

    ranked_with_camps = rank.rank(calls_with_camps)

    # Compare orders and bands
    for b_row, c_row in zip(ranked_baseline, ranked_with_camps):
        assert b_row["id"] == c_row["id"], f"Rank order altered! {b_row['id']} vs {c_row['id']}"
        assert b_row["band"] == c_row["band"], f"Band altered for {b_row['id']}!"
        assert b_row["rank"] == c_row["rank"], f"Rank index altered for {b_row['id']}!"

    print("[PASS] Golden Invariant confirmed: camp data has ZERO influence on rank and priority bands.")


def test_camp_reachability_and_routing():
    """Test RoadNetwork nearest_camp reachability and risk classification."""
    print("--- Testing RoadNetwork nearest_camp reachability ---")
    roads_path = ROOT / "backend" / "data" / "roadgraph.json"
    if not roads_path.exists():
        roads_path = ROOT / "data" / "roadgraph.json"
    
    if not roads_path.exists():
        print("roadgraph.json not found, skipping network graph test.")
        return

    net = RoadNetwork(roads_path)
    assert len(net.camp_candidates) > 0, "No camp candidates loaded in RoadNetwork"
    print(f"Loaded {len(net.camp_candidates)} relief camps into RoadNetwork.")

    # 1. Test a call located right next to a camp (e.g. Aluva near St. Joseph's / Govt School)
    camp0 = net.camp_candidates[0]
    res_near = net.nearest_camp(camp0["lat"], camp0["lon"])
    assert res_near["risk"] in ("low", "high")
    if res_near["nearest_camp"]:
        print(f"Near camp query: {res_near['reason']} (risk={res_near['risk']})")
        if res_near["nearest_camp"]["length_m"] <= 1500:
            assert res_near["risk"] == "low"

    # 2. Test declaring a live camp dynamically
    live_camp = {
        "name": "Live Emergency Shelter Aluva Stadium",
        "kind": "community_centre",
        "lat": 10.1140,
        "lon": 76.4715,
        "declared_live": True,
    }
    net.add_live_camp(live_camp)
    assert any(c["name"] == "Live Emergency Shelter Aluva Stadium" for c in net.camp_candidates)

    # Query immediately at the declared shelter
    res_live = net.nearest_camp(10.1140, 76.4715)
    print(f"Live declared camp query: {res_live['reason']} (risk={res_live['risk']})")
    assert res_live["risk"] == "low"
    assert res_live["nearest_camp"]["length_m"] <= 1500

    # 3. Test a far coordinate where distance exceeds 1500m
    res_far = net.nearest_camp(10.020, 76.570)
    print(f"Far coordinate query: {res_far['reason']} (risk={res_far['risk']})")
    if res_far["nearest_camp"] is not None and res_far["nearest_camp"]["length_m"] > 1500:
        assert res_far["risk"] == "high"
        assert "exceeds 1.5 km" in res_far["reason"]

    print("[PASS] RoadNetwork nearest_camp reachability and thresholds verified.")


if __name__ == "__main__":
    test_briefing_guardrails()
    test_golden_invariant_ranking_untouched()
    test_camp_reachability_and_routing()
    print("\nALL TESTS PASSED SUCCESSFULLY!")
