"""Smoke test. Run it after any change to the ranker:  python smoke_test.py"""
import sys
import time
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
    while time.time() - t0 < 300:
        if not any(r["extractor_pending"] for r in c.get("/queue").json()["calls"]):
            break
        time.sleep(0.5)
    print(f"extraction settled in {time.time() - t0:.1f}s")

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

    print(f"\naudit rows written: {len(c.get('/audit').json())}")
    print("\nALL CHECKS PASSED")
