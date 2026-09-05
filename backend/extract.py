"""Transcript -> structured fields.

The architectural claim: the language model classifies and narrates and NEVER
produces a quantity. Every field below is an enum or a boolean, so there is no
slot in which the model could emit a number that reaches the ranking maths.
A violation is rejected, not repaired -- the call falls to the keyword spotter.
"""
import json
import time
import urllib.error
import urllib.request

# 127.0.0.1, never "localhost". On Windows the name resolves to ::1 first,
# Ollama listens on IPv4 only, and every single call pays ~2 s waiting for
# that attempt to fail before falling back -- measured 2.07 s vs 0.019 s,
# on every extraction AND every health probe.
OLLAMA = "http://127.0.0.1:11434/api/chat"
OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"
MODEL = "qwen2.5:3b"        # 3B is plenty; the schema does the heavy lifting

SCHEMA = {
    "type": "object",
    "properties": {
        "hazard_class": {"type": "string",
                         "enum": ["flood", "landslide", "structural", "medical", "fire", "other"]},
        "severity_band": {"type": "integer", "enum": [0, 1, 2, 3]},
        "people_affected": {"type": "string", "enum": ["1", "2-5", "6-20", "20+"]},
        "trapped": {"type": "boolean"},
        "medical_critical": {"type": "boolean"},
        "vulnerable": {"type": "array", "items": {
            "type": "string",
            "enum": ["child", "elderly", "disabled", "pregnant", "injured"]}},
        "access_constraint": {"type": "string", "enum": [
            "road_blocked", "water_on_road", "no_vehicle_access", "none", "unknown"]},
        "landmark_text": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": list(("hazard_class severity_band people_affected trapped medical_critical "
                      "vulnerable access_constraint landmark_text confidence").split()),
}

PROMPT = (
    "Triage this emergency call transcript. Fill the structured fields only.\n"
    "severity_band: 0 none, 1 minor, 2 serious, 3 immediate danger to life.\n"
    "Copy any spoken landmark verbatim into landmark_text. Invent nothing.\n"
    "If you are unsure of any field, set confidence to low.\n\nTranscript:\n"
)

# The offline / noisy-audio path. Add your languages here.
KEYWORDS = {
    "trapped":   ["trapped", "stuck", "can't get out", "cannot get out", "save us", "rescue us", "help us",
                  "फंस", "കുടുങ്ങി", "kudungi", "koodungi", "reksik", "rakshik", "rekshik"],
    # Deliberately NOT "under water" -- callers overwhelmingly use that phrase
    # for a submerged road or ground floor, not a submerged person, and it
    # pins medical_critical + band 3 below. "going under" is said of a person.
    "drowning":  ["drowning", "going under", "went under", "swept away", "डूब", "മുങ്ങ", "mungi", "moongi"],
    "flood":     ["water", "flood", "rising", "पानी", "बाढ़", "വെള്ളം", "vellam", "valam", "pongi", "kayarunnu", "uyarunnu"],
    "landslide": ["landslide", "mudslide", "hill came down", "भूस्खलन", "ഉരുൾ", "urul"],
    "medical":   ["bleeding", "unconscious", "not breathing", "heart", "injured", "hospital"],
    "child":     ["child", "baby", "kid", "बच्चा", "കുട്ടി", "kutti"],
    "elderly":   ["old man", "old woman", "elderly", "grandmother", "grandfather"],
    "roof":      ["roof", "terrace", "first floor", "upstairs", "മേൽക്കൂര", "छत", "ऊपर"],
}


class SchemaViolation(ValueError):
    pass


def validate(d: dict) -> dict:
    """Reject, never repair."""
    out = {}
    for key, spec in SCHEMA["properties"].items():
        if key not in d:
            raise SchemaViolation(f"missing field: {key}")
        v = d[key]
        if spec["type"] == "array":
            allowed = spec["items"]["enum"]
            if not isinstance(v, list) or any(x not in allowed for x in v):
                raise SchemaViolation(f"bad array: {key}={v!r}")
        elif spec["type"] == "boolean":
            if not isinstance(v, bool):
                raise SchemaViolation(f"not a boolean: {key}={v!r}")
        elif spec["type"] == "integer":
            # bool is a subclass of int in Python, so True == 1 would slip
            # through an ordinal enum unnoticed. Reject the type, not just the
            # value -- severity_band is the field that reaches the sort key.
            if type(v) is not int or v not in spec["enum"]:
                raise SchemaViolation(f"not an ordinal in {spec['enum']}: {key}={v!r}")
        elif "enum" in spec and v not in spec["enum"]:
            raise SchemaViolation(f"outside enum: {key}={v!r}")
        elif spec["type"] == "string" and not isinstance(v, str):
            raise SchemaViolation(f"not a string: {key}={v!r}")
        out[key] = v
    return out


