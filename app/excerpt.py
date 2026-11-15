"""Local evidence preparation: choose the smallest passage that keeps enough context to interpret it.

Nothing here calls the network. Output is (a) the exact excerpt texts that may leave the
facility boundary, keyed by temporary opaque aliases, and (b) a backend-only alias map.

Rules (SELECTOR_VERSION excerpt-selector-v2):
- Only the clinical narrative body is eligible. Header fields (name, MRN, DOB, address,
  phone, author employee ID) are never read here.
- Watched terms are configured by the facility (demo: "demo signal A", "demo signal B").
- For each sentence containing a watched term, include that sentence plus one neighbour on
  each side (negation, subject, timing, and corrections often sit in the neighbour).
- If the note has no watched term, abstain: no model call; the note still enters the
  review inbox (the model never decides whether a note is seen).
- Comparison: include at most 2 sentences from ONE earlier source in the same encounter
  that mention the same watched term(s): the previous version of this note if it is an
  amendment, otherwise the most recently signed earlier note.
- Hard cap on characters. If the matched sentences alone exceed the cap, abstain rather
  than truncate mid-context (truncation can drop "denies" or "resolved").
"""
import re
import secrets
from dataclasses import dataclass, field

WATCH_TERMS = ("demo signal A", "demo signal B")
MAX_CURRENT_CHARS = 900
MAX_PRIOR_CHARS = 400
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


# Identifier-shaped tokens removed before anything leaves the boundary. This is a minimization
# backstop, NOT HIPAA de-identification: prose can still identify a person by combination of facts.
_SCRUB = [
    (re.compile(r"https?://\S+|www\.\S+", re.I), "[link removed]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[email removed]"),
    (re.compile(r"\b\d{3}[-. ]\d{3}[-. ]\d{4}\b"), "[phone removed]"),
    (re.compile(r"\b(?=[A-Z0-9-]*\d)[A-Z0-9]{2,}(?:-[A-Z0-9]{2,})+\b"), "[code removed]"),
    (re.compile(r"\b\d{5,}\b"), "[number removed]"),
]


def scrub(text: str) -> str:
    for rx, rep in _SCRUB:
        text = rx.sub(rep, text)
    return text


def split_sentences(text: str):
    return [s.strip() for s in _SENT.split(text.strip()) if s.strip()]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def terms_in(sentence: str):
    low = sentence.lower()
    return [t for t in WATCH_TERMS if t.lower() in low]


@dataclass
class Excerpt:
    abstain_reason: str = ""            # "" | no_watch_terms | context_exceeds_bound
    sources: list = field(default_factory=list)   # [{alias, role, signed, text}] -> leaves boundary
    alias_map: dict = field(default_factory=dict) # alias -> {note_id, version, sentence_idx} (backend only)
    matched_terms: list = field(default_factory=list)
    repeated_sentences: list = field(default_factory=list)  # [{sentence, earlier_note_count}]
    omitted_fields: list = field(default_factory=list)
    comparison_available: bool = False


def _alias(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3)}"


def select(current: dict, earlier: list) -> Excerpt:
    """current: {note_id, version, body}; earlier: same-encounter notes (incl. prior versions),
    each {note_id, version, body, signed_at}, any order."""
    ex = Excerpt(omitted_fields=["patient name", "MRN", "date of birth", "address", "phone",
                                 "author employee ID", "absolute dates/times", "other encounters",
                                 "identifier-shaped tokens in text (codes, links, emails, phones, long numbers)",
                                 "other patients", "sentences without watched terms (beyond 1 neighbour)"])
    sents = split_sentences(scrub(current["body"]))
    hits =[i for i, s in enumerate(sents) if terms_in(s)]
    if not hits:
        ex.abstain_reason = "no_watch_terms"
        return ex
    matched = sorted({t for i in hits for t in terms_in(sents[i])})
    ex.matched_terms = matched

    keep = set()
    for i in hits:
        keep.update(j for j in (i - 1, i, i + 1) if 0 <= j < len(sents))
    if sum(len(sents[i]) + 1 for i in hits) > MAX_CURRENT_CHARS:
        ex.abstain_reason = "context_exceeds_bound"
        return ex
    # Drop neighbours (never matched sentences) until within the cap.
    order = sorted(keep)
    while sum(len(sents[i]) + 1 for i in order) > MAX_CURRENT_CHARS:
        neighbours = [i for i in order if i not in hits]
        order.remove(neighbours[-1])
    text = _join(sents, order)
    cur_alias = _alias("SRC")
    ex.sources.append({"alias": cur_alias, "role": "current_note_excerpt", "signed": "today, this visit", "text": text})
    ex.alias_map[cur_alias] = {"note_id": current["note_id"], "version": current["version"], "sentence_idx": order}

    # Copied-text detection is local and deterministic.
    for i in hits:
        n = norm(sents[i])
        count = sum(1 for e in earlier if e["note_id"] != current["note_id"]
                    and e["signed_at"] < current.get("signed_at", "9999")
                    and any(norm(s) == n for s in split_sentences(e["body"])))
        if count:
            ex.repeated_sentences.append({"sentence": sents[i], "earlier_note_count": count})

    prior = _pick_prior(current, earlier)
    if prior:
        psents = split_sentences(scrub(prior["body"]))
        pick = [j for j, s in enumerate(psents) if any(t in terms_in(s) for t in matched)][:2]
        ptext = _join(psents, pick)
        if pick and len(ptext) <= MAX_PRIOR_CHARS:
            pal = _alias("PRI")
            role = "earlier_version_of_same_note" if prior["note_id"] == current["note_id"] else "earlier_note_same_encounter"
            ex.sources.append({"alias": pal, "role": role, "signed": "earlier, this visit", "text": ptext})
            ex.alias_map[pal] = {"note_id": prior["note_id"], "version": prior["version"], "sentence_idx": pick}
            ex.comparison_available = True
    return ex


def _join(sents, idxs):
    out, last = [], None
    for i in idxs:
        if last is not None and i != last + 1:
            out.append("[...]")
        out.append(sents[i])
        last = i
    return " ".join(out)


def _pick_prior(current, earlier):
    same = [e for e in earlier if e["note_id"] == current["note_id"] and e["version"] < current["version"]]
    if same:
        return max(same, key=lambda e: e["version"])
    others = [e for e in earlier if e["note_id"] != current["note_id"]
              and e["signed_at"] < current.get("signed_at", "9999")]
    return max(others, key=lambda e: e["signed_at"]) if others else None


def full_sentence_containing(body: str, quote: str):
    """Return the complete sentence(s) of the source note that contain `quote`."""
    for s in split_sentences(body):
        if quote.strip() and quote.strip() in s:
            return s
    # quote may span a sentence boundary
    idx = body.find(quote.strip())
    if idx < 0:
        return None
    start = max(body.rfind(". ", 0, idx) + 2, 0) if body.rfind(". ", 0, idx) >= 0 else 0
    end = body.find(". ", idx + len(quote.strip()))
    return body[start:(end + 1 if end >= 0 else len(body))].strip()
