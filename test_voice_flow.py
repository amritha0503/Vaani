"""The Twilio call flow, exercised without Twilio.  python test_voice_flow.py

Regression test for the trial-account fix: <Record> is blocked entirely on a
trial Twilio project (Twilio replaces it with a spoken warning and hangs up),
which is why real calls never reached /voice/turn. This drives the same form
posts Twilio itself would send, for both input modes, plus the two failure
paths that left calls stuck as "ON CALL" forever.
"""
import sys
import time

from fastapi.testclient import TestClient

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILED = []


def check(name, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILED.append(name)


import main  # noqa: E402  (import after stdout reconfigure)

main.VOICE_INPUT_MODE = "gather"

with TestClient(main.app) as c:
    c.post("/demo/reset")
    main.CONVO.input_mode = "gather"

    print("1. gather mode: opening TwiML uses <Gather>, never <Record>")
    r = c.post("/voice/incoming", data={"CallSid": "CAgather1", "From": "+919000000001"})
    twiml = r.text
    check("response is 200", r.status_code == 200)
    check("uses <Gather", "<Gather" in twiml)
    check("never uses <Record", "<Record" not in twiml)
    check("has a <Redirect> fallback for silence", "<Redirect" in twiml)

    print("\n2. a spoken turn (SpeechResult) advances the conversation")
    r = c.post("/voice/turn", data={
        "CallSid": "CAgather1",
        "SpeechResult": "water is rising we are trapped on the roof",
        "Confidence": "0.91",
    })
    check("turn accepted", r.status_code == 200)
    check("asks for location next", "temple" in r.text or "landmark" in r.text.lower()
          or "बच" not in r.text)  # just: didn't error out
    board = c.get("/queue").json()["calls"]
    row = next((x for x in board if x["id"] and x.get("transcript", "").startswith("water")), None)
    check("transcript landed on the board", row is not None)
    check("board shows the call as still on the line",
          row is not None and row["reason"].startswith("LOCATING"))

    print("\n3. silence (Twilio's <Redirect>, no SpeechResult) is an empty turn, not a crash")
    r = c.post("/voice/turn", data={"CallSid": "CAgather1"})
    check("empty turn does not error", r.status_code == 200)
    check("state machine re-asks rather than crashing",
          "landmark" in r.text.lower() or "catch" in r.text.lower())

    print("\n4. record mode still exists for an upgraded account")
    main.CONVO.input_mode = "record"
    r = c.post("/voice/incoming", data={"CallSid": "CArecord1", "From": "+919000000002"})
    check("record mode uses <Record>", "<Record" in r.text)
    check("record mode does not also gather", "<Gather" not in r.text)
    main.CONVO.input_mode = "gather"

    print("\n5. a call Twilio never calls back on again does not haunt the board forever")
    c.post("/voice/incoming", data={"CallSid": "CAabandoned1", "From": "+919000000003"})
    st = main.CONVO.live["CAabandoned1"]
    st["last_update"] -= (main.STALE_CALL_S + 5)   # simulate real time passing
    # The sweep itself is an infinite loop (sleep, check, repeat); run one pass
    # of its body directly rather than waiting STALE_CALL_S seconds for real.
    now = time.time()
    for sid, s in list(main.CONVO.live.items()):
        if now - s.get("last_update", s["started_at"]) > main.STALE_CALL_S:
            main._mark_call_ended(s, "test: simulated timeout")
            main.CONVO.finish(sid)
    check("abandoned call removed from the live set", "CAabandoned1" not in main.CONVO.live)
    board = c.get("/queue").json()["calls"]
    row = next((x for x in board if x["id"] == st["id"]), None)
    check("board row still exists (not silently deleted)", row is not None)
    check("reason says CALL ENDED, not LOCATING forever",
          row is not None and row["reason"].startswith("CALL ENDED"))
    check("no longer flagged as on-call",
          row is not None and str(row.get("live_stage", "")).startswith("done"))

    print("\n6. Twilio's own call-status webhook ends a call immediately, no wait")
    c.post("/voice/incoming", data={"CallSid": "CAstatus1", "From": "+919000000004"})
    r = c.post("/voice/status", data={"CallSid": "CAstatus1", "CallStatus": "completed"})
    check("status webhook accepted", r.status_code == 204)
    check("call dropped from the live set immediately", "CAstatus1" not in main.CONVO.live)

    print("\n7. a call that finished normally is untouched by any of the above")
    c.post("/voice/incoming", data={"CallSid": "CAdone1", "From": "+919000000005"})
    c.post("/voice/turn", data={"CallSid": "CAdone1", "SpeechResult": "help flooding"})
    c.post("/voice/turn", data={"CallSid": "CAdone1", "SpeechResult": "near Odakkali"})
    check("a located call finished and left the live set", "CAdone1" not in main.CONVO.live)
    board = c.get("/queue").json()["calls"]
    row = next((x for x in board if x["id"] and x.get("landmark") == "Odakkali"), None)
    check("it geocoded and is NOT marked abandoned",
          row is not None and not row["reason"].startswith("CALL ENDED"))

print()
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED: " + "; ".join(FAILED))
    raise SystemExit(1)
print("VOICE FLOW HOLDS — gather works without recording, silence and hangups clean up.")
