"""The ONLY outbound path to Claude. One permitted operation: extract candidate findings.

Boundary controls enforced here, server-side:
1. Synthetic-data gate: every excerpt must come from a seeded fixture whose (note_id, version,
   sha256(body)) is on the allowlist computed from fixtures/seed.json at startup, and every
   excerpt text must be a substring of that fixture body. Anything else is refused before a
   request object exists. There is no endpoint that accepts pasted or uploaded text.
2. Feature gate: the request may use only APPROVED_OUTBOUND_FEATURES. No tools, no MCP,
   no web, no files, no batches, no threads, no fallbacks (a fallback would silently change the
   model; see docs/04-vendor-checklist.md).
3. The model gets no credentials, no identifiers, and no ability to act. Its output is data
   that app/validate.py checks; it cannot select which chart is updated.
"""
import copy
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass

from . import config
from .prompt import OUTPUT_SCHEMA, SYSTEM_PROMPT

# Never let SDK/HTTP debug logging write request bodies anywhere.
for _name in ("anthropic", "httpx", "httpx2", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)


class BoundaryViolation(Exception):
    """Raised when something other than an approved synthetic fixture tries to leave."""


@dataclass
class CallResult:
    outcome: str            # ok | timeout | rate_limited | provider_error | connection_error |
                            # auth_error | bad_request | refused | truncated | no_replay | blocked
    text: str = ""
    provider_request_id: str = ""
    provenance: str = ""    # live_call | replay:authored_fixture | replay:captured_live
    retryable: bool = False
    model: str = ""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_allowlist(seed_path: str) -> dict:
    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)
    allow = {}
    for n in seed["seeded_notes"] + seed["deliverable_notes"]:
        allow[(n["note_id"], n["version"])] = sha(n["body"])
    return allow


def build_request(sources: list, matched_terms: list, settings: config.Settings) -> dict:
    """Exactly what leaves the boundary. Every field is labelled in docs/03-privacy-assessment.md."""
    user_doc = {
        "watched_terms": matched_terms,
        "excerpts": [{"alias": s["alias"], "role": s["role"], "signed": s["signed"], "text": s["text"]}
                     for s in sources],
    }
    return {
        "model": settings.model,
        "max_tokens": 16000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": json.dumps(user_doc, ensure_ascii=False)}],
        "output_config": {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
                          "effort": settings.effort},
    }


def features_of(req: dict) -> set:
    feats = {"messages"}
    allowed_keys = {"model", "max_tokens", "system", "messages", "output_config"}
    extra = set(req) - allowed_keys
    if extra:
        feats |= {f"unapproved_param:{k}" for k in sorted(extra)}
    oc = req.get("output_config", {})
    if "format" in oc:
        feats.add("structured_outputs")
    if "effort" in oc:
        feats.add("effort")
    if set(oc) - {"format", "effort"}:
        feats |= {f"unapproved_output_config:{k}" for k in sorted(set(oc) - {"format", "effort"})}
    feats.add("adaptive_thinking")  # model default on claude-opus-5-5; listed so review covers it
    return feats


