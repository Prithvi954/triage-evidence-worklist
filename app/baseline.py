"""Comparison baselines that run on the same synthetic notes as Claude.

1. phrase_match: the simplest possible approach. Any occurrence of a watched term counts as a
   current, reported patient finding.
2. negex_lite: phrase match plus a NegEx-style negation window (Chapman et al., 2001): a
   negation cue within 5 tokens before the term, in the same sentence, flips it to "denied".
   It does not model subject (family member), time context, or corrections across notes.
"""
import re

from .excerpt import WATCH_TERMS, split_sentences

_NEG_CUES = ("denies", "denied", "no", "not", "without", "negative for", "free of")


def phrase_match(text: str):
    out = []
    for s in split_sentences(text):
        for t in WATCH_TERMS:
            if t.lower() in s.lower():
                out.append({"term": t, "subject": "patient", "assertion": "reported", "time_context": "current",
                            "sentence": s})
    return out


def negex_lite(text: str):
    out = []
    for s in split_sentences(text):
        low = s.lower()
        for t in WATCH_TERMS:
            pos = low.find(t.lower())
            if pos < 0:
                continue
            window = re.findall(r"[a-z']+", low[:pos])[-5:]
            joined = " ".join(window)
            negated = any(re.search(r"\b" + re.escape(c) + r"\b", joined) for c in _NEG_CUES)
            out.append({"term": t, "subject": "patient", "assertion": "denied" if negated else "reported",
                        "time_context": "current", "sentence": s})
    return out
