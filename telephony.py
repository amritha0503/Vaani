"""The call agent.

A normal PSTN call carries a number, not a position. Twilio's FromCity is the
number's registration area -- useless at the resolution a rescue boat needs. So
the agent does what a control-room operator does: it asks.

    turn 1   "Emergency services. Tell me what is happening."
             -> hazard, severity, trapped, vulnerable
    turn 2   "Where are you? Name the nearest landmark."      (only if needed)
             -> landmark text -> local gazetteer -> point + honest error radius
    turn 3   confirm, tell them to stay put, hang up

Each turn writes straight to the live board, so the call appears the moment it
connects and fills in as it goes -- the operator is never waiting on the AI.

Twilio is the ingress here because it is the only thing obtainable same-day. In
production this is the ERC's own SIP trunk and location arrives out-of-band via
AML; both are the same two calls into `Conversation`.
"""
import time
import uuid
import xml.sax.saxutils as sx

PROMPTS = {
    "opening": "Emergency services. Tell me what is happening and how many people are with you.",
    "location": "Help is coming. Tell me where you are. Name the nearest temple, school, bridge or village.",
    "relocate": "I did not catch the place. Say only the name of the nearest landmark.",
    "closing": "Understood. Stay where you are, move to higher ground if you can, and keep this phone with you.",
    "closing_nolocation": "Stay where you are and keep this phone with you. An operator is calling you back now.",
}
MAX_LOCATION_ATTEMPTS = 2


def twiml(*children: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(children) + "</Response>"


def say(text: str, lang: str = "en-IN") -> str:
    return f'<Say language="{lang}">{sx.escape(text)}</Say>'


def record(action: str, seconds: int = 12) -> str:
    """Requires an UPGRADED (non-trial) Twilio project. On a trial project
    Twilio silently replaces this verb with a spoken warning and hangs up --
    see gather() below, which is the default for exactly that reason."""
    return (f'<Record action="{sx.escape(action)}" method="POST" maxLength="{seconds}" '
            f'timeout="3" playBeep="true" trim="trim-silence" />')


def gather(action: str, prompt: str, lang: str, seconds: int = 6) -> str:
    """Twilio's own speech recognition, live during the call. Available on a
    trial project -- <Record> is not, so this is the default input mode.

    The trade-off, stated plainly: transcription for THIS path comes from
    Twilio's cloud speech engine, not the local Whisper model that transcribes
    every rehearsed, uploaded and simulated call. The phone ingress already
    isn't fully on-prem (it crosses Twilio's own network and a public tunnel),
    so this doesn't cross a new line -- it's the same boundary the plan already
    draws, just where speech-to-text happens to sit on this one path.

    If the caller says nothing before the gather times out, Twilio does NOT
    call `action` on its own -- it falls through to the next verb in this same
    response. The <Redirect> makes that case reach /voice/turn anyway, with no
    SpeechResult set, which the handler already treats as an empty turn.
    """
    return (
        f'<Gather input="speech" action="{sx.escape(action)}" method="POST" '
        f'language="{lang}" speechTimeout="auto" timeout="{seconds}">'
        f'{say(prompt, lang)}'
        f'</Gather>'
        f'<Redirect method="POST">{sx.escape(action)}</Redirect>'
    )


def hangup() -> str:
    return "<Hangup/>"


class Conversation:
    """State machine for in-flight calls. One entry per call, dropped on hangup."""

    def __init__(self, gazetteer, on_update, input_mode: str = "gather"):
        self.gaz = gazetteer
        self.on_update = on_update          # called with the call dict every turn
        self.live: dict[str, dict] = {}     # call_sid -> state
        self.input_mode = input_mode        # "gather" (trial-safe) or "record"

    # ---------------------------------------------------------------- turns
    def start(self, call_sid: str, from_number: str | None = None) -> dict:
        state = {
            "id": str(uuid.uuid4())[:8],
            "call_sid": call_sid,
            "from_number": from_number,
            "started_at": time.time(),
            "stage": "opening",
            "location_attempts": 0,
            "transcript": "",
            "language": None,
            "fields": None,
            "location": None,
            "turns": [],
            "last_update": time.time(),   # for the stale-call sweep in main.py
        }
        self.live[call_sid] = state
        self.on_update(state)
        return state

    def turn(self, call_sid: str, transcription: dict) -> dict:
        """Feed one recorded utterance in. Returns the state; caller renders TwiML."""
        st = self.live.get(call_sid) or self.start(call_sid)
        text = (transcription.get("text") or "").strip()
        st["last_update"] = time.time()
        st["turns"].append({"stage": st["stage"], "text": text,
                            "language": transcription.get("language")})
        if transcription.get("language"):
            st["language"] = transcription["language"]
            st["language_name"] = transcription.get("language_name")
            st["language_confidence"] = transcription.get("language_confidence")
        if transcription.get("backend"):
            st["asr_backend"] = transcription["backend"]

        if st["stage"] == "opening":
            st["transcript"] = text
            st["stage"] = "locating"
        elif st["stage"] == "locating":
            st["location_attempts"] += 1
            hit = self.gaz.locate(text) if text else None
            if hit:
                st["location"] = hit
                st["stage"] = "done"
            elif st["location_attempts"] >= MAX_LOCATION_ATTEMPTS:
                st["stage"] = "done_nolocation"
            # else: stay in "locating" and ask once more
            # keep the words either way -- the operator may recognise the place
            st["landmark_said"] = " ".join(
                t["text"] for t in st["turns"] if t["stage"] == "locating").strip()

        self.on_update(st)
        return st

    def next_twiml(self, st: dict, base_url: str) -> str:
        lang = {"ml": "ml-IN", "hi": "hi-IN", "ta": "ta-IN",
                "te": "te-IN", "kn": "kn-IN"}.get(st.get("language"), "en-IN")
        action = f"{base_url}/voice/turn"

        def listen(prompt: str, record_s: int, gather_s: int) -> str:
            if self.input_mode == "record":
                return twiml(say(prompt, lang), record(action, record_s))
            return twiml(gather(action, prompt, lang, gather_s))

        if st["stage"] == "opening":
            return listen(PROMPTS["opening"], 15, 8)
        if st["stage"] == "locating":
            key = "relocate" if st["location_attempts"] else "location"
            return listen(PROMPTS[key], 10, 6)
        if st["stage"] == "done":
            return twiml(say(PROMPTS["closing"], lang), hangup())
        return twiml(say(PROMPTS["closing_nolocation"], lang), hangup())

    def finish(self, call_sid: str) -> dict | None:
        return self.live.pop(call_sid, None)
