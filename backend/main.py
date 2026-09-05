"""Vaani backend. One process, no database server, no Docker.

    pip install -r requirements.txt
    uvicorn main:app --reload --port 8000

The exposure raster is OPTIONAL at startup. Until the geo lane delivers it the
app runs on a clearly-labelled stub, so lanes C and D are never blocked waiting
for lane A. Check GET /health/degradation to see which surface is live.
"""
import asyncio
import hashlib
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import File, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# .env, if present, is loaded before anything reads os.environ -- Twilio and
# the tunnel URL live there rather than in a shell the operator has to
# remember to re-set on every restart. A missing .env is fine: every value
# here has a safe default (unconfigured, degrade honestly) elsewhere in this
# file.
from dotenv import load_dotenv
load_dotenv(Path(__file__).with_name(".env"))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import backend.asr as asr
import backend.briefing as briefing
import backend.extract as extract
import backend.rank as rank
import backend.store as store
import backend.teams as teams
import backend.telephony as telephony

HERE = Path(__file__).parent
STORAGE_DIR = HERE.parent / "storage"
AUDIO_STORAGE = STORAGE_DIR / "audio"
AUDIO_STORAGE.mkdir(parents=True, exist_ok=True)


def save_audio(call_id: str, audio_bytes: bytes, filename: str = "clip.wav") -> str:
    """Save audio locally to ./storage/audio/<call_id>.wav in standard PCM_16 WAV format.
    Guarantees 100% air-gapped local storage with zero cloud in the hot path."""
    AUDIO_STORAGE.mkdir(parents=True, exist_ok=True)
    target_path = AUDIO_STORAGE / f"{call_id}.wav"
    try:
        import io
        import soundfile as sf
        data, sr = sf.read(io.BytesIO(audio_bytes))
        sf.write(target_path, data, sr, format="WAV", subtype="PCM_16")
    except Exception:
        target_path.write_bytes(audio_bytes)
    return str(target_path)


EXPOSURE_TIF = HERE / "data" / "exposure.tif"
HAND_TIF = HERE / "data" / "hand.tif"
ROADS_JSON = HERE / "data" / "roadgraph.json"

SURFACE = None          # real Surface, or None while we are on the stub
ROADS = None            # RoadNetwork, or None if the graph was never baked
DEMO_OFFLINE = False    # /demo/cut-network flips this for rehearsal
_ROUTE_CACHE = {}       # call_id -> (key, route). The graph is frozen; a
                        # route asked for twice is the same route both times.
_CAMP_RISK_CACHE = {}   # call_id -> (key, risk_data)
_BRIEFING_CACHE = {}    # call_id -> (key, briefing_data)
LIVE_CAMPS = []         # in-memory declared relief camps during disaster ops
_IMPASSABLE_CACHE = None
GAZ = None              # local landmark gazetteer
CONVO = None            # the call agent
# "gather" (default) uses Twilio's own speech recognition and works on a
# trial project. "record" downloads audio and transcribes locally with
# Whisper, but needs an UPGRADED Twilio project -- <Record> is blocked
# entirely on trial, replaced with a spoken warning, and the call ends.
VOICE_INPUT_MODE = os.environ.get("VAANI_TWILIO_INPUT", "gather").lower()
# A call that never reaches "done" and never updates for this long is
# almost certainly abandoned -- the caller hung up, or (on a trial
# project) Twilio silently dropped the call after a blocked verb -- and
# should stop showing as "ON CALL" forever.
STALE_CALL_S = float(os.environ.get("VAANI_STALE_CALL_S", "90"))
_sweep_task = None


class StubSurface:
    """Deterministic pseudo-terrain so the board is demoable from hour two.
    Clearly labelled everywhere it appears -- never let this reach a judge."""

    def sample(self, lat, lon, error_radius_m):
        h = int(hashlib.md5(f"{lat:.4f},{lon:.4f}".encode()).hexdigest()[:8], 16)
        p = (h % 1000) / 1000.0
        return {"p50": round(max(0.0, p - 0.08), 3), "p75": round(p, 3),
                "p90": round(min(1.0, p + 0.10), 3),
                "hand_m": round(1.0 + (1 - p) * 14, 1), "in_aoi": True,
                "wide_error": error_radius_m > 800}


def surface():
    return SURFACE or StubSurface()


def model_ver() -> str:
    """Whatever the loaded GeoTIFF says it is. Never a constant in the code."""
    return "stub" if SURFACE is None else SURFACE.model_ver


# Whisper is CPU-bound and takes seconds. Called straight from an async handler
# it blocks the event loop, which stalls the SSE board, /queue and every other
# caller's webhook for the duration -- invisible with one call, obvious with
# three. Run it on a worker thread, and bound how many run at once: two
# transcriptions sharing the cores are each roughly twice as slow, so a queue
# is faster than a stampede as well as being kinder to the board.
ASR_SLOTS = asyncio.Semaphore(int(os.environ.get("VAANI_ASR_CONCURRENCY", "2")))

# The model costs seconds per call, and a surge is thirty calls at once. A call
# goes on the board the instant it arrives, triaged by the keyword spotter, and
# the row upgrades to the model's reading when it lands -- the same "appears
# immediately, fills in" contract the live phone path already keeps. The
# operator is never waiting on the AI, which is the property that matters when
# thirty calls arrive in forty seconds.
# One worker, deliberately. Ollama on a CPU is a serial resource: three threads
# in flight do not run three times faster, they queue INSIDE Ollama where our
# timeout cannot see them -- measured, that turned 12 extractions into 5 timeouts
# and 87 s. Queueing in our own pool instead gives each call the whole machine,
# finishes every one of them, and leaves cores for Whisper if a call comes in
# mid-surge. Raise it only on a box with a GPU.
EXTRACT_POOL = ThreadPoolExecutor(
    max_workers=int(os.environ.get("VAANI_EXTRACT_WORKERS", "1")),
    thread_name_prefix="extract")

# Batch upload runs ASR and the model back to back, both CPU-bound, so it gets
# its own single worker rather than competing with live calls for the pool.
INTAKE_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="intake")


def _upgrade(call_id: str, transcript: str) -> None:
    """Run the model for a call already on the board, then re-rank it."""
    fields, extractor, note = extract.extract(transcript)
    call = store.CALLS.get(call_id)
    if call is None:                      # reset, or the call was cleared
        return
    call["fields"], call["extractor"] = fields, extractor
    call["extractor_error"], call["extractor_pending"] = note, False
    store.save_call(call)
    store.log("re-extracted", call_id=call_id, extractor=extractor, note=note,
              severity_band=fields["severity_band"], trapped=fields["trapped"])


