"""Check Claude's response against the source text before any clinician sees it.

What validation CAN establish: the response is well-formed; each quote is verbatim in the excerpt
that was sent AND in the stored note; aliases refer to this request only; comparisons cite a
real earlier excerpt.

What it CANNOT establish: that the interpretation is right. An exact quote proves the words
appear in the note, not that "reported / patient / current" is the correct reading. So we also
run deterministic context cues over the FULL sentence and surface disagreements as warnings,
and the clinician always sees the full sentence one action away. Remaining semantic errors are
recorded on the finding (checks.warnings) and in the clinician's decision.
"""
import json
import re

from .excerpt import full_sentence_containing, scrub

MAX_FINDINGS = 12
MAX_QUOTE = 600
MAX_NOTE = 400

_ENUMS = {
    "status": {"findings_identified", "no_qualifying_finding", "insufficient_context", "conflicting_evidence"},
    "subject": {"patient", "family_member", "other_person", "unclear"},
    "assertion": {"reported", "denied", "uncertain"},
    "time_context": {"current", "past_resolved", "past_unspecified", "unclear"},
    "change": {"newly_reported", "denied_then_reported", "reported_then_denied", "unchanged", "not_comparable"},
}
_FINDING_KEYS = {"source_alias", "quote", "finding_label", "subject", "assertion", "time_context",
                 "time_evidence_quote", "uncertainty_reason", "comparison"}
_CMP_KEYS = {"supported", "prior_alias", "prior_quote", "change"}

NEGATION = re.compile(r"\b(denies|denied|deny|no|not|without|negative for|resolved|absent)\b", re.I)
FAMILY = re.compile(r"\b(mother|father|sister|brother|family|aunt|uncle|grandmother|grandfather|son|daughter|spouse)\b", re.I)
PAST = re.compile(r"\b(last week|last month|last year|yesterday|resolved|previously|history of|prior to)\b", re.I)
URL = re.compile(r"https?://|www\.", re.I)


class Malformed(Exception):
    pass


def _check_schema(obj):
    if not isinstance(obj, dict) or set(obj) != {"status", "findings", "reviewer_note"}:
        raise Malformed("top-level keys")
    if obj["status"] not in _ENUMS["status"]:
        raise Malformed("status enum")
    if not isinstance(obj["findings"], list) or len(obj["findings"]) > MAX_FINDINGS:
        raise Malformed("findings list")
    if not isinstance(obj["reviewer_note"], str) or len(obj["reviewer_note"]) > MAX_NOTE:
        raise Malformed("reviewer_note")
    for f in obj["findings"]:
        if not isinstance(f, dict) or set(f) != _FINDING_KEYS:
            raise Malformed("finding keys")
        for k in ("source_alias", "quote", "finding_label", "time_evidence_quote", "uncertainty_reason"):
            if not isinstance(f[k], str) or len(f[k]) > MAX_QUOTE:
                raise Malformed(f"finding.{k}")
        for k in ("subject", "assertion", "time_context"):
            if f[k] not in _ENUMS[k]:
                raise Malformed(f"finding.{k} enum")
        c = f["comparison"]
        if not isinstance(c, dict) or set(c) != _CMP_KEYS or not isinstance(c["supported"], bool) \
                or c["change"] not in _ENUMS["change"] or not isinstance(c["prior_quote"], str) \
                or not isinstance(c["prior_alias"], str):
            raise Malformed("comparison")
    if obj["status"] == "findings_identified" and not obj["findings"]:
        raise Malformed("status says findings but none listed")
    if obj["status"] == "no_qualifying_finding" and obj["findings"]:
        raise Malformed("status says no finding but findings listed")


