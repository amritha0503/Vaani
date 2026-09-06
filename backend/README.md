# Vaani — backend

One Python process. No Postgres, no PostGIS, no Redis, no Docker.

Forty calls do not need a database server, and every geospatial operation here is
a numpy array lookup rather than a spatial query — so the whole thing is FastAPI +
SQLite + rasterio. That decision buys back roughly three hours of the eighteen.

## Run

```
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000/docs for the interactive API.

## The terrain is real

`data/hand.tif` and `data/exposure.tif` are baked from a Copernicus GLO-30 tile
over Ernakulam (Aluva–Perumbavoor, 20 × 20 km at 30 m, UTM 43N):

```
python build_terrain.py        # ~7 seconds
```

DEM → epsilon priority-flood fill → D8 → flow accumulation → stream network
(9,437 cells) → HAND → slope → exposure probability. HAND is implemented in numpy
in `build_terrain.py`, so the build has no binary dependency.

To move district: change `AOI` in `build_terrain.py`, drop the matching Copernicus
tile in `data/dem_raw.tif`, rerun. Nothing else changes.

## The labels are real, and so is the fit

The full build is three scripts, in order. Each writes to `data/` and the API
reads only what they leave behind.

```
python build_terrain.py     # DEM  -> HAND, slope, TWI, distance-to-drainage   ~7 s
python build_labels.py      # S1   -> observed flood mask + permanent water    ~30 s
python train_exposure.py    # both -> a fitted, calibrated exposure surface    ~4 s
```

`build_labels.py` reads two Sentinel-1 RTC gamma0 VV scenes over the AOI —
21 Aug 2018 (four days after the Kerala flood peak) and 17 Jan 2018, **the same
relative orbit 165**, both descending — and calls a cell inundated where it is
dark in August *and* materially darker than its January baseline. Both windows
are cached to disk with their scene ids, so a re-threshold runs offline.

`train_exposure.py` fits LightGBM to that mask over HAND, slope, TWI and
distance-to-drainage, then overwrites `data/exposure.tif`. Four choices are the
whole defence of the number:

| Choice | Why |
|---|---|
| Permanent water dropped from **both** classes | The Periyar was already there in January. Train on it and the model learns to find rivers. |
| Sample thinned to one cell per **300 m** per class | Adjacent 30 m cells are near-duplicates. Without thinning the effective sample size is a fraction of the row count and every metric is inflated. |
| Validation holds out whole **2 km blocks** | A random split puts a cell's own neighbours in the training set and scores memory as skill. Only the blocked number goes on a slide. |
| Isotonic calibration, **Brier reported next to AUC** | The ranker sorts on this number. Ordering correctly is not enough — it has to mean what it says. |

Held out by block, 426 points in 96 blocks, 5 folds:

| | AUC | Brier |
|---|---|---|
| **fitted, calibrated** (`lgbm-s1-2018kerala-v1`) | **0.877** | **0.113** |
| expert-set logistic (what it replaces) | 0.870 | 0.127 |
| HAND alone, no model | 0.872 | — |

Per-fold AUC 0.867 / 0.912 / 0.885 / 0.890 / 0.871. Gain: `hand_m` 0.89,
`dist_drain_m` 0.09, `twi` 0.02, `slope_deg` 0.01 — HAND dominates, which is the
claim. Every number above is in `data/exposure_model.json` and in the GeoTIFF
tags; `model_ver` is read from the raster and shown on every row of the board,
so an expert-set surface and a fitted one can never look alike on screen.

**Three things to say before you are asked.**

1. It is a **susceptibility index, not an event probability**. The design is
   balanced presence/absence — standard for susceptibility mapping — so the
   number ranks ground against ground. Natural prevalence in the AOI is 0.47%;
   the logit shift that converts (−5.35) is stored with the model.
2. The SAR mask is a **lower bound** on true extent: the scene is days after the
   peak, and radar misses flooded vegetation and flooded built-up ground, where
   double-bounce brightens the return. So the fit **under-predicts** — the wrong
   direction of error for triage, and worth naming first.
3. Rainfall is still an **expert-set** term, applied as a documented scenario
   shift in logit space. One event gives rainfall no spatial variance to fit on.
   At the baked 250 mm it contributes exactly zero.

Elevation is deliberately not a feature: it is the best single predictor inside
one valley and worthless outside it. Every feature used is computable anywhere.

If the rasters are absent the app falls back to a labelled `StubSurface` so lanes
C and D are never blocked. `GET /health/degradation` reports which is live.
**Never demo on the stub.**

## Endpoints

| Route | Does |
|---|---|
| `POST /calls` | transcript + coordinates → extraction, exposure, ranked |
| `GET /queue` | the board, ranked, each row carrying its terrain reason |
| `GET /queue/stream` | same as SSE, one event per change — this is what the console consumes |
| `POST /calls/{id}/override` | operator promote/demote by one band, always audited — the console binds this to `[` and `]` |
| `POST /calls/{id}/location` | place a call by hand: `{lat, lon, error_radius_m?, source?}` (a map click, a phone's GPS) or `{landmark}` (geocoded through the same gazetteer the agent uses). 422 rather than a guess when a landmark is not recognised |
| `POST /calls/{id}/transcript` | `{text}` — the operator listened to the recording and typed what was actually said. Re-triages on the corrected words and geocodes any landmark they contain; never wipes a location fix the call already has |
| `POST /calls/{id}/crew-brief` | `{to}` — a hazard-and-precautions brief for this call, composed only from fields the spotter/model/ranker already computed, sent by SMS through MSG91 to whatever number is given. Demo-scoped: not tied to a real crew roster. Returns `{ok, message, detail}` even on a rejected send, so the composed brief is visible either way |
| `GET /audio/{id}` | the caller's own recording, when one was kept (an upload, a rehearsed simulate, or a live call taken in `record` mode) — 404, not a fabricated clip, when there is none |
| `GET /exposure` | `lat, lon, radius_m` → p50/p75/p90 + HAND. Pure raster read |
| `GET /exposure/section` | `lat, lon` → HAND sampled outward in the direction that actually descends toward drainage — the number behind "ground" as a line, not a point |
| `GET /health/degradation` | current rung of the ladder and what was lost |
| `GET /audit` | the accountability trail |
| `POST /demo/surge` | replay `fixtures.json` — 30 calls, paced over `spread_s` seconds (default 40, so the board fills in visibly rather than jumping); `spread_s=0` for instant (what the tests use) |
| `POST /demo/cut-network` | rehearse the ladder without touching the cable |
| `POST /demo/reset` | back to zero |
| `GET /` | the operator console |
| `GET /surface.png?layer=exposure\|hand` | the surface as an image, rendered locally — `exposure` (default, the AFTER) or `hand` (the BEFORE, plain terrain) — the console slides between the two, no tile server either way |
| `GET /surface/meta` | WGS84 bounds, so the console can place callers on it |
| `GET /dispatch/{id}` | nearest usable station → caller, avoiding flooded road. `route: null` + `boat_or_air_required` when there is no dry way in |
| `GET /roads/impassable` | the cut segments as GeoJSON — the map layer, drawn from the same raster |
| `POST /intake/audio` | drop N recordings on the board at once; each is transcribed, triaged, geocoded and re-read in turn |
| `GET /clusters` | the board grouped by settlement, worst first |
| `GET /twilio/status` | what the phone ingress still needs, or that it is ready |
| `POST /twilio/dial` | call a number back and hand it to the same agent |
| `GET /loc.html` | the page an SMS sent on connect points a caller at, for a GPS fix the call audio alone cannot give |
| `GET /legacy` | the dependency-free console, kept as a fallback |

## The three rules the code enforces

**The model never produces a number.** Every field in `extract.SCHEMA` is an enum
or a boolean. A violation raises `SchemaViolation`, the call falls to the keyword
spotter, and the board shows `extractor: keyword`. Rejected, never repaired.

**Terrain can promote, never demote.** `rank.rank()` sorts on `(band, within_score)`
lexicographically. Exposure only moves the second element, so it can reorder inside
a band and can never push a reported life threat below a terrain score.

**The spotter is a floor under the model, never a ceiling.** `extract.apply_floor()`
runs the keyword spotter alongside the model and takes the higher band, the union
of the flags, the union of the vulnerabilities, and the spotter's hazard whenever
the model fell back to `other`. The model may escalate a call;
it can never de-escalate one below what the deterministic spotter found. The row
shows `llm+floor` and says what was raised.

This is not theoretical. `qwen2.5:3b` read the Malayalam call *"വെള്ളം കയറുന്നു,
ഞങ്ങൾ കുടുങ്ങി, ഒരു കുട്ടിയുണ്ട്"* (water is rising, we are trapped, there is a
child) as **band 0** and dropped a trapped family with a child to position 12 of
12. The spotter — a word list — had it right. Same shape as the terrain rule: the
clever component can promote, only the dumb reliable one sets the floor.

## Files

```
build_terrain.py  DEM -> HAND -> covariates. Run once, bakes data/*.tif
build_labels.py   Sentinel-1 change detection -> observed flood mask
train_exposure.py fits + calibrates the exposure surface, rewrites exposure.tif
build_roads.py    Overpass -> road graph with exposure baked onto every edge
dispatch.py       routing on that graph: the detour, or "send a boat"
test_guardrail.py the no-numbers claim as a runnable test — run it when challenged
static/index.html the operator console: live board, map, badge. No dependencies
main.py        FastAPI app, endpoints, degradation ladder, demo controls
exposure.py    the two rasters in RAM + uncertainty-disc sampling
extract.py     Ollama structured output, schema validator, keyword fallback
rank.py        the fusion ranker and the life-threat floor
store.py       in-memory working set + SQLite audit log
fixtures.json  12 calls including the twin pair — edit the coordinates
```

## The model, and what it costs

Ollama + `qwen2.5:3b`, local, schema-constrained. Install once:

```
winget install Ollama.Ollama      # or https://ollama.com/download
ollama pull qwen2.5:3b
ollama run qwen2.5:3b "test"      # warm it: never cold-start on stage
```

**Measured on this box: ~9 s per extraction on CPU.** Three consequences, all
handled:

1. **A call never waits for it.** `ingest()` triages with the spotter in
   microseconds, puts the call on the board, and submits the model to a worker
   pool; the row upgrades to `extractor: llm` when the answer lands. `/demo/surge`
   returns in **0.2 s** with a full board, and the twelve rows finish upgrading
   over the next two minutes. Same contract the live phone path already keeps.
2. **One worker, not three.** Ollama on a CPU is a serial resource — three
   threads in flight queue *inside* Ollama where our timeout cannot see them.
   Measured: 3 workers gave 5 timeouts out of 12 in 87 s; 1 worker gives 12 out
   of 12 in 118 s. `VAANI_EXTRACT_WORKERS` raises it on a box with a GPU.
3. **It over-triages.** A 3B model marks most flood calls band 3, including one
   caller who said they had already left the building. Over-triage is noise;
   under-triage is the dangerous direction, and that is what the floor above
   prevents. If you want a tighter board, `qwen2.5:7b` reads better at roughly
   twice the latency.

`python test_guardrail.py` exercises the whole path without needing Ollama up.

## Dispatch reads the same surface

```
python build_roads.py          # Overpass -> data/roadgraph.json, ~15 s, cached
```

15,903 edges between 13,536 junctions, built from an Overpass extract of the AOI.
Exposure and HAND are sampled **along** each edge at one sample per 30 m cell,
not at its midpoint — a road is impassable if it dips through a flooded low spot
anywhere on its length, and a midpoint sample is exactly the test that misses
that. 2,717 segments sit above the cut (`exposure ≥ 0.55`) and are drawn on the
map in red.

`dispatch.py` then answers two questions at once:

| | |
|---|---|
| the direct route | shortest road distance, flooding ignored |
| the dispatched route | shortest distance that never crosses the cut |

The difference is the detour the terrain forced, in metres — on the fixtures, up
to **5.7 km direct (through water) against 11.7 km dry**. Every candidate depot
is tried for a dry route before any of them is allowed to report a wet one:
answering "send a boat" because the nearest station is cut off, while one four
kilometres out has a clear run, is the kind of answer that gets someone killed.
When no depot has a dry approach, the answer is `route: null` with
`boat_or_air_required` and the flooded approach drawn dashed — a boat is a real
answer, a road route through a metre of water is not.

The graph is a baked local file and the weights come off a baked local raster,
so dispatch survives the cable pull exactly like the ranker. `smoke_test.py`
asserts that: it cuts the network and asks for the same route again.

Depots are OSM fire stations and police stations in the AOI (hospitals are in
the file as a fallback but are not dispatched from). The graph is undirected on
purpose — a rescue vehicle uses both sides of a one-way street.

## The console

React + Vite + Tailwind, in `../frontend`, built into `static/app/` and served by
FastAPI. Nothing is fetched from a CDN at runtime — script, stylesheet and icon
are all local files — so the UI draws with the cable pulled like everything else.

```
cd ../frontend
npm install         # once
npm run build       # -> backend/static/app, which "/" then serves
npm run dev         # optional: hot reload on :5173, API proxied to :8000
```

`GET /legacy` still serves the original single-file console. It has no build
step, so it is the thing that works if the toolchain does not — check it before
you sleep, and never delete it.

Three views:

**Operations** — the ranked queue, the exposure surface, and the detail of one
call. Rows carry the terrain reason, the extractor, the settlement and the age;
clicking one draws its route over the impassable layer and fills in the dispatch
line. Arrow keys move down the queue, Home and End jump, every row and map pin is
a real button with a spoken label, and the top of the queue is announced through
a live region when it changes — one announcement, not twelve, because a screen
reader reciting a reordering surge is worse than useless. When the fixture twin
pair (or a real duplicate) is on the board, a banner states their positions and
ground side by side — the whole pitch in one line, not something you have to
scroll to find. The map has a before/after slider (plain terrain vs. the fitted
exposure, same pixel grid, two renders) and, per selected call, a cross-section
reads HAND outward toward drainage as a line rather than a single number. The
detail panel plays the caller's own recording back when one was kept, and two
buttons — bound to `[` and `]` — let the operator override the band by one step
in either direction; every override is on the audit trail immediately.

**Intake & triage** — upload, call automation, and the two groupings:

| Panel | Does |
|---|---|
| Upload recordings | drop a folder of call audio; each file is on the board before it is transcribed, and fills in as the queue works through it. A file whose speech names no landmark the gazetteer recognises gets two more ways to be placed: "use my GPS" (the browser's own location) or "pick on map" (click the exact spot on the Operations map) |
| Call automation | what Twilio still needs, with the exact commands, or a number field that dials out and hands the call to the agent |
| Classified by severity | the four bands with counts; click one to filter the queue to it |
| Grouped by location | the board by settlement — thirty calls from one village is a different response from thirty spread across a district |

**Audit log** — every ranking decision, override, dispatch, SMS and system-state
change, newest first, live. This is what "every manual override is logged"
means in practice: a screen you can point a judge at, not a claim in a slide.

## What local ASR actually does to a short clip in Malayalam — and the hybrid that answers it

Measured, on a real 3.9-second Malayalam upload: `faster-whisper small`
identifies the language correctly (`ml`, probability 0.66) and then transcribes
it into **romanised gibberish** — *"Aayu, nyangala reksikyane, valam bonny bonny
vedya."* — rather than Malayalam script. Raising `beam_size` from 1 to 5,
turning the VAD filter off, and disabling `condition_on_previous_text` all
produce the **same** output, so this is not flaky decoding to be tuned away: it
is the `small` model's ceiling on a short clip in a lower-resource language.

**`asr.py` is two backends behind one function, not a replacement.** Set
`GEMINI_API_KEY` and it tries `gemini-3.5-transcribe` first (~2.6% average WER
across 85+ languages, Malayalam included) and falls straight through to local
Whisper on *any* failure — no key, no network, quota, a malformed response —
the same "never silently do nothing" shape as `extract.apply_floor()`. Every
transcript's `backend` field says which one actually answered.

This is deliberately not a strict upgrade, and not the default-on choice:

- **Tamil is not in Gemini's supported-language list.** Tamil calls fall back
  to Whisper regardless of configuration — the one language this project
  targets that Gemini doesn't claim.
- **It costs the offline claim.** Every other capability in this app —
  ranking, routing, the exposure surface, local ASR itself — reads cached
  local files and needs no network. Gemini is the one exception, by
  necessity: `/demo/cut-network` now actually disables it rather than only
  relabelling the badge, so rehearsing "the cable is pulled" is honest about
  what really stops working.
- **It costs the "audio never leaves the building" privacy claim**, which
  matters more for this data than most. Free-tier usage is typically eligible
  for Google to use in improving its models — worth knowing before pointing
  it at real emergency-call audio, even in rehearsal.
- **`VAANI_ASR_BACKEND=whisper`** forces local-only regardless of whether a
  key is set, for exactly the two reasons above.

With no key set, behaviour is unchanged from before this pass. Three things
also follow from the underlying accuracy gap, and the console does all three
regardless of which backend served a given call:

1. **`VAANI_ASR_MODEL=medium`** trades speed for multilingual accuracy on the
   Whisper side specifically — no code change, already an env var. Default
   stays `small` because ingestion is board-first and the operator should
   never wait on transcription.
2. **A low-confidence transcript says so.** Under 75% language probability, or
   under a 3-second clip, the detail panel prints
   `LOW-CONFIDENCE TRANSCRIPTION` and points at the recording. (Gemini doesn't
   return a comparable confidence figure, so this check only fires for a
   Whisper-served row — silence here is not a claim that a Gemini transcript
   is correct.)
3. **The operator can just fix it.** `POST /calls/{id}/transcript`, wired to an
   `edit` button next to every transcript: listen to the audio, type what was
   actually said, and the call re-triages on the corrected words — including
   geocoding a landmark the garbled version hid. This is the ladder's "operator
   types the transcript" rung, available on demand rather than only when ASR is
   declared down, because a wrong transcript and a missing one need the same
   remedy.

The ranking stays safe through all of it — that is what the spotter floor and
the enum guardrail are for — but "safe" is not "useful", and a dispatcher who
cannot read the transcript cannot dispatch. The recording is the ground truth,
so the console always puts it one click away.

`python test_asr_backend.py` drives all of this — auto/gemini-fails/whisper-
forced/network-cut — by monkeypatching `_transcribe_gemini`, no API key or
network call needed.

## Audit the keyword table before you demo

`extract.KEYWORDS` is the safety net, and it is only as good as its vocabulary.
A Hindi call saying *"हम छत पर हैं"* (we are on the roof) originally ranked 10th
because `छत` was missing — the fallback under-triaged a life threat, which is the
exact failure it exists to prevent. Every language you claim needs every key
checked.

---

## Taking a real phone call

A normal PSTN call carries the caller's **number, not their position**. Twilio's
`FromCity` is the number's registration area — useless at the resolution a rescue
boat needs. In real ERSS-112, location arrives out-of-band via **AML** (Android
ELS / Apple HELO push the handset's own GPS fix when an emergency number is
dialled), and you cannot be provisioned as an AML endpoint — that is a government
process, not a technical one.

So the agent does what a control-room operator does: **it asks, and geocodes the
answer locally.**

```
turn 1   "Emergency services. Tell me what is happening."
         -> faster-whisper (language auto-detected) -> hazard, severity, trapped
         -> the call appears on the board immediately, marked LOCATING
turn 2   "Tell me where you are. Name the nearest temple, school or village."
         -> gazetteer.locate() -> point + honest error radius
         -> exposure computed, call re-ranks on terrain
turn 3   confirm, tell them to stay put, hang up
```

`gazetteer.py` holds 393 named OSM landmarks for the AOI in RAM and fuzzy-matches
against them. **No Nominatim, no network** — geocoding survives the cable pull
like everything else. A village name yields ±1200 m, a named bridge ±120 m, and
when two landmarks match equally well the radius doubles and the row says so.
When nothing matches it returns `None` rather than guessing, because a guessed
coordinate sends a boat to where nobody is.

**A second, parallel channel closes the gap AML would otherwise close.** The
instant a call connects, an SMS goes out to the caller's own number with a link
to `/loc.html` — one button, the phone's own GPS, no app to install. Whichever
channel resolves first wins; a caller who names a landmark on turn 2 needs the
text for nothing, and one who never gets a word in (a bad line, a panicking
child) can still tap one link. Both write through the same
`POST /calls/{id}/location`, so there is exactly one path from "a fix arrived"
to "the board re-ranks," not two to keep in sync. **This inherits the same
trial-account ceiling `<Record>` hit**: Twilio only delivers SMS to a number
verified in that same console until the project is upgraded. Failure is
caught and logged (`sms_failed`, audit log) rather than allowed to touch the
call itself — the voice conversation is the channel that must never depend on
the text succeeding.

### Wiring Twilio (~20 minutes)

1. Sign up, get a trial number. (An Indian number needs KYC and takes days — a
   US/UK trial number works fine for a demo; you dial it from any phone.)
2. Expose the laptop: `cloudflared tunnel --url http://localhost:8000`
   (or ngrok). Copy the public https URL.
3. Set it so the agent builds correct callback URLs:
   ```
   set VAANI_PUBLIC_URL=https://your-tunnel.trycloudflare.com
   set TWILIO_ACCOUNT_SID=AC...
   set TWILIO_AUTH_TOKEN=...
   ```
4. In the Twilio console, point the number's **A CALL COMES IN** webhook at
   `POST {VAANI_PUBLIC_URL}/voice/incoming`.
5. Dial the number. The call appears on the board before you finish the sentence.

The tunnel is the one cloud dependency, and it is only the ingress. Transcription,
extraction, geocoding, exposure and ranking all stay on the box — cut the network
mid-call and everything already on the board keeps working.

### Why the live call used to just end after the greeting

**`<Record>` is blocked entirely on a Twilio trial project.** Twilio silently
replaces it with a spoken warning -- "The Record verb is not available on
trial accounts" -- and since there is nothing after it in the response, the
call then just ends. Confirmed against Twilio's own account (three real calls,
all `status: completed`, all under 12 seconds, and the server access log shows
only `POST /voice/incoming` -- `/voice/turn` was never once reached, because no
recording was ever produced for the platform to call back on).

The fix is `VAANI_TWILIO_INPUT=gather` (the default): Twilio's own live speech
recognition, `<Gather input="speech">`, which **is** available on a trial
project and needs no recording at all -- Twilio POSTs the transcribed text
straight to `/voice/turn` as `SpeechResult`. Say the trade-off plainly if
asked: transcription for this ONE path comes from Twilio's cloud speech
engine, not the local Whisper model that transcribes every rehearsed, uploaded
and simulated call. The phone ingress was never fully on-prem anyway -- it
already crosses Twilio's network and a public tunnel -- so this doesn't move
the line the plan already draws, it just says where speech-to-text happens to
sit on this one path.

`VAANI_TWILIO_INPUT=record` switches back to local Whisper transcription of a
downloaded recording, exactly as before -- **but needs an upgraded (non-trial)
Twilio project**, since that is what the trial restriction actually blocks.

**A call Twilio never calls back on again used to sit on the board forever**,
reading "ON CALL" with the age counting up with no way to stop. Two
independent fixes, either is enough on its own:

- A background sweep (`VAANI_STALE_CALL_S`, default 90s) marks a call that has
  gone quiet for that long as ended, and the row switches to
  `CALL ENDED -- no response reached us` rather than `ON CALL` forever.
- Twilio's own **Call status changes** webhook -- a *different* field from "A
  call comes in" on the same Phone Number page -- ends it immediately instead
  of waiting for the sweep:

  | Field | Value |
  |---|---|
  | Call status changes | `{VAANI_PUBLIC_URL}/voice/status` |
  | Method | HTTP POST |

  Optional, but it is the difference between a stale row disappearing in
  seconds versus up to ninety of them.

`python test_voice_flow.py` drives both input modes and both cleanup paths
without needing Twilio at all -- run it after touching anything in
`telephony.py` or the `/voice/*` routes in `main.py`.

### Rehearsing without a number

`POST /voice/simulate` drives the identical state machine:

```
curl -X POST http://127.0.0.1:8000/voice/simulate \
  -F "call_sid=CA_test" -F "audio=@data/test_call.wav"      # real audio
curl -X POST http://127.0.0.1:8000/voice/simulate \
  -F "call_sid=CA_test" -F "text=we are near Odakkali"       # or typed
```

Use this for every rehearsal. Keep the real number for the live demo only.

### What this costs you on stage

Say it plainly: *"Location on a real 112 call comes from AML, which is provisioned
by the carrier to designated emergency endpoints. We implement that interface. In
this demo the agent asks for a landmark instead, and tells you how precise that
answer is."* Naming the limit is stronger than papering over it — and the
uncertainty sampling means the coarse fix degrades the ranking honestly rather
than silently.
