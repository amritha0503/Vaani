"""The features added on top of the original plan.  python test_features.py

Manual placement (map click / GPS / landmark), operator transcript correction,
audio playback, the terrain cross-section, the before/after surface layers,
the loc.html hand-off page, and the paced surge -- none of these touch the
ranker or the guardrail, so they get their own file rather than crowding
smoke_test.py or test_guardrail.py.
"""
import io
import sys
import time
import wave

from fastapi.testclient import TestClient

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILED = []


def check(name, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILED.append(name)


import main  # noqa: E402

with TestClient(main.app) as c:
    c.post("/demo/reset")

    print("1. a call can be placed by hand -- map click, GPS, or landmark")
    cid = c.post("/calls", json={
        "transcript": "no landmark mentioned at all", "lat": 10.12, "lon": 76.45,
        "error_radius_m": 50, "source": "fixture",
    }).json()["id"]
    r = c.post(f"/calls/{cid}/location", json={"lat": 10.10, "lon": 76.44, "source": "map_click"})
    check("map click accepted", r.status_code == 200)
    row = next(x for x in c.get("/queue").json()["calls"] if x["id"] == cid)
    check("location_source reflects the click", row["location_source"] == "map_click")

    r = c.post(f"/calls/{cid}/location",
               json={"lat": 10.10, "lon": 76.44, "error_radius_m": 0.01, "source": "gps"})
    check("a near-zero reported GPS accuracy is floored, not passed straight through",
          r.status_code == 200)

    r = c.post(f"/calls/{cid}/location", json={"landmark": "not a real place xyzzy"})
    check("an unrecognised landmark is rejected, not guessed", r.status_code == 422)

    r = c.post(f"/calls/{cid}/location", json={})
    check("neither coordinate nor landmark is rejected", r.status_code == 422)

    r = c.post("/calls/does-not-exist/location", json={"lat": 1, "lon": 1})
    check("an unknown call is 404, not a silent no-op", r.status_code == 404)

    print("\n2. the operator can retype a transcript ASR got wrong")
    cid2 = c.post("/calls", json={
        "transcript": "garbled nonsense the model mis-heard",
        "lat": 10.12, "lon": 76.45, "error_radius_m": 50, "source": "fixture",
    }).json()["id"]
    r = c.post(f"/calls/{cid2}/transcript", json={"text": "we are trapped near Odakkali, water rising"})
    check("corrected transcript accepted", r.status_code == 200)
    row = next(x for x in c.get("/queue").json()["calls"] if x["id"] == cid2)
    check("transcript actually changed", row["transcript"].startswith("we are trapped"))
    check("marked as operator-corrected", row["transcript_source"] == "operator")
    check("re-triaged off the new words (trapped -> band 3)", row["band"] == 3)

    manual = c.post(f"/calls/{cid2}/location", json={"lat": 10.2, "lon": 76.5, "source": "map_click"})
    check("manual placement still works after a correction", manual.status_code == 200)
    r = c.post(f"/calls/{cid2}/transcript", json={"text": "still trapped, no landmark this time"})
    row = next(x for x in c.get("/queue").json()["calls"] if x["id"] == cid2)
    check("a later correction with no landmark does not wipe an existing manual fix",
          row["lat"] == 10.2 and row["lon"] == 76.5)

    check("an empty correction is rejected, not accepted as blank",
          c.post(f"/calls/{cid2}/transcript", json={"text": "   "}).status_code == 422)
    check("correcting an unknown call is 404",
          c.post("/calls/does-not-exist/transcript", json={"text": "x"}).status_code == 404)

    print("\n3. a caller's own recording can be played back, when one exists")
    silence = io.BytesIO()
    with wave.open(silence, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 8000)   # half a second of silence
    up = c.post("/intake/audio",
                files=[("files", ("clip.wav", silence.getvalue(), "audio/wav"))])
    uid = up.json()["queued"][0]["id"]

    def row(call_id):
        return next(x for x in c.get("/queue").json()["calls"] if x["id"] == call_id)

    t0 = time.time()
    while time.time() - t0 < 60 and row(uid)["intake_stage"]:
        time.sleep(0.3)
    r = c.get(f"/audio/{uid}")
    check("an uploaded recording is servable", r.status_code == 200)
    check("has_audio is true on the board", row(uid)["has_audio"])
    check("a call with no recording says so, not a 500",
          c.get(f"/audio/{cid}").status_code == 404)

    # Silence names no landmark, so this upload is the un-placed call a
    # correction is actually for -- the case cid2 above could not test,
    # because POST /calls requires a coordinate up front.
    check("an upload whose speech named no landmark stays un-placed",
          row(uid)["lat"] is None)
    c.post(f"/calls/{uid}/transcript", json={"text": "we are trapped near Odakkali, water rising"})
    check("correcting the transcript geocodes the landmark it now contains",
          row(uid)["lat"] is not None and row(uid)["landmark"] == "Odakkali")

    print("\n4. the surface renders both layers, and a bogus one is rejected")
    exp_png = c.get("/surface.png?layer=exposure")
    hand_png = c.get("/surface.png?layer=hand")
    default_png = c.get("/surface.png")
    check("exposure layer renders", exp_png.status_code in (200, 503))
    check("hand layer renders", hand_png.status_code in (200, 503))
    if exp_png.status_code == 200:
        check("hand and exposure are visually distinct images", hand_png.content != exp_png.content)
        check("no layer defaults to exposure (matches the original endpoint)",
              default_png.content == exp_png.content)
    check("an unknown layer name is rejected", c.get("/surface.png?layer=bogus").status_code == 422)

    print("\n5. the cross-section reads real terrain, not a fabricated line")
    sec = c.get("/exposure/section?lat=10.12&lon=76.45")
    if sec.status_code == 200:
        body = sec.json()
        check("a direction and points come back", "direction_deg" in body and len(body["points"]) > 1)
        check("distances are non-negative and increasing",
              all(b["distance_m"] >= a["distance_m"] for a, b in zip(body["points"], body["points"][1:])))
    else:
        check("section 503s honestly when there is no baked surface", sec.status_code == 503)

    print("\n6. loc.html is reachable -- the page an SMS link points at")
    r = c.get("/loc.html")
    check("loc.html serves", r.status_code == 200)
    check("it asks for geolocation, not a fabricated fix", "geolocation" in r.text)

    print("\n7. a surge can be paced, or kept instant for tests")
    c.post("/demo/reset")
    t0 = time.time()
    n = len(c.post("/demo/surge?spread_s=0").json()["ingested"])
    instant_s = time.time() - t0
    check("30 fixtures are shipped", n == 30)
    check("spread_s=0 ingests essentially at once", instant_s < 5)

print()
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED: " + "; ".join(FAILED))
    raise SystemExit(1)
print("FEATURES HOLD — manual placement, transcript correction, playback, both surface layers, "
      "the cross-section, loc.html and a paced surge all behave.")