def _ask_model(transcript: str, timeout: float = 45.0) -> dict:
    # 45 s, not 20: a 3B model on a CPU takes ~9 s per call and a queued one
    # takes longer. A timeout that fires under load does not degrade gracefully,
    # it just silently throws away the model's answer on the busiest calls.
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT + transcript}],
        "format": SCHEMA,            # Ollama constrains decoding to the schema
        "stream": False,
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(json.loads(r.read())["message"]["content"])


def keyword_extract(transcript: str) -> dict:
    """Level 1 of the ladder. Conservative by design: when in doubt it escalates,
    because under-triaging a real emergency is the expensive error."""
    low = transcript.lower()
    hit = lambda k: any(w.lower() in low for w in KEYWORDS[k])

    trapped = hit("trapped") or hit("roof")
    drowning = hit("drowning")
    hazard = ("landslide" if hit("landslide") else
              "medical" if hit("medical") else
              "flood" if hit("flood") else "other")
    return {
        "hazard_class": hazard,
        "severity_band": 3 if (trapped or drowning) else (2 if hazard != "other" else 1),
        "people_affected": "1",
        "trapped": trapped,
        "medical_critical": drowning or hit("medical"),
        "vulnerable": [v for v in ("child", "elderly") if hit(v)],
        "access_constraint": "water_on_road" if hit("flood") else "unknown",
        "landmark_text": "",
        "confidence": "low",
    }


def apply_floor(llm: dict, kw: dict) -> tuple[dict, list[str]]:
    """The keyword spotter is a FLOOR under the model, never a ceiling.

    The model reads the sentence, so it wins on hazard class, headcount band and
    the landmark span. But it is a 3B model and it does not read Malayalam as
    well as it reads English: given "water is rising, we are trapped, there is a
    child" in Malayalam it returned band 0, and a trapped family with a child
    fell to the bottom of the queue. The spotter, which is just a word list, had
    it right.

    So the model may ESCALATE a call and may never DE-ESCALATE one below what
    the deterministic spotter found. Same shape as the terrain rule: the clever
    component can promote, only the dumb reliable one sets the floor.
    """
    out, raised = dict(llm), []
    if kw["severity_band"] > llm["severity_band"]:
        out["severity_band"] = kw["severity_band"]
        raised.append(f"band {llm['severity_band']}->{kw['severity_band']}")
    if llm["hazard_class"] == "other" and kw["hazard_class"] != "other":
        # A model that cannot read the language falls back to "other". That is
        # an absence of information, not a finding, so it must not erase one.
        out["hazard_class"] = kw["hazard_class"]
        raised.append(f"hazard other->{kw['hazard_class']}")
    for flag in ("trapped", "medical_critical"):
        if kw[flag] and not llm[flag]:
            out[flag] = True
            raised.append(flag)
    extra = [v for v in kw["vulnerable"] if v not in llm["vulnerable"]]
    if extra:
        out["vulnerable"] = list(llm["vulnerable"]) + extra
        raised.append("+".join(extra))
    return out, raised


def extract(transcript: str) -> tuple[dict, str, str | None]:
    """Returns (fields, extractor, note). Never raises -- a call always gets triaged."""
    kw = keyword_extract(transcript)
    try:
        fields = validate(_ask_model(transcript))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return kw, "keyword", f"model unreachable: {e}"
    except (SchemaViolation, json.JSONDecodeError, KeyError) as e:
        return kw, "keyword", f"schema violation: {e}"
    merged, raised = apply_floor(fields, kw)
    if raised:
        return merged, "llm+floor", "spotter floor raised: " + ", ".join(raised)
    return merged, "llm", None


_PROBE = {"at": 0.0, "alive": False}
PROBE_EVERY_S = 2.0


def model_alive(timeout: float = 1.5, max_age_s: float = PROBE_EVERY_S) -> bool:
    """Is the model reachable? Cached for PROBE_EVERY_S -- the ladder is a
    2-second probe loop, not a probe per render.

    It has to be cached, not just cheap: degradation() is called by every
    /queue request AND by the SSE generator once a second, and this is a
    BLOCKING call inside the event loop. Uncached it starved the whole app --
    every endpoint answering in 7-15 s while the machine sat at 21% CPU."""
    now = time.time()
    if now - _PROBE["at"] < max_age_s:
        return _PROBE["alive"]
    try:
        urllib.request.urlopen(OLLAMA_TAGS, timeout=timeout).read(1)
        _PROBE["alive"] = True
    except Exception:
        _PROBE["alive"] = False
    _PROBE["at"] = now
    return _PROBE["alive"]
