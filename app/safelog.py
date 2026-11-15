"""Content-free logging.

Rule: application logs and error reports carry event names, opaque IDs, versions, enum
states, counts, and exception *class names*. They never carry note text, quotes, model
output, request bodies, or exception messages (provider errors can echo the request).

Enforcement is structural: every field value must match a narrow token pattern or it is
replaced with a marker. This is a backstop; code should not pass content in the first place.
"""
import json
import re
import threading

_TOKEN = re.compile(r"^[A-Za-z0-9_.:\-]{0,80}$")
_lock = threading.Lock()
_paths = {"log": None, "errors": None}


def configure(log_path: str, error_path: str) -> None:
    _paths["log"] = log_path
    _paths["errors"] = error_path


def _clean(v):
    if v is None or isinstance(v, (bool, int, float)):
        return v
    s = str(v)
    return s if _TOKEN.match(s) else "[withheld:non-token]"


def _write(kind: str, record: dict) -> None:
    path = _paths.get(kind)
    if not path:
        return
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")


def log_event(ts: str, event: str, **fields) -> dict:
    rec = {"ts": ts, "event": _clean(event)}
    rec.update({k: _clean(v) for k, v in fields.items()})
    _write("log", rec)
    return rec


def report_error(ts: str, where: str, exc: BaseException, **fields) -> dict:
    """Record that an error happened without recording what it said."""
    rec = {"ts": ts, "where": _clean(where), "exc_class": type(exc).__name__}
    rid = getattr(exc, "request_id", None)
    if rid:
        rec["provider_request_id"] = _clean(rid)
    status = getattr(exc, "status_code", None)
    if status is not None:
        rec["status_code"] = status
    rec.update({k: _clean(v) for k, v in fields.items()})
    _write("errors", rec)
    return rec
