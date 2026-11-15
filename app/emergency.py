"""Possible-emergency rules: transparent, deterministic, and local (no AI, nothing leaves the server).

ILLUSTRATIVE RULES ONLY. "Demo red flag R/S" are fictional placeholders. A real facility's clinical
lead must write and approve the actual criteria. Rule-based emergency alerts are likely a medical
device function under FDA's CDS guidance (alarms supporting time-critical decisions): see docs/02.

A rule fires only on a sentence that reports the factor about the patient, now. Sentences that
deny it, describe a family member, or describe the past do not fire (same cue words as validate.py).
A flag never changes the care order by itself. Nurses check it; the charge nurse decides.
"""
import re

from .excerpt import split_sentences
from .validate import FAMILY, NEGATION, PAST

RULES_VERSION = "emergency-rules-v1 (illustrative)"

RULES = [
    {"id": "ER-1", "label": "Demo red flag R reported", "all_of": ["demo red flag R"],
     "why": "Demo red flag R stands in for a sign the facility treats as a possible emergency."},
    {"id": "ER-2", "label": "Demo red flag S reported", "all_of": ["demo red flag S"],
     "why": "Demo red flag S stands in for a second emergency sign."},
    {"id": "ER-3", "label": "Demo signals A and B reported together", "all_of": ["demo signal A", "demo signal B"],
     "why": "This combination stands in for a pattern the facility treats as higher risk when both are present."},
]


def _affirmed(sentence: str, term: str) -> bool:
    low = sentence.lower()
    if term.lower() not in low:
        return False
    before = low[: low.find(term.lower())]
    if NEGATION.search(before[-60:]):          # "denies demo red flag R", "no demo red flag S"
        return False
    if FAMILY.search(sentence) or PAST.search(sentence):
        return False
    return True


def evaluate(text: str):
    """Return [{rule_id, label, why, evidence}] for rules that fire on this text."""
    sents = split_sentences(text)
    hits = []
    for rule in RULES:
        terms = rule["all_of"]
        if len(terms) == 1:
            ev = next((s for s in sents if _affirmed(s, terms[0])), None)
        else:   # combination: every term affirmed somewhere in the same note
            found = [next((s for s in sents if _affirmed(s, t)), None) for t in terms]
            ev = " … ".join(dict.fromkeys(found)) if all(found) else None
        if ev:
            hits.append({"rule_id": rule["id"], "label": rule["label"], "why": rule["why"], "evidence": ev})
    return hits


def describe_rules():
    return [{"id": r["id"], "label": r["label"], "why": r["why"]} for r in RULES]


_WS = re.compile(r"\s+")


def clean_reason(reason: str) -> str:
    return _WS.sub(" ", (reason or "").strip())[:1000]
