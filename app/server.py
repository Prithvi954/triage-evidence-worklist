"""HTTP server (stdlib). The browser talks only to this server; it never calls Anthropic.

Run:  .venv/bin/python -m app.server
"""
import json
import os
import re
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import config, safelog
from .adapter import Adapter, load_allowlist
from .clock import Clock, iso
from .db import Store
from .services import BadInput, Conflict, Forbidden, NotFound, Service

STATIC = os.path.join(config.ROOT, "static")
SEED = os.path.join(config.ROOT, "fixtures", "seed.json")
MAX_BODY = 16 * 1024
SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# Generic, privacy-preserving announcement text. No patient labels, no clinical content.
ANNOUNCE = {
    "note_received": lambda p: f"New note awaiting clinical review. {p.get('unreviewed', 0)} unreviewed in evidence inbox.",
    "evidence_ready": lambda p: f"New evidence awaiting clinical review. {p.get('unreviewed', 0)} unreviewed in evidence inbox.",
    "ai_status": lambda p: None,   # status shown in the inbox row; announced only when it means manual work (below)
    "item_reviewed": lambda p: None,
    "queue_changed": lambda p: "Care queue order updated by a clinician decision.",
    "manual_escalation": lambda p: "Manual escalation raised. See overdue and owned actions.",
    "obligation_overdue": lambda p: "An action is overdue. See overdue and owned actions.",
    "obligation_changed": lambda p: None,
    "feed_status": lambda p: ("EHR feed may be stalled. Data may be out of date." if p.get("stale")
                              else "EHR feed messages resumed."),
    "encounter_closed": lambda p: None,
    "emergency_flag": lambda p: "Possible emergency flagged for a patient. Open the Emergencies page to check it.",
    "emergency_updated": lambda p: "An emergency flag was updated. See the Emergencies page.",
    "chart_note_added": lambda p: None,
    "access_revoked": lambda p: None,
}


class Broker:
    """Fan-out of outbox events to live SSE subscribers, per org."""

    def __init__(self):
        self.lock = threading.Lock()
        self.subs = []   # [(org, user_id, list_queue, threading.Event)]

    def subscribe(self, org, user_id):
        q, ev = [], threading.Event()
        with self.lock:
            self.subs.append((org, user_id, q, ev))
        return q, ev

    def unsubscribe(self, q):
        with self.lock:
            self.subs = [s for s in self.subs if s[2] is not q]

    def publish(self, row):
        with self.lock:
            for org, uid, q, ev in self.subs:
                if org == row["org"]:
                    q.append(row)
                    ev.set()


class App:
    def __init__(self, settings=None, clock=None):
        self.settings = settings or config.Settings()
        self.clock = clock or Clock(frozen=False)
        safelog.configure(self.settings.log_path, self.settings.error_report_path)
        self.store = Store(self.settings.db_path)
        with open(SEED, encoding="utf-8") as f:
            self.seed = json.load(f)
        self.adapter = Adapter(self.settings, load_allowlist(SEED), os.path.join(config.ROOT, "fixtures", "replay"))
        self.svc = Service(self.settings, self.clock, self.store, self.adapter, self.seed)
        self.broker = Broker()
        if not self.store.one("SELECT 1 FROM orgs LIMIT 1"):
            self.svc.seed_db()
        fault = self.store.one("SELECT v FROM sim WHERE k='provider_fault'")
        self.adapter.fault = fault["v"] if fault else "none"
        self._stop = threading.Event()

    def dispatch_outbox(self):
        rows = self.store.q("SELECT * FROM outbox WHERE delivered_at IS NULL ORDER BY seq")
        for r in rows:
            self.broker.publish(r)
            with self.store.tx() as c:
                c.execute("UPDATE outbox SET delivered_at=? WHERE seq=?", (iso(self.clock.now()), r["seq"]))
        return len(rows)

    def worker_loop(self):
        while not self._stop.is_set():
            try:
                self.svc.run_jobs_once()
                self.svc.tick()
                self.dispatch_outbox()
            except Exception as e:  # keep running; record class only
                safelog.report_error(iso(self.clock.now()), "worker_loop", e)
            self._stop.wait(0.5)

    def start_worker(self):
        t = threading.Thread(target=self.worker_loop, daemon=True)
        t.start()
        return t


