"""Compare Claude with simple baselines on the same synthetic notes.

  .venv/bin/python -m scripts.evaluate --mode replay   # baselines + AUTHORED replays (not model evidence)
  ANTHROPIC_API_KEY=... .venv/bin/python -m scripts.evaluate --mode live   # baselines + real Claude calls

Scores are per expected finding on (subject, assertion, time_context, change). The set is tiny and
synthetic: it shows WHERE approaches differ. It says nothing about clinical performance.
"""
import argparse
import json
import os
import sys

from app import baseline, config, excerpt, validate
from app.adapter import Adapter, build_request, load_allowlist

ROOT = config.ROOT
FIELDS = ("subject", "assertion", "time_context", "change")


def load():
    seed = json.load(open(os.path.join(ROOT, "fixtures", "seed.json")))
    notes = {(n["note_id"], n["version"]): n for n in seed["seeded_notes"] + seed["deliverable_notes"]}
    gold = json.load(open(os.path.join(ROOT, "fixtures", "eval_gold.json")))["cases"]
    return seed, notes, gold


def match_baseline(found, exp):
    for f in found:
        if f["term"] == exp["term"] and exp["sentence_contains"] in f["sentence"]:
            return dict(f, change="not_comparable")   # baselines cannot compare across notes
    return None


def match_claude(findings, exp):
    for f in findings:
        if f["checks"]["rejected"]:
            continue
        fin = f["finding"]
        sent = f["checks"].get("full_sentence") or fin["quote"]
        if exp["term"].lower() in (fin["finding_label"] + " " + fin["quote"]).lower() and exp["sentence_contains"] in sent:
            return dict(fin, change=fin["comparison"]["change"])
    return None


def score(pred, exp):
    if not pred:
        return {k: False for k in FIELDS}, "missed"
    return {k: pred.get(k) == exp[k] for k in FIELDS}, ""


def run_claude(case, notes, mode, adapter):
    cur = dict(notes[tuple(case["note"])])
    earlier = [dict(notes[tuple(case["prior"])])] if case["prior"] else []
    ex = excerpt.select(cur, earlier)
    if ex.abstain_reason:
        return [], "abstained:" + ex.abstain_reason, ""
    bodies = {(n["note_id"], n["version"]): n["body"] for n in earlier + [cur]}
    adapter.check_fixture_boundary(ex.alias_map, ex.sources, bodies)
    req = build_request(ex.sources, ex.matched_terms, adapter.settings)
    subst = {("{{CURRENT}}" if s["role"] == "current_note_excerpt" else "{{PRIOR}}"): s["alias"] for s in ex.sources}
    res = adapter.extract(req, tuple(case["note"]), subst)
    if res.outcome != "ok":
        return [], res.outcome, res.provenance
    v = validate.validate(res.text, ex.sources, ex.alias_map, bodies, ex.repeated_sentences)
    return v["findings"], v["state"], res.provenance


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["replay", "live"], default="replay")
    args = ap.parse_args()
    seed, notes, gold = load()
    s = config.Settings()
    s.mode = args.mode
    adapter = Adapter(s, load_allowlist(os.path.join(ROOT, "fixtures", "seed.json")), os.path.join(ROOT, "fixtures", "replay"))
    totals = {name: {k: 0 for k in FIELDS} for name in ("phrase_match", "negex_lite", "claude")}
    n_exp = 0
    rows = []
    provs = set()
    for case in gold:
        body = notes[tuple(case["note"])]["body"]
        pm, nx = baseline.phrase_match(body), baseline.negex_lite(body)
        cl, state, prov = run_claude(case, notes, args.mode, adapter)
        provs.add(prov or "none")
        for exp in case["expected"]:
            n_exp += 1
            for name, pred in (("phrase_match", match_baseline(pm, exp)), ("negex_lite", match_baseline(nx, exp)),
                               ("claude", match_claude(cl, exp))):
                sc, note = score(pred, exp)
                for k in FIELDS:
                    totals[name][k] += sc[k]
                rows.append({"case": case["case"], "term": exp["term"], "approach": name, "state": state if name == "claude" else "",
                             **{k: ("ok" if sc[k] else f"WRONG ({pred.get(k) if pred else note})") for k in FIELDS}})
    claude_label = {"replay": "claude (AUTHORED REPLAY, not a model measurement)", "live": "claude (live)"}[args.mode]
    print(f"Synthetic evaluation, {n_exp} expected findings, mode={args.mode}, provenance={sorted(provs)}\n")
    print(f"| approach | " + " | ".join(FIELDS) + " |")
    print("|---|" + "---|" * len(FIELDS))
    for name, t in totals.items():
        label = claude_label if name == "claude" else name
        print(f"| {label} | " + " | ".join(f"{t[k]}/{n_exp}" for k in FIELDS) + " |")
    print("\nPer-finding detail:")
    for r in rows:
        print(f"- [{r['approach']}] {r['case']} / {r['term']}: " + ", ".join(f"{k}={r[k]}" for k in FIELDS) +
              (f" (state={r['state']})" if r["state"] else ""))
    out = os.path.join(ROOT, "data", f"eval_{args.mode}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"mode": args.mode, "provenance": sorted(provs), "totals": totals, "n": n_exp, "rows": rows}, open(out, "w"), indent=2)
    print(f"\nSaved {out}")


if __name__ == "__main__":
    sys.exit(main())