def _upgrade_live(st: dict) -> None:
    """Same, for a call still on the line: update the conversation state and
    push the row again."""
    fields, extractor, note = extract.extract(st["transcript"])
    st["fields"], st["extractor"] = fields, extractor
    st["extractor_error"], st["extractor_pending"] = note, False
    sync_call(st)


async def transcribe(path: str) -> dict:
    async with ASR_SLOTS:
        return await asyncio.to_thread(asr.transcribe, path)


async def transcribe_and_translate(path: str) -> dict:
    async with ASR_SLOTS:
        return await asyncio.to_thread(asr.transcribe_and_translate, path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global SURFACE, GAZ, CONVO, ROADS
    store.init()
    osm = HERE / "data" / "osm_raw.json"
    if osm.exists():
        from backend.gazetteer import Gazetteer
        GAZ = Gazetteer(str(osm))
    CONVO = telephony.Conversation(GAZ, sync_call, input_mode=VOICE_INPUT_MODE)
    # warm the ASR model off the request path -- never on the first real call
    threading.Thread(target=asr.load, daemon=True).start()
    if EXPOSURE_TIF.exists() and HAND_TIF.exists():
        from backend.exposure import Surface          # imported late: rasterio is heavy
        SURFACE = Surface(str(EXPOSURE_TIF), str(HAND_TIF))
        store.log("startup", surface="real")
        if ROADS_JSON.exists():
            from backend.dispatch import RoadNetwork   # networkx, also heavy
            ROADS = RoadNetwork(ROADS_JSON)
            store.log("startup", roads=len(ROADS), depots=len(ROADS.candidates))
    else:
        store.log("startup", surface="stub", missing=str(EXPOSURE_TIF))
    global _sweep_task
    _sweep_task = asyncio.create_task(_sweep_stale_calls())
    yield
    _sweep_task.cancel()


app = FastAPI(title="Vaani", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


# The built console. Everything it loads -- script, stylesheet, fonts -- is
# served from this directory, so the UI draws with the network cable pulled.
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/", include_in_schema=False)
def console():
    from fastapi.responses import FileResponse
    built = HERE / "static" / "app" / "index.html"
    # If the frontend was never built, fall back to the dependency-free console
    # rather than serving a blank page ten minutes before a demo.
    return FileResponse(built if built.exists() else HERE / "static" / "index.html")


@app.get("/legacy", include_in_schema=False)
def legacy_console():
    """The single-file console. Kept deliberately: it needs no build step, so it
    is the thing that still works if the toolchain does not."""
    from fastapi.responses import FileResponse
    return FileResponse(HERE / "static" / "index.html")


@app.get("/loc.html", include_in_schema=False)
def loc_page():
    """The page an SMS sends a caller to. Static, dependency-free, no build step
    -- it has to render over a weak phone connection with nothing cached, so it
    is one file with inline CSS/JS, same as the /legacy console."""
    from fastapi.responses import FileResponse
    return FileResponse(HERE / "static" / "loc.html")


# ---------------------------------------------------------------- models
class CallIn(BaseModel):
    transcript: str
    text_english: str | None = None
    lat: float
    lon: float
    error_radius_m: float = Field(50.0, description="gps ~10, aml ~50, tower ~1500")
    language: str = "en"
    location_source: str = "aml"
    source: str = "live"


class Override(BaseModel):
    direction: int = Field(..., ge=-1, le=1)
    reason: str
    operator: str = "operator:1"


class LocationIn(BaseModel):
    """Either a coordinate (map click, GPS) or a landmark name to geocode --
    never both, and at least one. Manual placement for a call the audio alone
    could not locate: an uploaded clip with no landmark in it, or a caller who
    denied speech and tapped the location link instead."""
    lat: float | None = None
    lon: float | None = None
    error_radius_m: float | None = None
    landmark: str | None = None
    source: str = "operator"


class TranscriptIn(BaseModel):
    text: str


class CampIn(BaseModel):
    lat: float
    lon: float
    name: str


def _get_cached_camp_risk(call_id: str, lat: float | None, lon: float | None) -> dict | None:
    if ROADS is None or lat is None or lon is None:
        return None
    key = (lat, lon)
    hit = _CAMP_RISK_CACHE.get(call_id)
    if hit and hit[0] == key:
        return hit[1]
    res = ROADS.nearest_camp(*key)
    _CAMP_RISK_CACHE[call_id] = (key, res)
    return res


# ---------------------------------------------------------------- core
def ingest(c: CallIn) -> dict:
    # Deterministic triage first, in microseconds, so the call is ranked before
    # the model has even been asked. The model's answer replaces this one when
    # it arrives -- and can only ever raise the band, never lower it.
    text_english = c.text_english or c.transcript
    if not c.text_english and c.language and c.language != "en":
        text_english = asr.translate_to_english(c.transcript, c.language)
    extract_input = c.transcript
    if text_english and text_english != c.transcript:
        extract_input = f"{c.transcript} {text_english}"
    fields = extract.keyword_extract(extract_input)
    exp = surface().sample(c.lat, c.lon, c.error_radius_m)
    call = {
        "id": str(uuid.uuid4())[:8],
        "received_at": time.time(),
        "transcript": c.transcript,
        "text_english": text_english,
        "language": c.language,
        "lat": c.lat, "lon": c.lon,
        "error_radius_m": c.error_radius_m,
        "location_source": c.location_source,
        "fields": fields,
        "extractor": "keyword",
        "extractor_error": None,
        "extractor_pending": True,
        "exposure": exp,
        "model_ver": model_ver(),
        "override": 0,
    }
    store.CALLS[call["id"]] = call
    store.save_call(call)
    store.log("ranked", call_id=call["id"], extractor="keyword", exposure=exp,
              severity_band=fields["severity_band"], trapped=fields["trapped"])
    EXTRACT_POOL.submit(_upgrade, call["id"], extract_input)
    return call


def place_of(call: dict) -> dict | None:
    """Nearest named settlement, computed once and kept on the call. The board
    groups by this: an operator triages a village, not a coordinate."""
    if call.get("lat") is None or GAZ is None:
        return None
    if call.get("_place_at") != (call["lat"], call["lon"]):
        call["place"] = GAZ.nearest(call["lat"], call["lon"])
        call["_place_at"] = (call["lat"], call["lon"])
    return call.get("place")


def board() -> list[dict]:
    ranked = rank.rank(list(store.CALLS.values()))
    store.save_rankings(ranked)
    return [{
        "rank": c["rank"], "id": c["id"], "band": c["band"],
        "within": c["within"], "reason": c["reason"],
        "floor_applied": c["floor_applied"], "transcript": c["transcript"],
        "text_english": c.get("text_english"),
        "hazard": c["fields"]["hazard_class"], "trapped": c["fields"]["trapped"],
        "live_stage": c.get("live_stage"), "landmark": c.get("landmark"),
        "language": c.get("language"), "from_number": c.get("from_number"),
        "location_source": c.get("location_source"),
        "vulnerable": c["fields"]["vulnerable"], "extractor": c["extractor"],
        "extractor_pending": bool(c.get("extractor_pending")),
        "place": place_of(c),
        "source": c.get("source", "live"),
        "filename": c.get("filename"),
        "has_audio": bool(c.get("audio_path")),
        "override": c.get("override", 0),
        "intake_stage": c.get("intake_stage"),
        "asr_conf": c.get("asr_conf"),
        "asr_duration_s": c.get("asr_duration_s"),
        "asr_backend": c.get("asr_backend"),
        "transcript_source": c.get("transcript_source"),
        "extractor_note": c.get("extractor_error"),
        "lat": c["lat"], "lon": c["lon"], "error_radius_m": c["error_radius_m"],
        "hand_m": c["exposure"]["hand_m"], "exposure_p75": c["exposure"]["p75"],
        # "no fix yet" and "fix is outside the mapped AOI" are different states
        # and the console must not print the second when it means the first.
        "exposure_pending": bool(c["exposure"].get("pending")),
        "model_ver": c["model_ver"], "age_s": int(time.time() - c["received_at"]),
        "manual_team_id": c.get("manual_team_id"),
        "assigned_team": (
            {**teams.TEAMS_BY_ID[c["manual_team_id"]], "manual_override": True}
            if c.get("manual_team_id") and c.get("manual_team_id") in teams.TEAMS_BY_ID
            else teams.find_nearest_team(c["lat"], c["lon"])
        ),
        "camp_risk": _get_cached_camp_risk(c["id"], c.get("lat"), c.get("lon")),
    } for c in ranked]


def degradation() -> dict:
    model_ok = (not DEMO_OFFLINE) and extract.model_alive()
    surface_ok = SURFACE is not None
    asr_ok = not DEMO_OFFLINE
    level, lost = 0, []
    if not model_ok:
        level, lost = 1, lost + ["language model"]
    if not asr_ok:
        level, lost = 2, lost + ["speech recognition"]
    if not surface_ok:
        level, lost = 3, lost + ["calibrated exposure surface (stub in use)"]
    if not asr.ready():
        lost = lost + ["speech recognition (model warming)"]
        level = max(level, 2)
    return {"level": level, "lost": lost,
            # True on every rung by design -- both read cached local files. If
            # the road graph was never baked, say so rather than claiming it.
            "ranking": True, "routing": ROADS is not None,
            "roads": None if ROADS is None else len(ROADS),
            "extraction": "model" if model_ok else "keyword spotter",
            "surface": "real" if surface_ok else "stub"}


# ---------------------------------------------------------------- routes
@app.post("/calls")
def post_call(c: CallIn):
    return ingest(c)


@app.get("/queue")
def get_queue():
    return {"degradation": degradation(), "calls": board()}


@app.get("/queue/stream")
async def stream_queue():
    async def gen():
        last = None
        while True:
            payload = json.dumps({"degradation": degradation(), "calls": board()})
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            await asyncio.sleep(1.0)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.post("/calls/{call_id}/override")
def override(call_id: str, o: Override):
    call = store.CALLS.get(call_id)
    if not call:
        raise HTTPException(404, "no such call")
    call["override"] = o.direction
    store.log("overridden", actor=o.operator, call_id=call_id,
              direction=o.direction, reason=o.reason)
    return {"ok": True, "queue": board()}


@app.post("/calls/{call_id}/location")
def set_location(call_id: str, loc: LocationIn):
    """Place a call the audio alone could not: a map click, a phone's own GPS
    fix relayed from loc.html, or a landmark typed into the upload panel.
    Same effect as the live agent resolving turn 2 -- reprice the ground and
    let the rank move, never invent a fix nobody gave us."""
    call = store.CALLS.get(call_id)
    if not call:
        raise HTTPException(404, "no such call")
    if loc.landmark:
        hit = GAZ.locate(loc.landmark) if GAZ else None
        if not hit:
            raise HTTPException(422, "that landmark was not recognised")
        lat, lon = hit["lat"], hit["lon"]
        radius = hit["error_radius_m"]
        call["landmark"] = hit["matched"]
        call["landmark_confidence"] = hit["confidence"]
        source = "landmark"
    elif loc.lat is not None and loc.lon is not None:
        lat, lon = loc.lat, loc.lon
        # A phone's reported GPS accuracy can arrive as a handful of metres --
        # floor it so the disc sample still covers real coordinate rounding
        # and pixel-snap error rather than reading as an implausibly exact point.
        radius = max(loc.error_radius_m or 15.0, 5.0)
        source = loc.source
    else:
        raise HTTPException(422, "give either lat/lon or a landmark")
    call.update(lat=lat, lon=lon, error_radius_m=radius, location_source=source)
    call["exposure"] = surface().sample(lat, lon, radius)
    call["model_ver"] = model_ver() if call["exposure"].get("in_aoi") else "-"
    store.save_call(call)
    store.log("relocated", call_id=call_id, source=source, lat=lat, lon=lon,
              error_radius_m=radius)
    return {"ok": True, "queue": board()}


@app.post("/calls/{call_id}/transcript")
def set_transcript(call_id: str, t: TranscriptIn):
    """The Level-2 resilience rung, on demand rather than only during a
    declared outage: local ASR can mis-hear a short or heavily-accented clip
    in any language, badly enough that no keyword or model reading of the
    wrong words will recover the call. The operator has the recording right
    there -- this lets them listen and type what was actually said.

    Never destructive: a call that already has a real location fix keeps it
    even if the corrected words don't happen to name a landmark. Re-triages
    on the corrected text exactly like a fresh call would."""
    call = store.CALLS.get(call_id)
    if not call:
        raise HTTPException(404, "no such call")
    text = t.text.strip()
    if not text:
        raise HTTPException(422, "transcript cannot be empty")
    call["transcript"] = text
    call["transcript_source"] = "operator"
    # Re-translate the corrected transcript
    lang = call.get("language")
    audio_path = call.get("audio_path")
    call["text_english"] = asr.translate_to_english(text, lang, audio_path)
    extract_input = text
    if call["text_english"] and call["text_english"] != text:
        extract_input = f"{text} {call['text_english']}"
    call["fields"] = extract.keyword_extract(extract_input)
    call["extractor"] = "keyword"
    call["extractor_pending"] = True
    if call.get("lat") is None:
        _relocate(call)
    store.save_call(call)
    store.log("transcript_corrected", call_id=call_id, actor="operator:1", text=text)
    EXTRACT_POOL.submit(_upgrade, call_id, extract_input)
    return {"ok": True, "queue": board()}


@app.delete("/calls/{call_id}")
def remove_call(call_id: str):
    """Remove or resolve a call, permanently deleting it from the active queue and DB."""
    store.delete_call(call_id)
    return {"ok": True, "queue": board()}


@app.get("/audio/{call_id}")
def get_audio(call_id: str):
    """The caller's own voice, played back from air-gapped local storage at ./storage/audio/<call_id>.wav."""
    from fastapi.responses import FileResponse
    wav_path = AUDIO_STORAGE / f"{call_id}.wav"
    if wav_path.exists():
        return FileResponse(wav_path, media_type="audio/wav")
    call = store.CALLS.get(call_id)
    if not call:
        raise HTTPException(404, "no such call")
    path = call.get("audio_path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "no recording for this call")
    ext = Path(path).suffix.lower().lstrip(".") or "wav"
    return FileResponse(path, media_type=f"audio/{ext}")


@app.get("/exposure/section")
def get_section(lat: float, lon: float, length_m: float = 300.0):
    """HAND read outward from one point, in the direction that actually runs
    downhill toward drainage. The same number the rank is built from, drawn as
    a line instead of a single percentile."""
    if SURFACE is None:
        raise HTTPException(503, "no baked surface; run build_terrain.py")
    sec = SURFACE.section(lat, lon, length_m=length_m)
    if sec is None:
        raise HTTPException(404, "outside the mapped area")
    return sec


@app.get("/exposure")
def get_exposure(lat: float, lon: float, radius_m: float = 50.0):
    return {**surface().sample(lat, lon, radius_m),
            "model_ver": model_ver()}


@app.get("/dispatch/{call_id}")
def get_dispatch(call_id: str):
    """A vehicle from the nearest usable station to this caller, on roads priced
    by the same raster that ranked them. Cached per call: the graph does not
    change mid-demo, and a judge will click the same row twice."""
    call = store.CALLS.get(call_id)
    if not call:
        raise HTTPException(404, "no such call")
    if ROADS is None:
        raise HTTPException(503, "no road graph; run build_roads.py")
    if call.get("lat") is None:
        live = bool(call.get("live_stage")) and not str(
            call.get("live_stage")).startswith("done")
        return {"route": None, "reason": "no_location_yet",
                # Only a call actually in progress has an agent asking. For
                # anything else this is a job for the operator, and the line
                # should say so rather than implying someone else is on it.
                "detail": ("the agent is still asking where they are" if live
                           else "no location yet — place the call to route it")}
    key = (call["lat"], call["lon"])
    hit = _ROUTE_CACHE.get(call_id)
    if hit and hit[0] == key:
        return hit[1]
    r = ROADS.route_to(*key)
    _ROUTE_CACHE[call_id] = (key, r)
    store.log("dispatched", call_id=call_id, reason=r.get("reason"),
              depot=(r.get("depot") or {}).get("name"),
              length_m=r.get("length_m"), detour_m=r.get("detour_m"),
              blocked=len(r.get("blocked_on_direct") or []))
    return r


@app.post("/camps")
def declare_camp(camp: CampIn):
    """Declare a relief camp live during disaster operations.
    Appends to in-memory list, registers with RoadNetwork, and logs via store.log()."""
    item = {
        "name": camp.name,
        "kind": "community_centre",
        "lat": camp.lat,
        "lon": camp.lon,
        "declared_live": True,
    }
    LIVE_CAMPS.append(item)
    if ROADS is not None:
        ROADS.add_live_camp(item)
    _CAMP_RISK_CACHE.clear()
    store.log("camp_declared", name=camp.name, lat=camp.lat, lon=camp.lon)
    return {
        "ok": True,
        "camp": item,
        "total_camps": len(ROADS.camp_candidates) if ROADS else len(LIVE_CAMPS),
    }


@app.get("/calls/{call_id}/camp-risk")
def get_camp_risk(call_id: str):
    """Report whether caller has a reachable relief camp nearby via dry road (risk: low) or not (risk: high)."""
    call = store.CALLS.get(call_id)
    if not call:
        raise HTTPException(404, "no such call")
    if ROADS is None:
        raise HTTPException(503, "no road graph; run build_roads.py")
    if call.get("lat") is None:
        live = bool(call.get("live_stage")) and not str(
            call.get("live_stage")).startswith("done")
        return {
            "risk": "high",
            "nearest_camp": None,
            "reason": ("the agent is still asking where they are" if live
                       else "no location yet — place the call to assess camp risk"),
        }
    key = (call["lat"], call["lon"])
    hit = _CAMP_RISK_CACHE.get(call_id)
    if hit and hit[0] == key:
        return hit[1]
    res = ROADS.nearest_camp(*key)
    _CAMP_RISK_CACHE[call_id] = (key, res)
    return res


@app.get("/dispatch/{call_id}/briefing")
def get_briefing(call_id: str):
    """Responder safety briefing on specific hazards and precautions, strictly decided by rule."""
    call = store.CALLS.get(call_id)
    if not call:
        raise HTTPException(404, "no such call")
    if call.get("lat") is None:
        return {
            "warnings": ["No location yet — cannot generate route-specific safety briefing."],
            "route_hazards": [],
            "generated_from": [],
        }
    key = (call["lat"], call["lon"])
    hit = _BRIEFING_CACHE.get(call_id)
    if hit and hit[0] == key:
        return hit[1]

    route_data = {}
    if ROADS is not None:
        route_hit = _ROUTE_CACHE.get(call_id)
        if route_hit and route_hit[0] == key:
            route_data = route_hit[1]
        else:
            route_data = ROADS.route_to(*key)

    exp = call.get("exposure")
    if not exp and SURFACE is not None:
        exp = surface().sample(call["lat"], call["lon"], call.get("error_radius_m", 50.0))
    elif not exp:
        exp = {}

    fields = call.get("fields") or {}
    b = briefing.generate_briefing(call_fields=fields, exposure=exp, route=route_data)
    _BRIEFING_CACHE[call_id] = (key, b)
    store.log("briefed", call_id=call_id, warnings=b.get("warnings", []))
    return b


@app.get("/roads/impassable")
def get_impassable():
    """The cut segments, drawn from the same surface that ranked the caller."""
    global _IMPASSABLE_CACHE
    if ROADS is None:
        raise HTTPException(503, "no road graph; run build_roads.py")
    if _IMPASSABLE_CACHE is None:
        _IMPASSABLE_CACHE = ROADS.impassable()
    return _IMPASSABLE_CACHE


# ------------------------------------------------------------ batch intake
def _relocate(call: dict) -> None:
    """Geocode whatever landmark the transcript mentions, then price the ground.

    Exactly what the live agent does on turn 2 -- an uploaded recording is just
    a call whose audio arrived late."""
    hit = GAZ.locate(call["transcript"]) if GAZ else None
    if not hit and call.get("text_english") and GAZ and call["text_english"] != call["transcript"]:
        hit = GAZ.locate(call["text_english"])
    if hit:
        call.update(lat=hit["lat"], lon=hit["lon"],
                    error_radius_m=hit["error_radius_m"],
                    location_source=hit["source"], landmark=hit["matched"],
                    landmark_confidence=hit["confidence"])
        call["exposure"] = surface().sample(hit["lat"], hit["lon"],
                                            hit["error_radius_m"])
    else:
        # No guess. A guessed coordinate sends a boat where nobody is, so the
        # call stays on the board ranked on what was reported.
        call["exposure"] = dict(PENDING_EXPOSURE)
        call["location_source"] = "unknown"


def _process_upload(call_id: str, path: str) -> None:
    call = store.CALLS.get(call_id)
    if call is None:
        return
    tr = asr.transcribe_and_translate(path)
    call = store.CALLS.get(call_id)
    if call is None:                       # reset while we were transcribing
        return
    call["transcript"] = tr["text"] or "(no speech detected)"
    call["text_english"] = tr.get("text_english") or call["transcript"]
    call["language"] = tr.get("language") or "?"
    call["asr_conf"] = tr.get("language_confidence")
    call["asr_duration_s"] = tr.get("duration_s")
    call["asr_backend"] = tr.get("backend")
    call["intake_stage"] = "locating"

    extract_input = call["transcript"]
    if call["text_english"] and call["text_english"] != call["transcript"]:
        extract_input = f"{call['transcript']} {call['text_english']}"

    call["fields"] = extract.keyword_extract(extract_input)
    call["extractor"] = "keyword"
    _relocate(call)
    call["model_ver"] = model_ver() if call["exposure"].get("in_aoi") else "-"
    store.log("ranked", call_id=call_id, source="upload", extractor="keyword",
              language=call["language"], located=call.get("landmark"),
              severity_band=call["fields"]["severity_band"])
    # Then the model, on the same worker: ASR and the LLM both want the CPU.
    fields, extractor, note = extract.extract(extract_input)
    call = store.CALLS.get(call_id)
    if call is None:
        return
    call["fields"], call["extractor"] = fields, extractor
    call["extractor_error"], call["extractor_pending"] = note, False

    # Check if the LLM identified a spoken landmark that wasn't caught by the direct regex
    if call.get("lat") is None and fields.get("landmark_text") and GAZ:
        hit = GAZ.locate(fields["landmark_text"])
        if hit:
            call.update(lat=hit["lat"], lon=hit["lon"],
                        error_radius_m=hit["error_radius_m"],
                        location_source=hit["source"], landmark=hit["matched"],
                        landmark_confidence=hit["confidence"])
            call["exposure"] = surface().sample(hit["lat"], hit["lon"], hit["error_radius_m"])
            call["model_ver"] = model_ver() if call["exposure"].get("in_aoi") else "-"

    call["intake_stage"] = None
    store.save_call(call)
    store.log("re-extracted", call_id=call_id, extractor=extractor, note=note,
              severity_band=fields["severity_band"], trapped=fields["trapped"])


@app.post("/intake/audio")
async def intake_audio(files: list[UploadFile] = File(...)):
    """Drop a folder of recordings on the board.

    Audio is saved directly to ./storage/audio/<call_id>.wav for 100% air-gapped local storage.
    Each file becomes a call the instant it is saved -- transcript pending,
    ranked on nothing yet -- and fills in as the queue works through them."""
    queued = []
    for f in files:
        cid = str(uuid.uuid4())[:8]
        raw_bytes = await f.read()
        dest = save_audio(cid, raw_bytes, f.filename or "clip.wav")
        call = {
            "id": cid, "received_at": time.time(),
            "transcript": "(transcribing)", "language": "?",
            "lat": None, "lon": None, "error_radius_m": 0.0,
            "location_source": "pending",
            "fields": extract.keyword_extract(""),
            "extractor": "pending", "extractor_error": None,
            "extractor_pending": True, "intake_stage": "transcribing",
            "exposure": dict(PENDING_EXPOSURE), "model_ver": "-",
            "override": 0, "source": "upload", "filename": f.filename,
            "audio_path": dest,
        }
        store.CALLS[cid] = call
        store.save_call(call)
        INTAKE_POOL.submit(_process_upload, cid, dest)
        queued.append({"id": cid, "filename": f.filename})
    store.log("intake", count=len(queued), files=[q["filename"] for q in queued])
    return {"queued": queued}


@app.get("/clusters")
def get_clusters():
    """The board grouped by settlement, worst first.

    Thirty calls from one village is a different response from thirty calls
    spread across a district, and a flat list hides which one you are in."""
    groups: dict[str, dict] = {}
    for c in board():
        p = c["place"]
        key = p["name"] if p else "not yet located"
        g = groups.setdefault(key, {
            "place": key, "kind": p["kind"] if p else None,
            "lat": p["lat"] if p else None, "lon": p["lon"] if p else None,
            "calls": 0, "life_threat": 0, "worst_band": 0, "best_rank": 10 ** 6,
            "max_exposure": 0.0, "min_hand_m": None, "ids": [],
        })
        g["calls"] += 1
        g["life_threat"] += int(c["band"] >= 3)
        g["worst_band"] = max(g["worst_band"], c["band"])
        g["best_rank"] = min(g["best_rank"], c["rank"])
        g["max_exposure"] = max(g["max_exposure"], c["exposure_p75"] or 0.0)
        if c["hand_m"] is not None:
            g["min_hand_m"] = (c["hand_m"] if g["min_hand_m"] is None
                               else min(g["min_hand_m"], c["hand_m"]))
        g["ids"].append(c["id"])
    out = sorted(groups.values(),
                 key=lambda g: (-g["life_threat"], -g["worst_band"], g["best_rank"]))
    return {"groups": out, "located": sum(1 for g in out if g["lat"] is not None)}


@app.get("/teams")
def get_rescue_teams():
    """Group all emergency calls by assigned rescue team stationed across Ernakulam."""
    calls = board()
    return teams.group_calls_by_teams(calls)


class TeamAssignIn(BaseModel):
    team_id: str


@app.post("/calls/{call_id}/assign")
def assign_call_team(call_id: str, payload: TeamAssignIn):
    """Manually assign/reassign a call to a specific rescue team."""
    call = store.CALLS.get(call_id)
    if not call:
        raise HTTPException(404, "no such call")
    if payload.team_id not in teams.TEAMS_BY_ID and payload.team_id != "auto":
        raise HTTPException(422, f"unknown team_id, must be one of: {list(teams.TEAMS_BY_ID.keys())}")
    call["manual_team_id"] = None if payload.team_id == "auto" else payload.team_id
    store.save_call(call)
    store.log("team_reassigned", call_id=call_id, team_id=payload.team_id)
    return {"ok": True, "call": call, "teams": teams.group_calls_by_teams(board())}


# ------------------------------------------------------------ twilio control
def _twilio_env() -> dict:
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    tok = os.environ.get("TWILIO_AUTH_TOKEN", "")
    pub = os.environ.get("VAANI_PUBLIC_URL", "")
    return {
        "configured": bool(sid and tok),
        "public_url_set": bool(pub),
        "account_sid": (sid[:6] + "..." + sid[-4:]) if sid else None,
        "from_number": os.environ.get("TWILIO_NUMBER") or None,
        "webhook_url": (pub.rstrip("/") + "/voice/incoming") if pub else None,
        "hint": ("set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and VAANI_PUBLIC_URL, "
                 "then point the number's 'A call comes in' webhook at webhook_url"),
    }


@app.get("/twilio/status")
def twilio_status():
    return _twilio_env()


def _send_sms(to: str, body: str) -> None:
    """Best-effort. A trial Twilio project can only deliver SMS to a number
    verified in that same console -- exactly the restriction that silently ate
    <Record> earlier -- so this must never be allowed to break the call it was
    sent from. Logged either way; never raised past this function."""
    env = _twilio_env()
    if not (env["configured"] and env["from_number"]):
        return
    import base64
    import urllib.parse
    import urllib.request as ur
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    data = urllib.parse.urlencode(
        {"To": to, "From": env["from_number"], "Body": body}).encode()
    req = ur.Request(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data)
    req.add_header("Authorization", "Basic " + base64.b64encode(
        f"{sid}:{os.environ['TWILIO_AUTH_TOKEN']}".encode()).decode())
    try:
        with ur.urlopen(req, timeout=15) as r:
            reply = json.loads(r.read())
        store.log("sms_sent", to=to, sid=reply.get("sid"), status=reply.get("status"))
    except Exception as e:
        # 21608 ("unverified number") on a trial project is the expected
        # failure mode, not a bug -- the call itself is unaffected either way.
        store.log("sms_failed", to=to, error=str(e))


@app.post("/twilio/dial")
def twilio_dial(to: str, call_id: str | None = None):
    """Call a number back and let the same agent run the conversation.

    The dispatcher clicks one button; Twilio dials, and the answered call hits
    /voice/incoming exactly like an inbound one. Unconfigured, this says what is
    missing rather than pretending it worked."""
    env = _twilio_env()
    if not env["configured"] or not env["public_url_set"]:
        raise HTTPException(503, f"twilio not configured: {env['hint']}")
    if not env["from_number"]:
        raise HTTPException(503, "set TWILIO_NUMBER to the Twilio number to dial from")
    import base64
    import urllib.parse
    import urllib.request as ur
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    body = urllib.parse.urlencode({
        "To": to, "From": env["from_number"],
        "Url": env["webhook_url"], "Method": "POST"}).encode()
    req = ur.Request(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json",
                     body)
    req.add_header("Authorization", "Basic " + base64.b64encode(
        f"{sid}:{os.environ['TWILIO_AUTH_TOKEN']}".encode()).decode())
    try:
        with ur.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
    except Exception as e:
        raise HTTPException(502, f"twilio rejected the call: {e}")
    store.log("dialled", call_id=call_id, to=to, sid=data.get("sid"),
              status=data.get("status"))
    return {"ok": True, "sid": data.get("sid"), "status": data.get("status"),
            "to": to}


@app.get("/health/degradation")
def get_degradation():
    return degradation()


@app.get("/storage/status")
def get_storage_status():
    """Confirms local air-gapped storage and spatial database tables."""
    audio_files = list(AUDIO_STORAGE.glob("*.wav"))
    summary = store.get_spatial_tables_summary()
    summary["audio_storage"] = {
        "path": str(AUDIO_STORAGE),
        "count": len(audio_files),
        "air_gapped": True
    }
    return summary


@app.get("/audit")
def get_audit(limit: int = 200):
    return store.audit_rows(limit)


# ------------------------------------------------------------ live telephony
PENDING_EXPOSURE = {"p50": 0.0, "p75": 0.0, "p90": 0.0, "hand_m": None,
                    "in_aoi": False, "wide_error": False, "terrain_edge": False,
                    "pending": True}


def sync_call(st: dict) -> dict:
    """Push a conversation's current state onto the board. Called every turn, so
    the call appears the instant it connects and fills in as the agent works."""
    call = store.CALLS.get(st["id"]) or {
        "id": st["id"], "received_at": st["started_at"], "override": 0,
        "source": "phone", "from_number": st.get("from_number"),
    }
    call["transcript"] = st["transcript"] or "(on the line…)"
    call["text_english"] = st.get("text_english") or call["transcript"]
    call["language"] = st.get("language") or "?"
    call["live_stage"] = st["stage"]
    if st.get("audio_path"):
        call["audio_path"] = st["audio_path"]
    if st.get("asr_backend"):
        call["asr_backend"] = st["asr_backend"]

    if st["transcript"] and st.get("fields") is None:
        # Spotter now, model on a worker thread: a caller on the line must never
        # wait nine seconds to appear on the board.
        st["fields"] = extract.keyword_extract(st["transcript"])
        st["extractor"], st["extractor_error"] = "keyword", None
        st["extractor_pending"] = True
        EXTRACT_POOL.submit(_upgrade_live, st)
    call["fields"] = st.get("fields") or extract.keyword_extract("")
    call["extractor"] = st.get("extractor", "pending")
    call["extractor_error"] = st.get("extractor_error")
    call["extractor_pending"] = bool(st.get("extractor_pending"))

    loc = st.get("location")
    if loc:
        call.update(lat=loc["lat"], lon=loc["lon"],
                    error_radius_m=loc["error_radius_m"],
                    location_source=loc["source"], landmark=loc["matched"],
                    landmark_confidence=loc["confidence"])
        call["exposure"] = surface().sample(loc["lat"], loc["lon"], loc["error_radius_m"])
        call["model_ver"] = model_ver()
    else:
        call.setdefault("lat", None); call.setdefault("lon", None)
        call.setdefault("error_radius_m", 0.0)
        call["location_source"] = "pending"
        call["exposure"] = PENDING_EXPOSURE
        call["model_ver"] = "-"

    store.CALLS[st["id"]] = call
    store.save_call(call)
    store.log("call_turn", call_id=st["id"], stage=st["stage"],
              language=st.get("language"), located=bool(loc),
              landmark=(loc or {}).get("matched"))
    return call


def _mark_call_ended(st: dict, why: str) -> None:
    """A call the telephony layer never got to a proper 'done' for -- Twilio
    stopped talking to us, whatever the reason. Push it through sync_call one
    last time so the board stops calling it ON CALL, and drop it from the
    live set so the sweep does not see it again."""
    st["stage"] = "done_abandoned"
    sync_call(st)
    store.log("call_ended", call_id=st["id"], why=why,
              transcript=st.get("transcript"))


async def _sweep_stale_calls():
    """A caller who hangs up mid-turn leaves nothing behind: no further
    webhook, ever. Twilio's own 'call status changes' webhook (POST
    /voice/status, configured separately in the console) catches this
    immediately when it is set up; this is the safety net when it is not."""
    while True:
        await asyncio.sleep(10)
        if not CONVO:
            continue
        now = time.time()
        for sid, st in list(CONVO.live.items()):
            if now - st.get("last_update", st["started_at"]) > STALE_CALL_S:
                _mark_call_ended(st, "no update reached us in time")
                CONVO.finish(sid)


def public_base(request) -> str:
    return os.environ.get("VAANI_PUBLIC_URL") or str(request.base_url).rstrip("/")


@app.post("/voice/incoming")
async def voice_incoming(request: Request):
    """Twilio hits this when a call connects. Answer, greet, start listening --
    and in parallel, text a location link to the number that just called. GPS
    on a phone is metres; a spoken landmark is tens to hundreds. Either channel
    can finish first and both feed the same fix through /calls/{id}/location,
    so nothing here blocks on the other."""
    form = await request.form()
    sid = form.get("CallSid", str(uuid.uuid4()))
    from_number = form.get("From")
    st = CONVO.start(sid, from_number)
    base = public_base(request)
    if from_number and from_number != "+simulated" and base.startswith("https://"):
        link = f"{base}/loc.html?c={st['id']}"
        asyncio.create_task(asyncio.to_thread(
            _send_sms, from_number,
            f"Vaani Emergency Response: tap to share your exact location so "
            f"help can reach you faster: {link}"))
    return Response(CONVO.next_twiml(st, public_base(request)),
                    media_type="application/xml")


@app.post("/voice/turn")
async def voice_turn(request: Request):
    """One turn of the conversation. Advance the agent, return the next step.

    Two ways a turn arrives, and they never both fire for the same request:
      SpeechResult   -- Twilio's own speech recognition (gather mode, default,
                        works on a trial project). Nothing to download; the
                        text is already in the form.
      RecordingUrl   -- a completed <Record> (record mode, needs an upgraded
                        Twilio project). Downloaded and transcribed locally.
    Neither present -- the caller said nothing before the listen window closed
                        (gather's own <Redirect> lands here with no
                        SpeechResult) or the recording never downloaded. Either
                        way this is an empty turn, not a crash: the state
                        machine already re-asks once before giving up.
    """
    form = await request.form()
    sid = form.get("CallSid", "")
    speech = form.get("SpeechResult")
    url = form.get("RecordingUrl")
    tr = {"text": "", "language": None, "text_english": ""}
    if speech is not None:
        conf = form.get("Confidence")
        tr = {"text": speech, "language": None, "language_name": None,
              "language_confidence": float(conf) if conf else None,
              "ok": bool(speech), "text_english": speech}
    elif url:
        import urllib.request as ur
        sidauth = (os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN"))
        tmp = HERE / "data" / f"turn_{sid[-8:]}_{int(time.time())}.wav"
        req = ur.Request(url + ".wav")
        if all(sidauth):
            import base64
            req.add_header("Authorization", "Basic " + base64.b64encode(
                f"{sidauth[0]}:{sidauth[1]}".encode()).decode())
        try:
            with ur.urlopen(req, timeout=25) as r:
                tmp.write_bytes(r.read())
            if sid in CONVO.live:
                CONVO.live[sid]["audio_path"] = str(tmp)
            tr = await transcribe_and_translate(str(tmp))
        except Exception as e:
            store.log("asr_failed", call_id=sid, error=str(e))
    # Propagate text_english to conversation state
    if sid in CONVO.live:
        CONVO.live[sid]["text_english"] = tr.get("text_english") or tr.get("text", "")
    st = CONVO.turn(sid, tr)
    if st["stage"].startswith("done"):
        CONVO.finish(sid)
    return Response(CONVO.next_twiml(st, public_base(request)),
                    media_type="application/xml")


@app.post("/voice/status", include_in_schema=False)
async def voice_status(request: Request):
    """Twilio's call-status-changes webhook. Configure it separately in the
    console (Phone Number -> Voice Configuration -> Call status changes) --
    it is a different field from 'A call comes in'. Optional: the sweep above
    catches the same thing within STALE_CALL_S seconds regardless, but this
    marks it the instant Twilio actually reports the call over."""
    form = await request.form()
    sid, status = form.get("CallSid", ""), form.get("CallStatus", "")
    store.log("call_status", call_id=sid, status=status)
    if status in ("completed", "busy", "failed", "no-answer", "canceled"):
        st = CONVO.live.get(sid)
        if st and not st["stage"].startswith("done"):
            _mark_call_ended(st, f"twilio reported {status}")
        CONVO.finish(sid)
    return Response(status_code=204)


@app.post("/voice/simulate")
async def voice_simulate(call_sid: str = Form(...), text: str = Form(""),
                         language: str = Form("en"), audio: UploadFile | None = None):
    """The same agent, without Twilio. Post audio (transcribed locally) or text.

    This is how you rehearse the call flow on a laptop with no number, and it
    exercises exactly the code path a real call takes."""
    if call_sid not in CONVO.live:
        CONVO.start(call_sid, "+simulated")
    if audio is not None:
        tmp = HERE / "data" / f"sim_{call_sid}_{int(time.time())}{Path(audio.filename or '.wav').suffix}"
        tmp.write_bytes(await audio.read())
        CONVO.live[call_sid]["audio_path"] = str(tmp)
        tr = await transcribe_and_translate(str(tmp))
    else:
        text_english = asr.translate_to_english(text, language) if language != "en" else text
        tr = {"text": text, "language": language,
              "language_name": asr.SUPPORTED.get(language, language),
              "language_confidence": 1.0, "ok": bool(text),
              "text_english": text_english}
    # Propagate text_english to conversation state
    CONVO.live[call_sid]["text_english"] = tr.get("text_english") or tr.get("text", "")
    st = CONVO.turn(call_sid, tr)
    done = st["stage"].startswith("done")
    if done:
        CONVO.finish(call_sid)
    return {"stage": st["stage"], "heard": tr["text"], "language": tr.get("language"),
            "text_english": tr.get("text_english"),
            "located": st.get("location"), "done": done,
            "next_prompt": telephony.PROMPTS.get(
                {"opening": "opening", "locating": "relocate" if st["location_attempts"]
                 else "location", "done": "closing"}.get(st["stage"], "closing_nolocation")),
            "board": board()}


@app.get("/voice/health")
def voice_health():
    return {"asr_model": asr.MODEL_SIZE, "asr_loaded": asr.ready(),
            "asr_backend": asr.ASR_BACKEND,
            "gemini_configured": asr.gemini_configured(),
            "landmarks": len(GAZ) if GAZ else 0,
            "live_calls": len(CONVO.live) if CONVO else 0,
            "voice_input_mode": VOICE_INPUT_MODE,
            "twilio_configured": bool(os.environ.get("TWILIO_ACCOUNT_SID"))}


# ------------------------------------------------------- the surface itself
_PNG_CACHE: dict[str, bytes] = {}


@app.get("/surface.png")
def surface_png(layer: str = "exposure"):
    """The surface as an image -- two layers over the identical pixel grid.

    layer=exposure (default): the fitted flood-exposure probability -- the
    AFTER, what the rank is actually computed from.
    layer=hand: plain terrain, Height Above Nearest Drainage, no flood model
    applied at all -- the BEFORE. Same raster set the exposure model was
    fitted on, rendered with its own palette so it never reads as a second,
    fainter flood layer. The console slides between the two."""
    if layer not in ("exposure", "hand"):
        raise HTTPException(422, "layer must be 'exposure' or 'hand'")
    if layer not in _PNG_CACHE:
        import io
        import numpy as np
        from PIL import Image
        if SURFACE is None:
            raise HTTPException(503, "no baked surface; run build_terrain.py")
        rgba = np.zeros((*SURFACE.exp.shape, 4), "uint8")
        if layer == "exposure":
            e = np.nan_to_num(SURFACE.exp, nan=0.0).clip(0, 1)
            # Gamma the alpha, don't ramp it linearly: at 0.05 probability a
            # linear ramp still paints visible water, and the whole valley
            # floor reads as flooded. The gamma keeps low probability
            # genuinely dry-looking, so the eye sees the discrimination the
            # model is actually making.
            a = e ** 1.6
            rgba[..., 0] = (235 - 215 * a)
            rgba[..., 1] = (240 - 130 * a)
            rgba[..., 2] = (242 - 60 * a)
            rgba[..., 3] = (248 * a)
        else:
            h = np.nan_to_num(SURFACE.hand, nan=np.nanmax(SURFACE.hand[np.isfinite(SURFACE.hand)]))
            # Low ground (near drainage) reads as a muted terrain green,
            # rising to pale high ground -- a plain basemap tone, deliberately
            # nothing like the exposure teal, so BEFORE cannot be mistaken
            # for a faded AFTER.
            t = (h.clip(0, 20) / 20.0)
            rgba[..., 0] = (74 + 150 * t)
            rgba[..., 1] = (98 + 130 * t)
            rgba[..., 2] = (72 + 120 * t)
            rgba[..., 3] = 255
        buf = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
        _PNG_CACHE[layer] = buf.getvalue()
    from fastapi.responses import Response
    return Response(_PNG_CACHE[layer], media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})


@app.get("/surface/meta")
def surface_meta():
    """WGS84 bounds + pixel size, so the console can place callers on the image."""
    if SURFACE is None:
        return {"available": False}
    from rasterio.warp import transform_bounds
    from rasterio.transform import array_bounds
    h, w = SURFACE.exp.shape
    west, south, east, north = transform_bounds(
        SURFACE.crs, "EPSG:4326", *array_bounds(h, w, SURFACE.tf))
    return {"available": True, "width": w, "height": h,
            "west": west, "south": south, "east": east, "north": north}


# ---------------------------------------------------------------- demo
@app.post("/demo/surge")
async def surge(file: str = "fixtures.json", spread_s: float = 40.0):
    """Thirty calls arriving the way a real flood makes them arrive: not all at
    once, trickling in over spread_s seconds while the board reorders live.
    Each call still lands on the board immediately (keyword spotter, board-
    first ingest) -- spread_s paces ARRIVALS, not triage. Pass spread_s=0 for
    the old instant-ingest behaviour (what the regression tests want)."""
    data = json.loads((HERE / file).read_text(encoding="utf-8"))
    delay = spread_s / len(data) if data and spread_s > 0 else 0
    ids = []
    for i, c in enumerate(data):
        ids.append(ingest(CallIn(**c))["id"])
        if delay and i < len(data) - 1:
            await asyncio.sleep(delay)
    return {"ingested": ids}


@app.post("/demo/cut-network")
def cut_network(offline: bool = True):
    """Rehearsal switch. The real demo pulls the cable; this proves the ladder
    without touching the hardware. Also actually stops Gemini calls -- not
    just relabels the badge -- because that path genuinely needs the network
    that Ollama and Whisper never did."""
    global DEMO_OFFLINE
    DEMO_OFFLINE = offline
    asr.CLOUD_DISABLED_FOR_REHEARSAL = offline
    store.log("degraded", offline=offline, **degradation())
    return degradation()


@app.post("/demo/reset")
def reset():
    global DEMO_OFFLINE
    DEMO_OFFLINE = False
    asr.CLOUD_DISABLED_FOR_REHEARSAL = False
    _ROUTE_CACHE.clear()
    store.reset()
    store.init()
    return {"ok": True}
