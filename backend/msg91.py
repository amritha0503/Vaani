"""MSG91 SMS -- the crew dispatch brief.

Demo-scoped: a judge types a number on the console, the call they have
selected gets turned into a short hazard-and-precautions brief, and it goes
out as an SMS. Same shape as Twilio's _send_sms in main.py -- best effort,
logged either way, never allowed to raise past send().

Two paths, tried by configuration rather than by failure (unlike asr.py's
Gemini-then-Whisper fallback, these are not interchangeable: a Flow send
needs a template that must already exist in the MSG91 dashboard, so there is
nothing to "fall back to" at runtime if it is missing):

- MSG91_TEMPLATE_ID set  -> Flow API (v5), MSG91's current method. Indian
  DLT regulations require the template's static text to be pre-approved on
  the MSG91 dashboard; the whole brief is passed as a single variable, so
  the approved template should be essentially "##var1##" (check what your
  account's DLT registration actually allows).
- MSG91_TEMPLATE_ID unset -> the legacy quick-send endpoint. No template
  needed, which is the fastest path to a working demo, but may be rejected
  by MSG91 for Indian mobile numbers if DLT is enforced on the account.
"""
import json
import os
import urllib.parse
import urllib.request as ur

AUTH_KEY = os.environ.get("MSG91_AUTH_KEY", "")
SENDER_ID = os.environ.get("MSG91_SENDER_ID", "VAANI")
TEMPLATE_ID = os.environ.get("MSG91_TEMPLATE_ID", "")


def configured() -> bool:
    return bool(AUTH_KEY)


def send(to: str, body: str) -> dict:
    """to: E.164 (+91...) or a bare country-coded number. Never raises --
    returns {"ok": bool, "detail": str} either way."""
    if not AUTH_KEY:
        return {"ok": False, "detail": "MSG91_AUTH_KEY not set"}
    mobile = to.strip().lstrip("+")
    try:
        return _send_flow(mobile, body) if TEMPLATE_ID else _send_legacy(mobile, body)
    except Exception as e:
        return {"ok": False, "detail": str(e)}


def _send_flow(mobile: str, body: str) -> dict:
    payload = json.dumps({
        "template_id": TEMPLATE_ID,
        "short_url": "0",
        "recipients": [{"mobiles": mobile, "VAR1": body}],
    }).encode()
    req = ur.Request("https://control.msg91.com/api/v5/flow/", data=payload, method="POST")
    req.add_header("authkey", AUTH_KEY)
    req.add_header("Content-Type", "application/json")
    with ur.urlopen(req, timeout=15) as r:
        reply = json.loads(r.read())
    return {"ok": reply.get("type") == "success", "detail": json.dumps(reply)}


def _send_legacy(mobile: str, body: str) -> dict:
    params = urllib.parse.urlencode({
        "authkey": AUTH_KEY, "mobiles": mobile, "message": body,
        "sender": SENDER_ID, "route": "4", "country": "91",
    })
    with ur.urlopen(f"https://api.msg91.com/api/sendhttp.php?{params}", timeout=15) as r:
        reply = r.read().decode(errors="replace")
    # Success returns a bare numeric request id; anything else is an error string.
    return {"ok": reply.strip().isdigit(), "detail": reply.strip()}
