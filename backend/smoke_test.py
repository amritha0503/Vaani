import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
import backend.main as main

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # fixtures are multilingual

with TestClient(main.app) as c:
    c.post("/demo/reset")
    n = len(c.post("/demo/surge?spread_s=0").json()["ingested"])

    # The board fills instantly on the keyword spotter and the rows upgrade as
    # the model answers. Wait for that to settle so the printed board is stable;
    # with no model running this returns on the first poll.
    t0 = time.time()
    while time.time() - t0 < 5:
        if not any(r["extractor_pending"] for r in c.get("/queue").json()["calls"]):
            break
        time.sleep(0.5)
    print(f"extraction settled in {time.time() - t0:.1f}s", flush=True)

    q = c.get("/queue").json()
    board, deg = q["calls"], q["degradation"]

    print(f"ingested {n} calls | surface={deg['surface']} level={deg['level']} "
          f"extraction={deg['extraction']}")
    print(f"{'#':>2} {'band':>4} {'score':>6}  {'src':<8} {'hand':>5}  reason")
    for r in board:
        hand = f"{r['hand_m']:.1f}" if r["hand_m"] is not None else "  -"
        print(f"{r['rank']:>2} {r['band']:>4} {r['within']:>6.3f}  "
              f"{r['extractor']:<8} {hand:>5}  {r['transcript'][:44]}")

    # 1. the twin pair must split
    twins = [r for r in board if r["transcript"] == "Water is rising, please help."]
    assert len(twins) == 2, "twin pair missing from fixtures"
    gap = abs(twins[0]["rank"] - twins[1]["rank"])
    print(f"\ntwin split: positions {twins[0]['rank']} and {twins[1]['rank']} (gap {gap})")

    # 2. the life-threat floor must hold the top of the board
    assert board[0]["band"] == 3, "top of queue is not a band-3 call"

    # 3. terrain must never demote a life threat below a non-threat
    threat_ranks = [r["rank"] for r in board if r["band"] == 3]
    other_ranks = [r["rank"] for r in board if r["band"] < 3]
    assert not other_ranks or max(threat_ranks) < min(other_ranks), \
        "FLOOR VIOLATED: a life threat sits below a lesser call"
    print(f"floor holds: {len(threat_ranks)} life-threat calls occupy positions "
          f"1-{max(threat_ranks)}")

    # 4. a wide fix must ask for a landmark instead of ranking confidently
    wide = [r for r in board if r["error_radius_m"] > 800]
    assert wide and "REQUEST LANDMARK" in wide[0]["reason"], "wide fix not flagged"
    print(f"wide fix flagged: {wide[0]['reason']}")

    floored = [r for r in board if r["extractor"] == "llm+floor"]
    for r in floored:
        print(f"   spotter floor held #{r['rank']}: {r['extractor_note']}")

    # 5. dispatch must route on the same surface, and bend around the water
    routed = {r["id"]: c.get(f"/dispatch/{r['id']}").json() for r in board}
    dry = [x for x in routed.values() if x.get("route")]
    bent = [x for x in dry if (x.get("detour_m") or 0) > 0]
    boat = [x for x in routed.values() if x.get("reason") == "boat_or_air_required"]
    assert dry, "no call could be routed at all — is data/roadgraph.json baked?"
    assert bent, "no route bent around water: the exposure layer is doing nothing"
    worst = max(bent, key=lambda x: x["detour_m"])
    print(f"\nrouting: {len(dry)} by road, {len(boat)} need boat or air")
    print(f"longest detour: {worst['direct_length_m']} m direct (flooded) -> "
          f"{worst['length_m']} m dry, +{worst['detour_m']} m around "
          f"{len(worst['blocked_on_direct'])} cut segments, from {worst['depot']['name']}")
    cut = c.get("/roads/impassable").json()
    assert cut["count"] > 0, "no impassable segments to draw"
    print(f"impassable layer: {cut['count']} segments at exposure >= {cut['cut']}")

    # 6. the ladder must keep ranking AND routing with the network gone
    off = c.post("/demo/cut-network?offline=true").json()
    after = c.get("/queue").json()["calls"]
    assert off["ranking"] and off["routing"] and len(after) == n
    # Routing offline is not an assertion in a slide, it is this line: the graph
    # and the raster are local files, so the same route computes with no network.
    first_routed = next(i for i, x in routed.items() if x.get("route"))
    again = c.get(f"/dispatch/{first_routed}").json()
    assert again.get("route"), "dispatch lost its route when the network went"
    print(f"offline: level={off['level']} lost={off['lost']} "
          f"ranking still returns {len(after)} calls, dispatch still answers")

    # 7. Camp proximity risk factor
    for r in board:
        assert "camp_risk" in r, f"Call {r['id']} missing camp_risk on board"
        if r.get("camp_risk"):
            assert r["camp_risk"]["risk"] in ("low", "high")

    # Call-level endpoint check
    sample_call_id = board[0]["id"]
    camp_risk_resp = c.get(f"/calls/{sample_call_id}/camp-risk").json()
    assert camp_risk_resp["risk"] in ("low", "high")
    assert "reason" in camp_risk_resp
    print(f"\ncamp risk factor: #{board[0]['rank']} -> {camp_risk_resp['risk']} risk ({camp_risk_resp['reason']})")

    # 8. Responder safety briefing
    briefing_resp = c.get(f"/dispatch/{sample_call_id}/briefing").json()
    assert "warnings" in briefing_resp
    assert "generated_from" in briefing_resp
    assert "route_hazards" in briefing_resp
    print(f"safety briefing: {len(briefing_resp['warnings'])} warnings, triggered by {briefing_resp['generated_from']}")

    # 9. Live camp declaration during demo
    decl = c.post("/camps", json={"name": "Kunnathunadu Taluk Relief Camp", "lat": 10.024, "lon": 76.451}).json()
    assert decl["ok"], "failed to declare live camp"
    print(f"live camp declared: {decl['camp']['name']} (total camps now {decl['total_camps']})")

    print(f"\naudit rows written: {len(c.get('/audit').json())}")
    print("\nALL CHECKS PASSED")