def validate(response_text: str, sources: list, alias_map: dict, note_bodies: dict, repeated: list):
    """sources: what was sent [{alias, role, text}]; alias_map: backend alias -> note ref;
    note_bodies: (note_id, version) -> stored body. Returns a dict; never raises."""
    out = {"state": "malformed", "errors": [], "findings": [], "reviewer_note": "", "reviewer_note_flags": []}
    try:
        obj = json.loads(response_text)
        _check_schema(obj)
    except (ValueError, Malformed) as e:
        out["errors"].append(f"malformed: {type(e).__name__}: {e}" if isinstance(e, Malformed) else "malformed: not JSON")
        return out

    sent = {s["alias"]: s for s in sources}
    prior_aliases = {a for a, s in sent.items() if s["role"] != "current_note_excerpt"}
    repeated_norm = {r["sentence"].strip().lower(): r["earlier_note_count"] for r in repeated}

    for i, f in enumerate(obj["findings"]):
        checks = {"alias_known": False, "quote_in_excerpt": False, "quote_in_stored_note": False,
                  "full_sentence": None, "time_quote_verified": None, "comparison_verified": None,
                  "repeated_text_earlier_notes": 0, "warnings": [], "rejected": False}
        src = sent.get(f["source_alias"])
        ref = alias_map.get(f["source_alias"])
        if src and ref:
            checks["alias_known"] = True
            checks["quote_in_excerpt"] = bool(f["quote"].strip()) and f["quote"] in src["text"]
            # Compare against the same scrubbed text the model saw; the clinician still has the original note.
            body = scrub(note_bodies.get((ref["note_id"], ref["version"]), ""))
            checks["quote_in_stored_note"] = bool(f["quote"].strip()) and f["quote"] in body
            if checks["quote_in_stored_note"]:
                sentence = full_sentence_containing(body, f["quote"])
                checks["full_sentence"] = sentence
                _context_warnings(f, sentence, checks)
                checks["repeated_text_earlier_notes"] = repeated_norm.get((sentence or "").strip().lower(), 0)
                if checks["repeated_text_earlier_notes"]:
                    checks["warnings"].append(
                        f"This sentence also appears in {checks['repeated_text_earlier_notes']} earlier note(s) "
                        "for this encounter. It may be copied text, not an independent observation.")
            checks["source_role"] = src["role"]
            checks["source_note"] = {"note_id": ref["note_id"], "version": ref["version"]}
        if f["time_evidence_quote"]:
            checks["time_quote_verified"] = bool(src) and f["time_evidence_quote"] in src["text"]
            if not checks["time_quote_verified"]:
                checks["warnings"].append("Time wording quoted by the model was not found in the excerpt.")
        cmp_ = f["comparison"]
        if cmp_["supported"] or cmp_["change"] != "not_comparable":
            ok = cmp_["prior_alias"] in prior_aliases and bool(cmp_["prior_quote"]) \
                and cmp_["prior_quote"] in sent[cmp_["prior_alias"]]["text"] if cmp_["prior_alias"] in sent else False
            checks["comparison_verified"] = ok
            if not ok:
                checks["warnings"].append("Comparison claim is not supported by a supplied earlier excerpt; "
                                          "treated as not comparable.")
                f = dict(f, comparison={"supported": False, "prior_alias": "", "prior_quote": "", "change": "not_comparable"})
            else:
                pref = alias_map[cmp_["prior_alias"]]
                checks["prior_note"] = {"note_id": pref["note_id"], "version": pref["version"]}
        if not (checks["alias_known"] and checks["quote_in_excerpt"] and checks["quote_in_stored_note"]):
            checks["rejected"] = True
            checks["warnings"].append("Quote could not be matched verbatim to the source; finding not shown as evidence.")
        f = dict(f)
        f.pop("source_alias", None)   # aliases are request-scoped; never persisted as identifiers
        if isinstance(f.get("comparison"), dict):
            f["comparison"] = {k: v for k, v in f["comparison"].items() if k != "prior_alias"}
        f["group"] = _group(f)
        out["findings"].append({"idx": i, "finding": f, "checks": checks})

    note = obj["reviewer_note"]
    if URL.search(note):
        out["reviewer_note_flags"].append("contains_url_rendered_as_text")
    out["reviewer_note"] = note
    usable = [x for x in out["findings"] if not x["checks"]["rejected"]]
    if obj["status"] == "findings_identified" and not usable:
        out["state"] = "malformed"
        out["errors"].append("no finding survived source verification")
    else:
        out["state"] = obj["status"] if obj["status"] != "findings_identified" else "findings"
    return out


def _context_warnings(f, sentence, checks):
    s = sentence or ""
    q = f["quote"]
    neg_s, neg_q = NEGATION.findall(s), NEGATION.findall(q)
    if f["time_context"] == "past_resolved":   # "resolved" is expected wording for a resolved finding
        neg_s = [w for w in neg_s if w.lower() != "resolved"]
        neg_q = [w for w in neg_q if w.lower() != "resolved"]
    if f["assertion"] == "reported" and neg_s:
        missing = [w for w in neg_s if w.lower() not in (x.lower() for x in neg_q)]
        if missing:
            checks["warnings"].append(f"The full sentence contains \"{missing[0]}\", which the quote leaves out. "
                                      "Check whether the finding is denied or resolved.")
        else:
            checks["warnings"].append(f"The sentence contains \"{neg_s[0]}\" but the model labelled it reported. Verify.")
    if f["subject"] == "patient" and FAMILY.search(s):
        checks["warnings"].append(f"The sentence mentions \"{FAMILY.search(s).group(0)}\"; check who the finding is about.")
    if f["time_context"] == "current" and PAST.search(s):
        checks["warnings"].append(f"The sentence contains \"{PAST.search(s).group(0)}\"; check when this was observed.")


def _group(f):
    if f["subject"] == "patient" and f["assertion"] == "reported" and f["time_context"] == "current":
        return "patient_current_candidate"
    if f["subject"] != "patient":
        return "not_about_patient"
    if f["assertion"] == "denied":
        return "denied"
    if f["time_context"] in ("past_resolved", "past_unspecified"):
        return "historical"
    return "needs_interpretation"