class Adapter:
    def __init__(self, settings: config.Settings, allowlist: dict, replay_dir: str):
        self.settings = settings
        self.allowlist = allowlist
        self.replay_dir = replay_dir
        self.fault = "none"          # demo/test fault injection: none|timeout|rate_limit|outage|malformed|no_finding|refusal
        self._client = None
        self.sent_requests = []      # test/demo capture of outbound bodies (synthetic fixtures only)
        self.is_approved = lambda digest: False   # set by the app: approved synthetic scenario text
        self.allow_real_notes = False             # set by the app: only in clinical mode with a confirmed BAA

    # ---- gates -------------------------------------------------------------------------
    def check_fixture_boundary(self, alias_map: dict, sources: list, fixture_bodies: dict) -> None:
        for s in sources:
            ref = alias_map.get(s["alias"])
            if not ref:
                raise BoundaryViolation("excerpt without backend alias mapping")
            key = (ref["note_id"], ref["version"])
            expected = self.allowlist.get(key)
            body = fixture_bodies.get(key)
            if body is None:
                raise BoundaryViolation("source text missing")
            if self.allow_real_notes:
                pass   # clinical deployment with a confirmed BAA: real notes may be sent (still minimized below)
            elif expected is not None and sha(body) == expected:
                pass   # a seeded synthetic fixture, unmodified
            elif self.is_approved(sha(body)):
                pass   # built-in or instructor-approved synthetic scenario text
            else:
                raise BoundaryViolation("source is not an approved, unmodified synthetic fixture")
            from .excerpt import scrub
            for part in s["text"].split(" [...] "):
                if part not in scrub(body):
                    raise BoundaryViolation("excerpt text not derived from the approved fixture")

    def check_features(self, req: dict) -> None:
        unapproved = features_of(req) - config.APPROVED_OUTBOUND_FEATURES
        if unapproved:
            raise BoundaryViolation("unapproved outbound feature(s): " + ",".join(sorted(unapproved)))

    # ---- call --------------------------------------------------------------------------
    def extract(self, req: dict, replay_key: tuple, alias_subst: dict) -> CallResult:
        self.check_features(req)
        self.sent_requests.append(copy.deepcopy(req))
        if self.settings.mode == "live":
            return self._live(req)
        return self._replay(replay_key, alias_subst)

    def _fault_result(self):
        f = self.fault
        if f == "timeout":
            return CallResult("timeout", retryable=True, provenance=self._prov("fault"))
        if f == "rate_limit":
            return CallResult("rate_limited", retryable=True, provenance=self._prov("fault"))
        if f == "outage":
            return CallResult("provider_error", retryable=True, provenance=self._prov("fault"))
        if f == "refusal":
            return CallResult("refused", provenance=self._prov("fault"))
        return None

    def _prov(self, kind):
        return "replay:fault_injection" if self.settings.mode != "live" else "live_call:fault_injection"

    def _replay(self, key, alias_subst) -> CallResult:
        fr = self._fault_result()
        if fr:
            return fr
        if self.fault == "malformed":
            return CallResult("ok", text='{"status": "findings_identified", "findings": [{"quote": ', provenance="replay:fault_injection")
        if self.fault == "no_finding":
            return CallResult("ok", text=json.dumps({"status": "no_qualifying_finding", "findings": [], "reviewer_note": ""}),
                              provenance="replay:fault_injection")
        path = os.path.join(self.replay_dir, f"{key[0]}_v{key[1]}.json")
        if not os.path.exists(path):
            return CallResult("no_replay", provenance="replay:missing")
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        text = rec["response_text"]
        for placeholder, alias in alias_subst.items():
            text = text.replace(placeholder, alias)
        return CallResult("ok", text=text, provenance="replay:" + rec.get("provenance", "authored_fixture"),
                          model=rec.get("model", ""), provider_request_id=rec.get("provider_request_id", ""))

    def _live(self, req) -> CallResult:
        fr = self._fault_result()
        if fr:
            return fr
        if os.environ.get("ANTHROPIC_LOG", "").lower() == "debug":
            raise BoundaryViolation("refusing live call with SDK debug logging enabled")
        import anthropic  # imported lazily so replay mode works without the package/credentials
        if self._client is None:
            # max_retries=0: retries are owned by the durable job queue so state stays visible.
            self._client = anthropic.Anthropic(timeout=self.settings.request_timeout_s, max_retries=0)
        try:
            msg = self._client.messages.create(**req)
        except anthropic.APITimeoutError:
            return CallResult("timeout", retryable=True, provenance="live_call")
        except anthropic.RateLimitError as e:
            return CallResult("rate_limited", retryable=True, provenance="live_call",
                              provider_request_id=getattr(e, "request_id", "") or "")
        except anthropic.AuthenticationError:
            return CallResult("auth_error", provenance="live_call")
        except anthropic.BadRequestError as e:
            return CallResult("bad_request", provenance="live_call", provider_request_id=getattr(e, "request_id", "") or "")
        except anthropic.APIStatusError as e:
            return CallResult("provider_error", retryable=e.status_code >= 500, provenance="live_call",
                              provider_request_id=getattr(e, "request_id", "") or "")
        except anthropic.APIConnectionError:
            return CallResult("connection_error", retryable=True, provenance="live_call")
        rid = getattr(msg, "_request_id", "") or ""
        if msg.stop_reason == "refusal":
            return CallResult("refused", provenance="live_call", provider_request_id=rid, model=msg.model)
        if msg.stop_reason == "max_tokens":
            return CallResult("truncated", provenance="live_call", provider_request_id=rid, model=msg.model)
        text = next((b.text for b in msg.content if b.type == "text"), "")
        return CallResult("ok", text=text, provenance="live_call", provider_request_id=rid, model=msg.model)
