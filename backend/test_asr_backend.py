"""The Gemini/Whisper hybrid, without a real key or a network call.
python test_asr_backend.py

asr.transcribe() is the one function every audio path in the app calls.
These checks monkeypatch _transcribe_gemini directly rather than hitting the
real API -- what is under test is the FAILOVER logic (auto vs forced modes,
the rehearsal cut, never silently returning nothing), not Google's model.
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILED = []


def check(name, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILED.append(name)


import backend.asr as asr  # noqa: E402

WHISPER_CALLS = []


def fake_whisper(path):
    WHISPER_CALLS.append(path)
    return {"text": "whisper heard this", "language": "en", "language_name": "English",
            "language_confidence": 0.9, "duration_s": 2.0, "ok": True, "backend": "whisper"}


asr._transcribe_whisper = fake_whisper  # never touch the real model in this file

print("1. unconfigured -- no key at all -- runs on Whisper alone")
asr.GEMINI_API_KEY = ""
asr.ASR_BACKEND = "auto"
check("gemini_configured is False with no key", not asr.gemini_configured())
r = asr.transcribe("x.wav")
check("falls straight to whisper", r["backend"] == "whisper")
check("no gemini_error noise when gemini was never attempted", "gemini_error" not in r)

print("\n2. configured, Gemini succeeds -- Gemini serves it, Whisper never runs")
asr.GEMINI_API_KEY = "fake-key-for-test"
WHISPER_CALLS.clear()
asr._transcribe_gemini = lambda path: {
    "text": "gemini heard this correctly", "language": "ml", "language_name": "Malayalam",
    "language_confidence": None, "duration_s": None, "ok": True, "backend": "gemini"}
r = asr.transcribe("x.wav")
check("gemini configured with a key set", asr.gemini_configured())
check("gemini serves the call", r["backend"] == "gemini" and r["text"] == "gemini heard this correctly")
check("whisper was not called at all", len(WHISPER_CALLS) == 0)

print("\n3. configured, Gemini fails -- falls through to Whisper, error is visible")
asr._transcribe_gemini = lambda path: (_ for _ in ()).throw(RuntimeError("quota exceeded"))
WHISPER_CALLS.clear()
r = asr.transcribe("x.wav")
check("falls back to whisper on a gemini exception", r["backend"] == "whisper")
check("whisper actually ran", len(WHISPER_CALLS) == 1)
check("the gemini failure is recorded, not swallowed silently",
      "quota exceeded" in r.get("gemini_error", ""))

print("\n4. Gemini fails AND Whisper fails -- an honest empty result, not a crash")
def _boom(path):
    raise RuntimeError("model not loaded")
asr._transcribe_whisper = _boom
r = asr.transcribe("x.wav")
check("ok is False", r["ok"] is False)
check("both failures named in the error", "quota exceeded" in r["error"] and "model not loaded" in r["error"])
asr._transcribe_whisper = fake_whisper  # restore for the rest of this file

print("\n5. VAANI_ASR_BACKEND=whisper forces local-only even with a key configured")
asr.ASR_BACKEND = "whisper"
WHISPER_CALLS.clear()
check("gemini_configured is False once backend is forced to whisper",
      not asr.gemini_configured())
r = asr.transcribe("x.wav")
check("whisper serves it, gemini never attempted", r["backend"] == "whisper" and len(WHISPER_CALLS) == 1)
asr.ASR_BACKEND = "auto"

print("\n6. the rehearsal network cut actually disables Gemini, not just the badge")
asr._transcribe_gemini = lambda path: {"text": "should not be reached", "language": None,
                                        "language_name": None, "language_confidence": None,
                                        "duration_s": None, "ok": True, "backend": "gemini"}
asr.CLOUD_DISABLED_FOR_REHEARSAL = True
WHISPER_CALLS.clear()
r = asr.transcribe("x.wav")
check("cutting the network routes to whisper, not gemini",
      r["backend"] == "whisper" and len(WHISPER_CALLS) == 1)
asr.CLOUD_DISABLED_FOR_REHEARSAL = False
r = asr.transcribe("x.wav")
check("restoring the network brings gemini back", r["backend"] == "gemini")

print()
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED: " + "; ".join(FAILED))
    raise SystemExit(1)
print("ASR HYBRID HOLDS — Gemini first when configured, Whisper under every failure mode.")