def make_handler(app: App):
    svc = app.svc

    class H(BaseHTTPRequestHandler):
        server_version = "TriageDemo"
        sys_version = ""

        def log_message(self, fmt, *args):   # default handler logs to stderr incl. paths; keep IDs only
            safelog.log_event(iso(app.clock.now()), "http", method=self.command,
                              status=args[1] if len(args) > 1 else "")

        # ---- helpers
        def _headers(self, code, ctype="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
                             "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def _json(self, code, obj, extra=None):
            body = json.dumps(obj).encode()
            self._headers(code, extra=extra)
            self.wfile.write(body)

        def _err(self, code, msg):
            self._json(code, {"error": msg})

        def _user(self):
            c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            tok = c["sid"].value if "sid" in c else None
            return svc.user_for_token(tok)

        def _body(self):
            raw = getattr(self, "_raw", b"{}")
            try:
                obj = json.loads(raw or b"{}")
            except ValueError:
                raise BadInput("json")
            if not isinstance(obj, dict):
                raise BadInput("json")
            return obj

        def _route(self, method):
            path = urlparse(self.path).path
            try:
                if method == "GET" and not path.startswith("/api/"):
                    return self._static(path)
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > MAX_BODY:
                        self.rfile.read(min(n, 1024 * 1024))   # drain (bounded) so the client sees the 413
                        self.close_connection = True
                        return self._err(413, "too_large")
                    self._raw = self.rfile.read(n) if n else b"{}"
                    if self.headers.get("X-Requested-With") != "triage-ui":   # CSRF: custom header + SameSite=Strict
                        return self._err(403, "forbidden")
                if path == "/api/demo-users" and method == "GET":
                    return self._json(200, [{"id": u["id"], "label": f"{u['name']}, {u['title']}"}
                                            for u in app.store.q("SELECT * FROM users WHERE active=1 ORDER BY org, id")])
                if path == "/api/login" and method == "POST":
                    b = self._body()
                    tok, u = svc.login(str(b.get("user_id", "")))
                    return self._json(200, {"ok": True}, extra={
                        "Set-Cookie": f"sid={tok}; HttpOnly; SameSite=Strict; Path=/"
                                      + ("; Secure" if app.settings.secure_cookies else "")})
                user = self._user()
                if not user:
                    return self._err(401, "unauthenticated")
                parts = [p for p in path.split("/") if p][1:]   # drop "api"
                for p in parts:
                    if not SAFE_ID.match(p):
                        return self._err(404, "not_found")
                return self._dispatch(method, parts, user)
            except NotFound:
                return self._err(404, "not_found")
            except Forbidden:
                return self._err(403, "forbidden")
            except Conflict as e:
                return self._err(409, str(e))
            except BadInput as e:
                msg = str(e)
                return self._err(413 if msg == "too_large" else 400, msg if msg.startswith("Please") else "bad_request")
            except BrokenPipeError:
                return None
            except Exception as e:
                safelog.report_error(iso(app.clock.now()), "http", e, method=method)
                return self._err(500, "internal_error")

        def _dispatch(self, m, p, user):
            if m == "GET" and p == ["me"]:
                return self._json(200, {"id": user["id"], "name": user["name"], "title": user["title"], "role": user["role"]})
            if m == "POST" and p == ["logout"]:
                with app.store.tx() as c:
                    c.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
                return self._json(200, {"ok": True}, extra={"Set-Cookie": "sid=; Max-Age=0; Path=/"})
            if m == "GET" and p == ["state"]:
                return self._json(200, svc.state(user))
            if m == "GET" and p == ["events"]:
                return self._sse(user)
            if m == "GET" and len(p) == 2 and p[0] == "review-items":
                return self._json(200, svc.review_item_detail(user, p[1]))
            if m == "GET" and len(p) == 3 and p[0] == "review-items" and p[2] == "ai-request":
                return self._json(200, svc.ai_request_view(user, p[1]))
            if m == "POST" and len(p) == 3 and p[0] == "review-items" and p[2] == "decision":
                b = self._body()
                return self._json(200, svc.decide(user, p[1], b.get("decision"), b.get("corrections"),
                                                  str(b.get("comment", ""))[:1001], b.get("expected_version")))
            if m == "GET" and p == ["emergencies"]:
                return self._json(200, svc.emergencies(user))
            if m == "POST" and len(p) == 3 and p[0] == "encounters" and p[2] == "notes":
                b = self._body()
                return self._json(200, svc.add_chart_note(user, p[1], str(b.get("body", ""))[:4001],
                                                          str(b.get("note_type", "nursing_note")),
                                                          str(b.get("input_method", "typed"))))
            if m == "POST" and len(p) == 3 and p[0] == "encounters" and p[2] == "emergency-flag":
                b = self._body()
                return self._json(200, svc.raise_manual_flag(user, p[1], str(b.get("reason", ""))))
            if m == "POST" and len(p) == 3 and p[0] == "emergency-flags" and p[2] in ("assess", "determine"):
                b = self._body()
                if p[2] == "assess":
                    return self._json(200, svc.assess_flag(user, p[1], b.get("assessment"), str(b.get("reason", "")),
                                                           b.get("expected_version")))
                return self._json(200, svc.determine_flag(user, p[1], b.get("determination"), str(b.get("reason", "")),
                                                          b.get("expected_version")))
            if m == "GET" and len(p) == 2 and p[0] == "encounters":
                return self._json(200, svc.encounter_detail(user, p[1]))
            if m == "GET" and len(p) == 3 and p[0] == "encounters" and p[2] == "obligations":
                return self._json(200, svc.encounter_obligations(user, p[1]))
            if m == "POST" and len(p) == 3 and p[0] == "encounters" and p[2] == "priority":
                b = self._body()
                try:
                    pr = int(b.get("priority"))
                except (TypeError, ValueError):
                    raise BadInput("priority")
                return self._json(200, svc.confirm_priority(user, p[1], pr, str(b.get("reason_code", "clinical_judgement")),
                                                            b.get("expected_version"), b.get("review_item_id")))
            if m == "POST" and len(p) == 3 and p[0] == "encounters" and p[2] == "escalate":
                b = self._body()
                return self._json(200, svc.escalate(user, p[1], str(b.get("reason_code", ""))))
            if m == "POST" and len(p) == 3 and p[0] == "obligations":
                return self._json(200, svc.obligation_action(user, p[1], p[2]))
            if p and p[0] == "sim":
                return self._sim(m, p[1:], user)
            raise NotFound()

        # ---- demo-only simulator (EHR feed, clock, provider faults). Disabled unless configured.
        def _sim(self, m, p, user):
            if not app.settings.demo_controls:
                raise NotFound()
            if m == "GET" and p == ["fixtures"]:   # IDs and case labels only; never note text
                encs = {e["id"]: e for e in app.seed["encounters"]}
                return self._json(200, [{"note_id": n["note_id"], "version": n["version"], "case": n["case"],
                                         "label": encs[n["encounter"]]["label"]}
                                        for n in app.seed["deliverable_notes"] if encs[n["encounter"]]["org"] == user["org"]])
            if m != "POST":
                raise NotFound()
            b = self._body()
            org = user["org"]
            if p == ["deliver"]:
                return self._json(200, svc.deliver_fixture(org, str(b.get("note_id", "")), int(b.get("version", 1))))
            if p == ["clock"]:
                app.clock.advance(minutes=max(0, min(120, float(b.get("minutes", 0)))))
                svc.tick()
                return self._json(200, {"now": iso(app.clock.now())})
            if p == ["provider"]:
                f = str(b.get("fault", "none"))
                if f not in ("none", "timeout", "rate_limit", "outage", "malformed", "no_finding", "refusal"):
                    raise BadInput("fault")
                app.adapter.fault = f
                with app.store.tx() as c:
                    c.execute("INSERT OR REPLACE INTO sim VALUES ('provider_fault', ?)", (f,))
                return self._json(200, {"fault": f})
            if p == ["feed"]:
                with app.store.tx() as c:
                    if b.get("stalled"):
                        c.execute("INSERT OR REPLACE INTO sim VALUES ('feed_stalled', '1')")
                    else:
                        c.execute("DELETE FROM sim WHERE k='feed_stalled'")
                return self._json(200, {"ok": True})
            if p == ["close"]:
                svc.sim_close(org, str(b.get("encounter_id", "")), "closed")
                return self._json(200, {"ok": True})
            if p == ["revoke"]:
                target = app.store.one("SELECT org FROM users WHERE id=?", (str(b.get("user_id", "")),))
                if not target or target["org"] != org:
                    raise NotFound()
                svc.sim_revoke(str(b.get("user_id")))
                return self._json(200, {"ok": True})
            if p == ["reset"]:
                svc.seed_db()
                app.adapter.fault = "none"
                return self._json(200, {"ok": True})
            raise NotFound()

        # ---- server-sent events: IDs and generic announcements only
        def _sse(self, user):
            self._headers(200, "text/event-stream", {"Connection": "keep-alive"})
            ck = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            tok = ck["sid"].value if "sid" in ck else None
            q, ev = app.broker.subscribe(user["org"], user["id"])
            last =self.headers.get("Last-Event-ID")
            try:
                if last and last.isdigit():   # replay what this client missed, from the durable outbox
                    for r in app.store.q("SELECT * FROM outbox WHERE org=? AND seq>? ORDER BY seq", (user["org"], int(last))):
                        if not self._send_event(r, user):
                            return
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    ev.wait(15)
                    ev.clear()
                    if not svc.user_for_token(tok):   # authorization re-checked on every wake
                        self.wfile.write(b"event: access_revoked\ndata: {}\n\n")
                        self.wfile.flush()
                        return
                    while q:
                        if not self._send_event(q.pop(0), user):
                            return
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                app.broker.unsubscribe(q)

        def _send_event(self, r, user):
            payload = json.loads(r["payload_json"])
            if r["type"] == "access_revoked":
                if payload.get("user_id") == user["id"]:
                    self.wfile.write(b"event: access_revoked\ndata: {}\n\n")
                    self.wfile.flush()
                    return False
                return True
            msg = ANNOUNCE.get(r["type"], lambda p: None)(payload)
            if r["type"] == "ai_status":
                st = app.store.one("SELECT ai_state FROM review_items WHERE id=?", (payload.get("item_id"),))
                if st and (st["ai_state"].startswith("failed_") or st["ai_state"] in ("malformed", "stale_discarded")):
                    msg = "AI extraction could not complete for a note. Manual review required in evidence inbox."
            data = {"seq": r["seq"], "type": r["type"], "announce": msg, "urgent": r["type"] == "emergency_flag",
                    "ids": {k: v for k, v in payload.items() if k.endswith("_id") and k != "user_id"},
                    "unreviewed": payload.get("unreviewed")}
            self.wfile.write(f"id: {r['seq']}\nevent: update\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()
            return True

        def _static(self, path):
            if path in ("/", ""):
                path = "/index.html"
            name = os.path.basename(path)
            full = os.path.join(STATIC, name)
            if not re.match(r"^[a-z0-9_\-]+\.(html|js|css)$", name) or not os.path.isfile(full):
                return self._err(404, "not_found")
            ctype = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8",
                     "css": "text/css; charset=utf-8"}[name.rsplit(".", 1)[1]]
            with open(full, "rb") as f:
                data = f.read()
            self._headers(200, ctype)
            self.wfile.write(data)

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

    return H


def main():
    app = App()
    if app.settings.mode == "live" and not os.environ.get("ANTHROPIC_API_KEY"):
        print("TRIAGE_MODE=live but ANTHROPIC_API_KEY is not set. Live calls will fail and show "
              "'AI credentials rejected; manual review required'. Use TRIAGE_MODE=replay for an offline demo.")
    app.start_worker()
    srv = ThreadingHTTPServer((app.settings.host, app.settings.port), make_handler(app))
    srv.daemon_threads = True
    print(f"Synthetic triage demo on http://{app.settings.host}:{app.settings.port}  mode={app.settings.mode} "
          f"model={app.settings.model}  (synthetic data only)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
