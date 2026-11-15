"""Record REAL Claude responses for the approved synthetic fixtures, for use in replay mode.

  ANTHROPIC_API_KEY=... .venv/bin/python -m scripts.record_replays

Writes fixtures/replay/<note>_v<version>.json with provenance "captured_live", the model the API
reported, the provider request ID, and the capture date. note_A3 is skipped on purpose: its replay
is a deliberately flawed, authored response used to test validation.
Only approved fixtures can be sent (the adapter's allowlist enforces it).
"""
import json
import os
from datetime import datetime, timezone

from app import config, excerpt
from app.adapter import Adapter, build_request, load_allowlist

ROOT = config.ROOT
SKIP = {("note_A3", 1)}


def main():
    seed = json.load(open(os.path.join(ROOT, "fixtures", "seed.json")))
    s = config.Settings()
    s.mode = "live"
    ad = Adapter(s, load_allowlist(os.path.join(ROOT, "fixtures", "seed.json")), os.path.join(ROOT, "fixtures", "replay"))
    enc_notes = {}
    for n in seed["seeded_notes"]:
        enc_notes.setdefault(n["encounter"], []).append(n)
    for n in seed["deliverable_notes"]:            # deliver in demo order so priors match the demo
        key = (n["note_id"], n["version"])
        earlier = list(enc_notes.get(n["encounter"], []))
        enc_notes.setdefault(n["encounter"], []).append(n)
        if key in SKIP:
            continue
        ex = excerpt.select(n, [e for e in earlier if not (e["note_id"] == n["note_id"] and e["version"] >= n["version"])])
        if ex.abstain_reason:
            print(key, "abstained", ex.abstain_reason)
            continue
        bodies = {(e["note_id"], e["version"]): e["body"] for e in earlier + [n]}
        ad.check_fixture_boundary(ex.alias_map, ex.sources, bodies)
        req = build_request(ex.sources, ex.matched_terms, s)
        res = ad.extract(req, key, {})
        print(key, res.outcome, res.provider_request_id)
        if res.outcome != "ok":
            continue
        text = res.text
        for src in ex.sources:   # store aliases as placeholders so replays bind to fresh aliases
            text = text.replace(src["alias"], "{{CURRENT}}" if src["role"] == "current_note_excerpt" else "{{PRIOR}}")
        rec = {"provenance": "captured_live", "model": res.model, "provider_request_id": res.provider_request_id,
               "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "prompt_version": config.PROMPT_VERSION, "schema_version": config.SCHEMA_VERSION,
               "provenance_note": "Captured from a live Claude Messages API call on an approved synthetic fixture.",
               "response_text": text}
        with open(os.path.join(ROOT, "fixtures", "replay", f"{key[0]}_v{key[1]}.json"), "w") as f:
            json.dump(rec, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
