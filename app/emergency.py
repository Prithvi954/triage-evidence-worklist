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

RULES_VERSION = "emergency-rules-v2 (educational; pending clinical review)"

# Practice rules: plain-language red flags commonly taught in triage courses. EDUCATIONAL ONLY,
# pending review by a licensed clinician; not validated for patient care.
# A rule fires when every "all_of" term and at least one "any_of" term (if given) are affirmed.
RULES = [
    {"id": "ER-1", "label": "Demo red flag R reported", "all_of": ["demo red flag R"],
     "why": "Demo red flag R stands in for a sign the facility treats as a possible emergency."},
    {"id": "ER-2", "label": "Demo red flag S reported", "all_of": ["demo red flag S"],
     "why": "Demo red flag S stands in for a second emergency sign."},
    {"id": "ER-3", "label": "Demo signals A and B reported together", "all_of": ["demo signal A", "demo signal B"],
     "why": "This combination stands in for a pattern the facility treats as higher risk when both are present."},
    {"id": "RF-CHEST", "label": "Chest pain with warning signs", "all_of": ["chest pain"],
     "any_of": ["sweating", "sweaty", "diaphoretic", "shortness of breath", "short of breath", "nausea",
                "left arm", "jaw", "spreading", "radiating"],
     "why": "Chest pain with sweating, breathlessness, nausea, or pain spreading to the arm or jaw can mean a heart attack."},
    {"id": "RF-STROKE", "label": "Possible stroke signs",
     "any_of": ["facial droop", "face droop", "face is drooping", "arm weakness", "weakness on one side",
                "slurred speech", "trouble speaking", "can't find words"],
     "why": "Sudden face droop, arm weakness, or speech trouble can mean a stroke. Note the time the signs started."},
    {"id": "RF-BREATH", "label": "Severe trouble breathing",
     "any_of": ["trouble breathing", "difficulty breathing", "struggling to breathe", "can't breathe",
                "unable to speak in full sentences", "lips look blue", "blue lips"],
     "why": "Struggling to breathe or being unable to speak in full sentences needs immediate attention."},
    {"id": "RF-SEPSIS", "label": "Fever with confusion or low blood pressure (possible sepsis)", "all_of": ["fever"],
     "any_of": ["confused", "confusion", "low blood pressure", "blood pressure low", "very fast heart rate"],
     "why": "Infection with confusion or low blood pressure can mean sepsis, which gets worse quickly."},
    {"id": "RF-BLEED", "label": "Severe bleeding",
     "any_of": ["severe bleeding", "bleeding heavily", "uncontrolled bleeding", "bleeding through"],
     "why": "Bleeding that won't stop can quickly become life-threatening."},
    {"id": "RF-AIRWAY", "label": "Possible severe allergic reaction",
     "any_of": ["throat tightness", "throat feels tight", "tongue swelling", "swollen tongue", "lips swelling"],
     "why": "Throat tightness or tongue swelling after an exposure can mean anaphylaxis."},
    {"id": "RF-UNRESP", "label": "Unresponsive", "any_of": ["unresponsive", "not responding", "hard to wake"],
     "why": "A patient who is hard to wake or not responding needs immediate assessment."},
]

_CLAUSE_BREAK = re.compile(r";|\bbut\b|\bhowever\b|\bnow\b", re.I)
_AFFIRM = re.compile(r"\b(reports|reported|complains of|c/o|endorses|states|has|now has|presents with|noted|developed)\b", re.I)


def _clause(sentence: str, idx: int, length: int):
    """The part of the sentence around the term, cut at ';', 'but', 'however', 'now'."""
    before = sentence[:idx]
    cuts = [m.end() for m in _CLAUSE_BREAK.finditer(before)]
    start = cuts[-1] if cuts else 0
    after = sentence[idx + length:]
    m = _CLAUSE_BREAK.search(after)
    end = idx + length + (m.start() if m else len(after))
    return sentence[start:idx], sentence[start:end]


def _affirmed(sentence: str, term: str) -> bool:
    low = sentence.lower()
    idx = low.find(term.lower())
    if idx < 0:
        return False
    before, clause = _clause(sentence, idx, len(term))
    window = " ".join(before.split()[-6:])      # negation scope: a few words before the term
    neg = None
    for m in NEGATION.finditer(window):
        neg = m
    if neg and not _AFFIRM.search(window[neg.end():]):   # "no allergies, reports chest pain" still counts
        return False
    if FAMILY.search(clause) or PAST.search(clause):      # "father had chest pain", "resolved last week"
        return False
    return True


def evaluate(text: str):
    """Return [{rule_id, label, why, evidence}] for rules that fire on this text."""
    sents = split_sentences(text)
    hits = []

    def first(term):
        return next((s for s in sents if _affirmed(s, term)), None)

    for rule in RULES:
        found = [first(t) for t in rule.get("all_of", [])]
        if not all(found):
            continue
        if rule.get("any_of"):
            alt = next((e for e in (first(t) for t in rule["any_of"]) if e), None)
            if not alt:
                continue
            found.append(alt)
        ev = " … ".join(dict.fromkeys(found))
        if ev:
            hits.append({"rule_id": rule["id"], "label": rule["label"], "why": rule["why"], "evidence": ev})
    return hits


def describe_rules():
    return [{"id": r["id"], "label": r["label"], "why": r["why"]} for r in RULES]


_WS = re.compile(r"\s+")


def clean_reason(reason: str) -> str:
    return _WS.sub(" ", (reason or "").strip())[:1000]
