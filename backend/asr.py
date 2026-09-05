"""Speech recognition, two backends behind one function.

**Local (faster-whisper)** runs on the CPU, on the box, with the model cached
on disk. No audio leaves the building -- which matters both for the
resilience claim and because emergency call audio is about the most sensitive
data there is. It is also the only backend that keeps working with the
network cable pulled, which in a real flood is not a hypothetical.

**Cloud (gemini-3.5-transcribe)** trades that offline guarantee for real
accuracy: measured on an actual short Malayalam clip, local Whisper `small`
correctly identified the language and then transcribed it into romanised
gibberish -- confirmed stable across beam_size 1-5 and VAD on/off, so it is a
model-capacity ceiling, not a tuning problem. Gemini reports ~2.6% average WER
across 85+ languages, Malayalam included. It is not a strict upgrade: Tamil
is not in Gemini's supported-language list, and a free-tier or unconfigured
key means every call falls straight through to Whisper anyway.

So: try Gemini first when it is configured, reachable, and not rehearsing a
network cut; on ANY failure -- no key, package not installed, quota, a bad
response shape, a network error -- fall straight through to local Whisper
without the caller ever seeing the difference. Only the returned "backend"
field says which one actually answered, the same way "extractor" says
whether the model or the keyword spotter actually triaged a call.
"""
import os
import threading

MODEL_SIZE = os.environ.get("VAANI_ASR_MODEL", "small")   # base | small | medium
_model = None
_lock = threading.Lock()

# auto: Gemini first if configured & reachable, Whisper on any failure.
# gemini: same, but never silently skip Gemini for a rehearsal network cut --
#         useful for testing the integration itself.
# whisper: local only, always -- what a genuinely offline box must fall back to.
ASR_BACKEND = os.environ.get("VAANI_ASR_BACKEND", "auto").lower()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3.5-transcribe"
GEMINI_TIMEOUT_S = float(os.environ.get("VAANI_GEMINI_TIMEOUT_S", "20"))

# Set by main.py's /demo/cut-network -- rehearsing "the cable is pulled" must
# actually stop the one thing in this file that needs a network, not just
# relabel the badge. Local Whisper does not care either way.
CLOUD_DISABLED_FOR_REHEARSAL = False

_gemini_client = None

# Whisper reports ISO codes; these are the ones the control room cares about.
SUPPORTED = {"en": "English", "ml": "Malayalam", "hi": "Hindi",
             "ta": "Tamil", "te": "Telugu", "kn": "Kannada"}

# Confirmed on Gemini's own supported-language table. Tamil is a known gap --
# not a bug, Google's model card simply does not list it. Kept here only as
# documentation; transcribe() does not gate on it because we do not know a
# clip's language before transcribing it, and Gemini may still do a
# reasonable job even on an unlisted language.
GEMINI_LANGUAGES = {"en", "ml", "hi", "kn", "te", "mr", "or", "pa"}


def load(size: str = MODEL_SIZE):
    """Lazy, and warmed once at startup -- never on the first real call."""
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(size, device="cpu", compute_type="int8")
    return _model


def ready() -> bool:
    return _model is not None


def gemini_configured() -> bool:
    return bool(GEMINI_API_KEY) and ASR_BACKEND != "whisper"


def _gemini() -> object:
    global _gemini_client
    if _gemini_client is None:
        from google import genai            # heavy-ish, imported only if used
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _transcribe_gemini(path: str) -> dict:
    """One upload, one transcription call. Whatever this raises, transcribe()
    below catches -- a malformed response is exactly as much a "Gemini
    failure" as a dropped connection, and both mean "use Whisper instead"."""
    client = _gemini()
    audio_file = client.files.upload(file=path)
    interaction = client.interactions.create(
        model=GEMINI_MODEL,
        input=[{"type": "audio", "uri": audio_file.uri,
                "mime_type": audio_file.mime_type}],
    )
    text = (getattr(interaction, "output_text", None) or "").strip()
    if not text:
        raise RuntimeError("gemini returned no text")
    # The language-detection field in the response is not something the
    # public docs pin down a name for -- probed defensively, three plausible
    # shapes, never trusted enough to raise if none match. Losing the
    # language badge on a Gemini-served row is a cosmetic gap; guessing wrong
    # and mislabelling it is worse.
    lang = None
    for probe in (
        lambda: interaction.output_language,
        lambda: interaction.steps[0].content[0].language,
        lambda: interaction.language,
    ):
        try:
            v = probe()
            if v:
                lang = v
                break
        except Exception:
            continue
    return {
        "text": text,
        "language": lang,
        "language_name": SUPPORTED.get(lang, lang) if lang else None,
        "language_confidence": None,
        "duration_s": None,
        "ok": True,
        "backend": "gemini",
    }


def _transcribe_whisper(path: str) -> dict:
    model = load()
    segments, info = model.transcribe(
        path, beam_size=1, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400})
    text = " ".join(s.text.strip() for s in segments).strip()
    return {
        "text": text,
        "language": info.language,
        "language_name": SUPPORTED.get(info.language, info.language),
        "language_confidence": round(float(info.language_probability), 3),
        "duration_s": round(float(info.duration), 1),
        "ok": bool(text),
        "backend": "whisper",
    }


def transcribe(path: str) -> dict:
    """-> {text, language, language_name, language_confidence, duration_s, ok, backend}

    Gemini first when it can plausibly help; Whisper is the floor everything
    else falls back to, the same shape as extract.apply_floor() being a floor
    under the LLM -- the fast, dumb, reliable path never goes away."""
    if gemini_configured() and not (
            CLOUD_DISABLED_FOR_REHEARSAL and ASR_BACKEND == "auto"):
        try:
            # A hard wall-clock timeout, independent of whatever the SDK's own
            # (version-dependent, unverified here) timeout handling does -- a
            # hung socket must not hang the whole worker pool behind it.
            # shutdown(wait=False): a ThreadPoolExecutor context manager
            # blocks on exit until the submitted call returns, which would
            # silently undo the timeout by waiting for the hang anyway.
            from concurrent.futures import ThreadPoolExecutor
            ex = ThreadPoolExecutor(max_workers=1)
            try:
                return ex.submit(_transcribe_gemini, path).result(timeout=GEMINI_TIMEOUT_S)
            finally:
                ex.shutdown(wait=False)
        except Exception as e:
            gemini_error = str(e)
        try:
            r = _transcribe_whisper(path)
            r["gemini_error"] = gemini_error
            return r
        except Exception as e:
            return {"text": "", "language": None, "language_name": None,
                    "language_confidence": 0.0, "duration_s": 0.0,
                    "ok": False, "backend": None,
                    "error": f"gemini: {gemini_error}; whisper: {e}"}
    try:
        return _transcribe_whisper(path)
    except Exception as e:
        # A failed transcription is a level-2 event, not a crash: the operator
        # types what they heard and the rest of the pipeline is unchanged.
        return {"text": "", "language": None, "language_name": None,
                "language_confidence": 0.0, "duration_s": 0.0,
                "ok": False, "backend": None, "error": str(e)}
