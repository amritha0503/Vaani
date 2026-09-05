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
import json
import os
import threading
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MODEL_SIZE = os.environ.get("VAANI_ASR_MODEL", "small")   # base | small | medium
_model = None
_lock = threading.Lock()

# auto: Gemini first if configured & reachable, Whisper on any failure.
# gemini: same, but never silently skip Gemini for a rehearsal network cut --
#         useful for testing the integration itself.
# whisper: local only, always -- what a genuinely offline box must fall back to.
ASR_BACKEND = os.environ.get("VAANI_ASR_BACKEND", "auto").lower()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("VAANI_GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_TIMEOUT_S = float(os.environ.get("VAANI_GEMINI_TIMEOUT_S", "20"))

# Set by main.py's /demo/cut-network -- rehearsing "the cable is pulled" must
# actually stop the one thing in this file that needs a network, not just
# relabel the badge. Local Whisper does not care either way.
CLOUD_DISABLED_FOR_REHEARSAL = False

_gemini_client = None

# Whisper reports ISO codes; these are the ones the control room cares about.
SUPPORTED = {"en": "English", "ml": "Malayalam", "hi": "Hindi",
             "ta": "Tamil", "te": "Telugu", "kn": "Kannada"}

# Known languages supported by Gemini with high transcription accuracy
GEMINI_LANGUAGES = {"en", "ml", "hi", "kn", "te", "mr", "or", "pa", "ta"}

KNOWN_WHISPER_HALLUCINATIONS = {
    "come on, let's go and have a rest.",
    "thank you for watching",
    "please subscribe",
    "subtitles by",
    "transcript by",
    "thanks for watching",
    "see you next time",
}


def _is_whisper_hallucination(text: str) -> bool:
    t = text.strip().lower().rstrip(".")
    return any(h in t for h in ["come on, let's go and have a rest", "thank you for watching", "please subscribe"])


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
    """One upload, one transcription + translation call via Gemini 3.6 Flash.
    Whatever this raises, transcribe() catches and falls back to local Whisper."""
    client = _gemini()
    from google.genai import types
    audio_file = client.files.upload(file=path)
    try:
        prompt = (
            "You are an emergency triage speech recognition and translation system.\n"
            "1. Transcribe the audio clip verbatim in its original spoken language and script (e.g. Malayalam script for Malayalam, Devanagari for Hindi, etc.).\n"
            "2. Detect the ISO 639-1 language code (e.g. 'ml', 'hi', 'ta', 'te', 'kn', 'en').\n"
            "3. Provide an accurate, faithful English translation of what was spoken.\n"
            "If the audio is silent or unintelligible, set text to '' and text_english to ''.\n\n"
            "Return ONLY a JSON object with keys:\n"
            '{"text": "<verbatim transcript in native script>", "language": "<iso code>", "text_english": "<English translation>"}'
        )
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[audio_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0
            ),
        )
        raw = (getattr(response, "text", None) or "").strip()
        if not raw:
            raise RuntimeError("gemini returned no text")
        data = json.loads(raw)
        text = (data.get("text") or "").strip()
        lang = (data.get("language") or "").strip().lower() or None
        text_english = (data.get("text_english") or "").strip() or text
        return {
            "text": text,
            "text_english": text_english,
            "language": lang,
            "language_name": SUPPORTED.get(lang, lang) if lang else None,
            "language_confidence": 0.95 if text else 0.0,
            "duration_s": None,
            "ok": bool(text),
            "backend": "gemini",
        }
    finally:
        try:
            client.files.delete(name=audio_file.name)
        except Exception:
            pass


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
        gemini_error = None
        try:
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
            if gemini_error:
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
        return {"text": "", "language": None, "language_name": None,
                "language_confidence": 0.0, "duration_s": 0.0,
                "ok": False, "backend": None, "error": str(e)}


# ---------------------------------------------------------------- translation
def _translate_whisper(path: str) -> str:
    """Whisper's task='translate' outputs English as fallback when offline."""
    model = load()
    segments, _info = model.transcribe(
        path, beam_size=1, task="translate",
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400})
    return " ".join(s.text.strip() for s in segments).strip()


def _translate_gemini_text(text: str, source_lang: str | None = None) -> str:
    """Ask Gemini to translate text to English with high accuracy."""
    client = _gemini()
    lang_hint = f" (source language: {source_lang})" if source_lang else ""
    from google.genai import types
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=(
            f"Translate the following emergency call text into clear, direct English. "
            f"Return ONLY the English translation, without explanation or commentary.{lang_hint}\n\n{text}"
        ),
        config=types.GenerateContentConfig(temperature=0.0),
    )
    result = (getattr(response, "text", None) or "").strip()
    if not result:
        raise RuntimeError("gemini returned no translation")
    return result


def translate_to_english(text: str, language: str | None, path: str | None = None) -> str:
    """Translate text (or re-process audio) to English.

    If the language is already English, returns the text unchanged.
    When Gemini is configured and reachable, it translates text accurately.
    Falls back to Whisper's native translate task when offline or unconfigured.
    Returns the original text on any failure — a missing translation is
    cosmetic, a crash is not."""
    if not text or language == "en":
        return text

    # Try Gemini translation first when configured (far superior translation accuracy)
    if gemini_configured() and not (
            CLOUD_DISABLED_FOR_REHEARSAL and ASR_BACKEND == "auto"):
        try:
            from concurrent.futures import ThreadPoolExecutor
            ex = ThreadPoolExecutor(max_workers=1)
            try:
                result = ex.submit(_translate_gemini_text, text, language).result(
                    timeout=GEMINI_TIMEOUT_S)
                if result:
                    return result
            finally:
                ex.shutdown(wait=False)
        except Exception:
            pass

    # Fall back to Whisper audio-based translation (offline mode)
    if path:
        try:
            result = _translate_whisper(path)
            if result and not _is_whisper_hallucination(result):
                return result
        except Exception:
            pass

    # No translation available — return original
    return text


def transcribe_and_translate(path: str) -> dict:
    """Transcribe audio, then translate to English if not already English.

    Returns the standard transcription dict with an added 'text_english' key."""
    result = transcribe(path)
    if not result.get("ok") or not result.get("text"):
        result["text_english"] = result.get("text", "")
        return result
    if result.get("text_english"):
        return result
    result["text_english"] = translate_to_english(
        result["text"], result.get("language"), path)
    return result

