"""The no-numbers guardrail, as a test you can run on stage.  python test_guardrail.py

The architectural claim is that the language model never produces a quantity.
That is either structurally true or it is a promise, and the difference is this
file. It runs without Ollama: the model is replaced by a function returning
exactly the malformed output we claim to reject.

Run it when challenged. It takes a second and it answers the question.
"""
import sys

import extract

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GOOD = {
    "hazard_class": "flood", "severity_band": 2, "people_affected": "2-5",
    "trapped": False, "medical_critical": False, "vulnerable": ["child"],
    "access_constraint": "water_on_road", "landmark_text": "near Odakkali",
    "confidence": "medium",
}

# Every one of these is a plausible thing a language model does, and every one
# of them would put a model-invented number into the ranking maths.
ATTACKS = [
    ("severity as a float",      {"severity_band": 2.7}),
    ("severity above the scale", {"severity_band": 7}),
    ("severity as a string",     {"severity_band": "2"}),
    ("severity as a boolean",    {"severity_band": True}),   # True == 1 in Python
    ("a counted headcount",      {"people_affected": 12}),
    ("an invented probability",  {"people_affected": "0.87"}),
    ("a hazard it made up",      {"hazard_class": "tsunami"}),
    ("trapped as a likelihood",  {"trapped": 0.9}),
    ("trapped as a word",        {"trapped": "yes"}),
    ("a vulnerability invented", {"vulnerable": ["rich"]}),
    ("vulnerable not a list",    {"vulnerable": "child"}),
    ("confidence as a number",   {"confidence": 0.8}),
    ("a field left out",         {"severity_band": None}),      # None fails the enum
]

FAILED = []


def check(name, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILED.append(name)


print("1. the schema itself carries no numeric slot")
for field, spec in extract.SCHEMA["properties"].items():
    if field == "landmark_text":
        continue                    # a verbatim span, for geocoding, never maths
    closed = ("enum" in spec or spec["type"] == "boolean"
              or (spec["type"] == "array" and "enum" in spec["items"]))
    check(f"{field} is a closed set, not a free value", closed)
# severity is the one that reaches the sort key, so state its type out loud
check("severity_band is an ordinal enum 0-3",
      extract.SCHEMA["properties"]["severity_band"]["enum"] == [0, 1, 2, 3])

print("\n2. a well-formed extraction is accepted")
check("valid output validates", extract.validate(dict(GOOD)) == GOOD)

print("\n3. every malformed extraction is REJECTED, not repaired")
for name, patch in ATTACKS:
    bad = {**GOOD, **patch}
    try:
        extract.validate(bad)
        check(f"{name}: {patch}", False)
    except extract.SchemaViolation:
        check(f"{name}", True)

print("\n4. a rejected call still gets triaged, by the keyword spotter")
extract._ask_model = lambda transcript, timeout=20.0: {**GOOD, "severity_band": 2.7}
fields, extractor, err = extract.extract("water is rising and we are trapped upstairs")
check("extractor falls back to keyword", extractor == "keyword")
check("the reason is recorded", bool(err) and "schema violation" in err)
check("the call is still ranked band 3 (trapped)",
      fields["trapped"] and fields["severity_band"] == 3)

print("")
print("5. the spotter is a FLOOR under the model, never a ceiling")
# The regression that made this necessary: qwen2.5:3b read the Malayalam call
# "water is rising, we are trapped, there is a child" as band 0 and dropped a
# trapped family with a child to the bottom of a twelve-call board.
MALAYALAM = "വെള്ളം കയറുന്നു, ഞങ്ങൾ കുടുങ്ങി, ഒരു കുട്ടിയുണ്ട്"
extract._ask_model = lambda transcript, timeout=20.0: {
    **GOOD, "severity_band": 0, "trapped": False, "vulnerable": []}
fields, extractor, note = extract.extract(MALAYALAM)
check("a model that de-escalates is overridden", fields["severity_band"] == 3)
check("the spotter's trapped flag survives", fields["trapped"] is True)
check("the override is visible as llm+floor", extractor == "llm+floor")
check("and it names what it raised", bool(note) and "band 0->3" in note)
# ...and the model is still allowed to escalate ABOVE the spotter
extract._ask_model = lambda transcript, timeout=20.0: {**GOOD, "severity_band": 3}
f2, e2, _ = extract.extract("some water is coming into the compound")
check("a model that escalates is left alone", f2["severity_band"] == 3 and e2 == "llm")

print("")
print("6. the spotter escalates a life threat in every language it claims")
# The regression that cost us a call: a Hindi caller on the roof ranked 10th
# because the vocabulary was missing the word for it.
for label, said in [
    ("English roof", "we are on the roof, water is rising"),
    ("Hindi roof",   "पानी बढ़ रहा है, हम छत पर हैं, मदद भेजिए"),
    ("Malayalam trapped", "വെള്ളം കയറുന്നു, ഞങ്ങൾ കുടുങ്ങി"),
]:
    f = extract.keyword_extract(said)
    check(f"{label} -> band 3", f["severity_band"] == 3 and f["trapped"])

print()
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED: " + "; ".join(FAILED))
    raise SystemExit(1)
print("GUARDRAIL HOLDS — the model has no field in which to put a number.")
