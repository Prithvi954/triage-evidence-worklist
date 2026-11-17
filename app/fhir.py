"""FHIR R4 connector: read in-progress encounters, patients, and clinical notes from an EHR.

Works with any FHIR R4 server, e.g. Epic/Cerner developer sandboxes or the public HAPI test
server (synthetic data). Configure with environment variables (never in code):
  TRIAGE_FHIR_BASE_URL   e.g. https://hapi.fhir.org/baseR4
  TRIAGE_FHIR_TOKEN      OAuth2 bearer token for SMART backend services (optional for open sandboxes)
  TRIAGE_FHIR_ORG        which unit the encounters belong to (default org_n)

Read-only: the connector never writes back to the EHR. Notes arrive through the same intake path
as the simulator, so every safety rule applies: notes are reviewed by people, priority changes need a
charge nurse, and real note text is sent to the AI only in clinical mode with a confirmed BAA.
"""
import base64
import json
import re
import urllib.parse
import urllib.request
from datetime import date

from .clock import iso, parse
from .safelog import log_event, report_error

SAFE = re.compile(r"[^A-Za-z0-9_-]")
GENERIC_WORDS = {"patient", "person", "synthetic", "unknown", "bed", "the", "and", "demo"}


class FhirError(Exception):
    pass


class FhirClient:
    def __init__(self, base_url, token="", timeout=20):
        u = urllib.parse.urlparse(base_url)
        local = u.hostname in ("127.0.0.1", "localhost")
        if u.scheme != "https" and not (u.scheme == "http" and local):
            raise FhirError("FHIR base URL must use https")
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def get(self, path, params=None):
        url = f"{self.base}/{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
        req = urllib.request.Request(url, headers={"Accept": "application/fhir+json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:   # never include the URL/token in the error we keep
            raise FhirError(type(e).__name__) from None

    def search(self, resource, params, limit=200):
        out, bundle = [], self.get(resource, params)
        while bundle:
            out += [e["resource"] for e in bundle.get("entry", []) if "resource" in e]
            nxt = next((l["url"] for l in bundle.get("link", []) if l.get("relation") == "next"), None)
            if not nxt or len(out) >= limit:
                break
            if not nxt.startswith(self.base):
                break   # don't follow links to other hosts
            bundle = self.get(nxt[len(self.base) + 1:])
        return out[:limit]


def _age(birth):
    try:
        b = date.fromisoformat(birth[:10])
        t = date.today()
        return t.year - b.year - ((t.month, t.day) < (b.month, b.day))
    except (TypeError, ValueError):
        return None


def _name(patient):
    for n in patient.get("name", []):
        if n.get("text"):
            return n["text"]
        given = " ".join(n.get("given", []))
        fam = n.get("family", "")
        if given or fam:
            return f"{fam}, {given}".strip(", ")
    return "Unknown patient"


def _note_text(client, doc):
    for c in doc.get("content", []):
        att = c.get("attachment", {})
        if not str(att.get("contentType", "text/plain")).startswith("text/plain"):
            continue
        if att.get("data"):
            return base64.b64decode(att["data"]).decode("utf-8", "replace")
        if att.get("url", "").startswith("Binary/"):
            b = client.get(att["url"])
            if b.get("data"):
                return base64.b64decode(b["data"]).decode("utf-8", "replace")
    return None


def _ts(value, fallback):
    if not value:
        return fallback
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return iso(dt)
    except ValueError:
        return fallback


def sync(svc, client, org, max_encounters=100):
    """One pass: upsert in-progress encounters and ingest new/updated notes. Idempotent."""
    db = svc.db
    now = iso(svc.clock.now())
    stats = {"encounters": 0, "notes": 0, "closed": 0}
    try:
        encounters = client.search("Encounter", {"status": "in-progress", "_count": 50}, limit=max_encounters)
        seen = set()
        for enc in encounters:
            eid = "fhir_" + SAFE.sub("_", enc["id"])[:60]
            seen.add(eid)
            pref = (enc.get("subject") or {}).get("reference", "")
            patient = client.get(pref) if pref.startswith("Patient/") else {}
            name = _name(patient)
            ident = next((i.get("value") for i in patient.get("identifier", []) if i.get("value")), None)
            display = {"age": _age(patient.get("birthDate")), "sex": (patient.get("gender") or "")[:1].upper() or None,
                       "complaint": next((r.get("text") or (r.get("coding") or [{}])[0].get("display", "")
                                          for r in enc.get("reasonCode", [])), "")[:60],
                       "location": next(((l.get("location") or {}).get("display", "") for l in enc.get("location", [])), "")[:30]}
            header = {"name": name, "mrn": ident, "dob": patient.get("birthDate"),
                      "phone": next((t.get("value") for t in patient.get("telecom", []) if t.get("system") == "phone"), None),
                      "address": next((a.get("text") or " ".join(a.get("line", [])) for a in patient.get("address", [])), None)}
            arrived = _ts((enc.get("period") or {}).get("start"), now)
            existing = db.one("SELECT * FROM encounters WHERE id=?", (eid,))
            with db.tx() as c:
                if not existing:
                    c.execute("INSERT INTO encounters (id, org, label, arrived_at, priority, primary_user, display_json) "
                              "VALUES (?,?,?,?,?,?,?)", (eid, org, name[:60], arrived, None, None, json.dumps(display)))
                    c.execute("INSERT INTO encounter_headers VALUES (?,?)", (eid, json.dumps(header)))
                    stats["encounters"] += 1
                else:
                    c.execute("UPDATE encounters SET display_json=? WHERE id=?", (json.dumps(display), eid))
                    c.execute("UPDATE encounter_headers SET header_json=? WHERE encounter_id=?", (json.dumps(header), eid))
            enc_row = db.one("SELECT * FROM encounters WHERE id=?", (eid,))
            if enc_row["status"] != "active":
                continue
            docs = client.search("DocumentReference", {"encounter": f"Encounter/{enc['id']}", "_count": 50}, limit=100)
            for doc in sorted(docs, key=lambda d: d.get("date") or ""):
                if doc.get("status") not in (None, "current"):
                    continue
                text = _note_text(client, doc)
                if not text or not text.strip():
                    continue
                nid = "fhir:" + SAFE.sub("_", doc["id"])[:60]
                try:
                    version = int((doc.get("meta") or {}).get("versionId") or 1)
                except ValueError:
                    version = 1
                r = svc.ingest_note(enc_row, {"note_id": nid, "version": version, "body": text[:20000],
                                              "signed_at": _ts(doc.get("date"), now), "author_employee_id": None}, fixture=False)
                if not r.get("duplicate"):
                    stats["notes"] += 1
        # encounters no longer in progress at the source are closed here (never deleted)
        for row in db.q("SELECT id FROM encounters WHERE org=? AND id LIKE 'fhir_%' AND status='active'", (org,)):
            if row["id"] not in seen:
                svc.sim_close(org, row["id"], "closed")
                stats["closed"] += 1
        with db.tx() as c:
            c.execute("INSERT INTO fhir_sync (source, org, last_sync_at, last_error, encounters, notes) VALUES (?,?,?,?,?,?) "
                      "ON CONFLICT(source) DO UPDATE SET last_sync_at=excluded.last_sync_at, last_error=NULL, "
                      "encounters=fhir_sync.encounters+excluded.encounters, notes=fhir_sync.notes+excluded.notes",
                      (client.base, org, now, None, stats["encounters"], stats["notes"]))
        log_event(now, "fhir.sync_ok", encounters=stats["encounters"], notes=stats["notes"], closed=stats["closed"])
        return stats
    except Exception as e:
        report_error(now, "fhir.sync", e)
        with db.tx() as c:
            c.execute("INSERT INTO fhir_sync (source, org, last_sync_at, last_error) VALUES (?,?,?,?) "
                      "ON CONFLICT(source) DO UPDATE SET last_error=excluded.last_error, last_sync_at=excluded.last_sync_at",
                      (client.base, org, now, type(e).__name__))
        raise


def identifiers_for(header):
    """Strings that identify the patient, to be removed from any text before it could reach the AI."""
    out = set()
    for k in ("name", "mrn", "dob", "phone", "address"):
        v = header.get(k)
        if not v or not isinstance(v, str):
            continue
        out.add(v)
        if k == "name":
            out.update(p for p in re.split(r"[\s,]+", v) if len(p) >= 3 and p.lower() not in GENERIC_WORDS)
    return sorted(out, key=len, reverse=True)
