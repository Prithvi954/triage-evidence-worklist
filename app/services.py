"""Clinical workflow services. Every state change is server-side, authorized, and audited.

Authority boundaries (the core safety property):
- Claude's output can create *evidence* attached to a review item. Nothing else.
- Official priority changes only through confirm_priority(), which requires an authorized
  clinician role, an explicit request, and the current encounter version.
- Obligation deadlines are immutable (DB trigger). Model failures, retries, duplicate events,
  and restarts never touch them.
"""
import json
import secrets
from datetime import timedelta

import time

from . import accounts, config, crypto, emergency, excerpt, validate
from .adapter import BoundaryViolation
from .clock import iso, parse
from .db import audit, emit
from .safelog import log_event, report_error

# ---- illustrative protocol values (REQUIRE CLINICAL LEAD APPROVAL before any real use) ------
NOTE_REVIEW_MINUTES = 15        # every new/amended note must be reviewed within this window
LEVEL1_BEDSIDE_MINUTES = 10     # after a confirmed Level 1, a clinician must reach the patient
ESCALATION_RESPONSE_MINUTES = 5
UNASSESSED_RANK = 1.5           # unassessed patients sort after Level 1, ahead of Level 2
CONFIRM_ROLES = {"charge_rn", "provider"}   # roles allowed to confirm a priority change (demo protocol)
CLINICAL_ROLES = {"rn", "charge_rn", "provider"}
EDUCATION_ROLES = {"student", "instructor"}
PRIORITY_LEVELS = (1, 2, 3, 4, 5)   # ESI (Emergency Severity Index) 1 = most urgent
EMERGENCY_CHECK_MINUTES = 5      # a nurse checks a possible emergency (illustrative)
EMERGENCY_DECISION_MINUTES = 10  # the charge nurse makes the final call (illustrative)
OPEN_FLAG = ("awaiting_nurse", "awaiting_charge")
EMERGENCY_STATUS_TEXT = {
    "awaiting_nurse": "Possible emergency: waiting for a nurse to check",
    "awaiting_charge": "Possible emergency: nurse checked, waiting for charge nurse decision",
    "confirmed_emergency": "Emergency confirmed by charge nurse",
    "not_emergency": "Charge nurse decided: not an emergency",
}
NOTE_TYPES = {"nursing_note": "Nursing note", "reassessment": "Reassessment", "patient_statement": "Patient statement",
              "handoff": "Handoff note"}
ESCALATION_REASONS = {"concern_deterioration", "patient_or_family_concern", "unable_to_reach",
                      "staffing_capacity", "other_clinical_concern"}

QUEUE_RULE = ("Official care order: confirmed priority level (1 is most urgent; unassessed patients "
              "sort after Level 1 and before Level 2), then the earliest open deadline, then arrival "
              "time, then encounter ID. AI output is not an input to this order.")
INBOX_RULE = ("Evidence inbox order: the time each note was received, oldest first, then item ID. "
              "Every new or amended note appears here whether or not AI found anything. AI status is a "
              "label only and does not change this order.")


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class BadInput(Exception):
    pass


class BreakGlassRequired(Exception):
    """Opening a restricted record needs a stated reason (logged and reviewable)."""


def _id(prefix):
    return f"{prefix}_{secrets.token_hex(5)}"


class Service:
    def __init__(self, settings, clock, store, adapter, seed):
        self.s = settings
        self.clock = clock
        self.db = store
        self.adapter = adapter
        self.seed = seed
        self.crash_hook = None   # tests: callable(point) may raise to simulate a worker crash

    # ------------------------------------------------------------------ seeding / sessions
    def seed_db(self):
        self.db.reset()
        now = iso(self.clock.now())
        with self.db.tx() as c:
            for o in self.seed["orgs"]:
                c.execute("INSERT INTO orgs VALUES (?,?)", (o["id"], o["name"]))
                c.execute("INSERT INTO feed (org, last_message_at) VALUES (?,?)", (o["id"], now))
            demo_hash = _demo_password_hash()
            for u in self.seed["users"]:
                c.execute("INSERT INTO users (id, org, name, role, title, status, covered_by, username, password_hash) "
                          "VALUES (?,?,?,?,?,?,?,?,?)",
                          (u["id"], u["org"], u["name"], u["role"], u["title"], u["status"], u.get("covered_by"),
                           u.get("username"), demo_hash))
            for e in self.seed["encounters"]:
                c.execute("INSERT INTO encounters (id, org, label, arrived_at, priority, priority_confirmed_by, "
                          "priority_confirmed_at, primary_user) VALUES (?,?,?,?,?,?,?,?)",
                          (e["id"], e["org"], e["label"], e["arrived_at"], e["priority"],
                           e.get("priority_confirmed_by"), e.get("priority_confirmed_at"), e.get("primary_user")))
                c.execute("INSERT INTO encounter_headers VALUES (?,?)", (e["id"], json.dumps(e["header"])))
            for ob in self.seed["obligations"]:
                org = next(e["org"] for e in self.seed["encounters"] if e["id"] == ob["encounter"])
                c.execute("INSERT INTO obligations (id, org, encounter_id, kind, due_at, created_at, owner, source) "
                          "VALUES (?,?,?,?,?,?,?,?)", (ob["id"], org, ob["encounter"], ob["kind"], ob["due_at"],
                                                       now, ob["owner"], "seed"))
            for n in self.seed["seeded_notes"]:
                org = next(e["org"] for e in self.seed["encounters"] if e["id"] == n["encounter"])
                c.execute("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?)",
                          (n["note_id"], n["version"], n["encounter"], org, n["author_employee_id"], n["signed_at"],
                           n["signed_at"], crypto.enc(n["body"]), None))
                # Seeded notes were reviewed before the demo starts.
                c.execute("INSERT INTO review_items (id, org, encounter_id, note_id, note_version, received_at, state, "
                          "ai_state, reviewed_by, reviewed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (_id("ri"), org, n["encounter"], n["note_id"], n["version"], n["signed_at"], "reviewed",
                           "not_run_seeded", "u_sam", n["signed_at"]))
            c.execute("INSERT INTO sim VALUES ('provider_fault', 'none')")
        if getattr(self, "education", None):
            self.education.seed_builtin()

    def login(self, user_id):
        """One-click sign-in for the demo picker (server only allows it when demo_login is on)."""
        u = self.db.one("SELECT * FROM users WHERE id=? AND active=1", (user_id,))
        if not u:
            raise Forbidden()
        return self._new_session(u), u

    def _new_session(self, u):
        token = secrets.token_urlsafe(24)
        with self.db.tx() as c:
            c.execute("INSERT INTO sessions (token, user_id, created_at, last_seen) VALUES (?,?,?,?)",
                      (token, u["id"], iso(self.clock.now()), time.time()))
            audit(c, iso(self.clock.now()), u["org"], u["id"], "login", "user", u["id"])
        return token

    throttle = accounts.Throttle()

    def login_password(self, username, password):
        username = (username or "").strip().lower()
        if self.throttle.blocked(username):
            raise Forbidden("Too many failed attempts. Wait 5 minutes and try again.")
        u = self.db.one("SELECT * FROM users WHERE username=? AND active=1", (username,))
        if not u or not u["password_hash"] or not accounts.verify_password(password or "", u["password_hash"]):
            self.throttle.fail(username)
            log_event(iso(self.clock.now()), "auth.login_failed")
            if not u and self.pending_signup(username):
                raise Forbidden("Please activate your account first with the code we emailed you.")
            raise Forbidden("Wrong email or password.")
        self.throttle.clear(username)
        return self._new_session(u), u

    # ---------------------------------------------------------------- public sign-up (learners)
    signup_throttle = accounts.Throttle(limit=10, window_s=3600)   # per client address

    def signup(self, email, name, program, password, client="?"):
        from . import mailer
        if not self.s.signup_open:
            raise Forbidden("Sign-up is closed on this server. Ask your instructor for an account.")
        if self.signup_throttle.blocked(client):
            raise Forbidden("Too many sign-ups from this network. Try again in an hour.")
        email = (email or "").strip().lower()[:120]
        name = " ".join((name or "").split())[:60]
        if not accounts.EMAIL_RE.match(email):
            raise BadInput("Please enter a valid email address.")
        if len(name) < 2:
            raise BadInput("Please enter your name.")
        if program not in accounts.PROGRAMS:
            raise BadInput("Please choose your program.")
        problems = accounts.password_problems(password or "")
        if problems:
            raise BadInput("Please use a password with " + " and ".join(problems) + ".")
        if self.db.one("SELECT 1 FROM users WHERE username=?", (email,)):
            raise Conflict("An account with this email already exists. Please sign in.")
        now = time.time()
        with self.db.tx() as c:
            c.execute("DELETE FROM signups WHERE created_at < ?", (now - 86400,))
        prev = self.db.one("SELECT * FROM signups WHERE email=?", (email,))
        if prev and now - prev["last_sent_at"] < accounts.RESEND_SECONDS:
            raise Conflict("We just sent a code to this email. Wait a minute before asking for another.")
        self.signup_throttle.fail(client)
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self.db.tx() as c:
            c.execute("INSERT OR REPLACE INTO signups VALUES (?,?,?,?,?,?,?,?,?)",
                      (email, name, program, accounts.hash_password(password), accounts.code_hash(email, code),
                       now + accounts.CODE_MINUTES * 60, 0, now, prev["created_at"] if prev else now))
        mailer.send_code(self.s, email, name, code, accounts.CODE_MINUTES)
        log_event(iso(self.clock.now()), "auth.signup_code_sent")
        return {"ok": True, "email": email, "minutes": accounts.CODE_MINUTES}

    def resend_code(self, email, client="?"):
        from . import mailer
        email = (email or "").strip().lower()
        row = self.db.one("SELECT * FROM signups WHERE email=?", (email,))
        if not row:
            raise NotFound()
        now = time.time()
        if now - row["last_sent_at"] < accounts.RESEND_SECONDS:
            raise Conflict(f"Please wait {int(accounts.RESEND_SECONDS - (now - row['last_sent_at'])) + 1} seconds before asking for a new code.")
        if self.signup_throttle.blocked(client):
            raise Forbidden("Too many requests from this network. Try again in an hour.")
        self.signup_throttle.fail(client)
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self.db.tx() as c:
            c.execute("UPDATE signups SET code_hash=?, expires_at=?, attempts=0, last_sent_at=? WHERE email=?",
                      (accounts.code_hash(email, code), now + accounts.CODE_MINUTES * 60, now, email))
        mailer.send_code(self.s, email, row["name"], code, accounts.CODE_MINUTES)
        return {"ok": True}

    def verify_signup(self, email, code):
        import hmac
        email = (email or "").strip().lower()
        code = "".join(ch for ch in str(code or "") if ch.isdigit())
        row = self.db.one("SELECT * FROM signups WHERE email=?", (email,))
        if not row:
            raise BadInput("We couldn't find a sign-up for this email. Please sign up again.")
        if row["attempts"] >= accounts.CODE_ATTEMPTS:
            raise Forbidden("Too many wrong codes. Ask for a new code.")
        if time.time() > row["expires_at"]:
            raise BadInput("This code has expired. Ask for a new code.")
        if not hmac.compare_digest(accounts.code_hash(email, code), row["code_hash"]):
            with self.db.tx() as c:
                c.execute("UPDATE signups SET attempts=attempts+1 WHERE email=?", (email,))
            left = accounts.CODE_ATTEMPTS - row["attempts"] - 1
            raise BadInput(f"That code isn't right. {left} {'try' if left == 1 else 'tries'} left." if left else
                           "That code isn't right. Ask for a new code.")
        if self.db.one("SELECT 1 FROM users WHERE username=?", (email,)):
            raise Conflict("An account with this email already exists. Please sign in.")
        uid = _id("u")
        org = self.s.signup_org
        with self.db.tx() as c:
            c.execute("INSERT INTO users (id, org, name, role, title, status, username, password_hash) VALUES (?,?,?,?,?,?,?,?)",
                      (uid, org, row["name"], "student", accounts.PROGRAMS[row["program"]], "on_shift", email, row["password_hash"]))
            c.execute("DELETE FROM signups WHERE email=?", (email,))
            audit(c, iso(self.clock.now()), org, uid, "account_activated", "user", uid)
        u = self.db.one("SELECT * FROM users WHERE id=?", (uid,))
        return self._new_session(u), u

    def pending_signup(self, email):
        return bool(self.db.one("SELECT 1 FROM signups WHERE email=?", ((email or "").strip().lower(),)))

    def user_for_token(self, token):
        if not token:
            return None
        row = self.db.one("SELECT u.*, s.last_seen AS _last_seen FROM sessions s JOIN users u ON u.id=s.user_id "
                          "WHERE s.token=? AND u.active=1", (token,))
        if not row:
            return None
        now = time.time()
        last = row.pop("_last_seen") or now
        if now - last > self.s.session_idle_minutes * 60:
            with self.db.tx() as c:
                c.execute("DELETE FROM sessions WHERE token=?", (token,))
            return None
        if now - last > 30:
            with self.db.tx() as c:
                c.execute("UPDATE sessions SET last_seen=? WHERE token=?", (now, token))
        return row

    def verify_current_password(self, user, password):
        u = self.db.one("SELECT password_hash FROM users WHERE id=?", (user["id"],))
        if not u or not u["password_hash"] or not accounts.verify_password(password or "", u["password_hash"]):
            raise Forbidden("Wrong password.")
        return {"ok": True}

    def change_password(self, user, old, new):
        self.verify_current_password(user, old)
        problems = accounts.password_problems(new or "")
        if problems:
            raise BadInput("Please use a password with " + " and ".join(problems) + ".")
        with self.db.tx() as c:
            c.execute("UPDATE users SET password_hash=? WHERE id=?", (accounts.hash_password(new), user["id"]))
            audit(c, iso(self.clock.now()), user["org"], user["id"], "password_changed", "user", user["id"])
        return {"ok": True}

    # who may create which accounts: hospital admins create clinical staff; instructors create learners
    CREATE_RULES = {"admin": {"rn", "charge_rn", "provider", "admin"}, "instructor": {"student", "instructor"}}

    def create_user(self, actor, username, name, role, title, password):
        allowed = self.CREATE_RULES.get(actor["role"], set())
        if role not in allowed:
            raise Forbidden("You can't create that kind of account.")
        username = (username or "").strip().lower()
        if not accounts.USERNAME_RE.match(username):
            raise BadInput("Please use a username of 3-40 lowercase letters, numbers, dots, dashes or underscores.")
        name = (name or "").strip()[:60]
        if not name:
            raise BadInput("Please enter a display name.")
        problems = accounts.password_problems(password or "")
        if problems:
            raise BadInput("Please use a password with " + " and ".join(problems) + ".")
        if self.db.one("SELECT 1 FROM users WHERE username=?", (username,)):
            raise Conflict("That username is taken.")
        uid = _id("u")
        with self.db.tx() as c:
            c.execute("INSERT INTO users (id, org, name, role, title, status, username, password_hash) VALUES (?,?,?,?,?,?,?,?)",
                      (uid, actor["org"], name, role, (title or accounts.ROLES[role])[:80], "on_shift", username,
                       accounts.hash_password(password)))
            audit(c, iso(self.clock.now()), actor["org"], actor["id"], "user_created", "user", uid, role=role)
        return {"ok": True, "user_id": uid}

    def list_users(self, actor):
        if actor["role"] not in self.CREATE_RULES:
            raise Forbidden()
        return [{"id": u["id"], "username": u["username"], "name": u["name"], "role": u["role"],
                 "role_text": accounts.ROLES.get(u["role"], u["role"]), "title": u["title"], "active": bool(u["active"])}
                for u in self.db.q("SELECT * FROM users WHERE org=? AND id NOT LIKE 'trn_%' ORDER BY role, name", (actor["org"],))]

    def set_active(self, actor, user_id, active):
        target = self.db.one("SELECT * FROM users WHERE id=?", (user_id,))
        if not target or target["org"] != actor["org"] or target["role"] not in self.CREATE_RULES.get(actor["role"], set()):
            raise NotFound()
        if target["id"] == actor["id"]:
            raise BadInput("You can't deactivate your own account.")
        with self.db.tx() as c:
            c.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, user_id))
            if not active:
                c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            audit(c, iso(self.clock.now()), actor["org"], actor["id"], "user_" + ("activated" if active else "deactivated"),
                  "user", user_id)
            if not active:
                emit(c, iso(self.clock.now()), target["org"], "access_revoked", user_id=user_id)
        return {"ok": True}

    def _enc_for(self, user, enc_id):
        """Object-level authorization. Unknown and other-org encounters look identical (404)."""
        e = self.db.one("SELECT * FROM encounters WHERE id=?", (enc_id,))
        if not e or e["org"] != user["org"] or user["role"] not in CLINICAL_ROLES:
            raise NotFound()
        return e

    def _item_for(self, user, item_id):
        it = self.db.one("SELECT * FROM review_items WHERE id=?", (item_id,))
        if not it or it["org"] != user["org"] or user["role"] not in CLINICAL_ROLES:
            raise NotFound()
        return it

    # ------------------------------------------------------------------ intake
    def deliver_fixture(self, org, note_id, version):
        """EHR simulator: deliver an approved synthetic fixture note. The only intake path."""
        n = next((x for x in self.seed["deliverable_notes"] if x["note_id"] == note_id and x["version"] == version), None)
        if not n:
            raise NotFound()
        enc = self.db.one("SELECT * FROM encounters WHERE id=?", (n["encounter"],))
        if enc["org"] != org:
            raise NotFound()
        if self.db.one("SELECT v FROM sim WHERE k='feed_stalled'"):
            return {"delivered": False, "reason": "simulated feed outage: message not delivered"}
        # The simulator stamps signing time one minute before delivery so demo times stay coherent.
        # The fixture allowlist covers the body text, which is what can leave the boundary.
        n = dict(n, signed_at=iso(self.clock.now() - timedelta(minutes=1)))
        return self.ingest_note(enc, n, fixture=True)

    def ingest_note(self, enc, n, fixture=False):
        now = iso(self.clock.now())
        exists = self.db.one("SELECT 1 FROM notes WHERE note_id=? AND version=?", (n["note_id"], n["version"]))
        if exists:   # duplicate EHR event: idempotent, no new item, no deadline change
            log_event(now, "intake.duplicate_ignored", note_id=n["note_id"], version=n["version"])
            return {"delivered": True, "duplicate": True}
        item_id = _id("ri")
        with self.db.tx() as c:
            c.execute("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?)",
                      (n["note_id"], n["version"], enc["id"], enc["org"], n.get("author_employee_id"), n["signed_at"],
                       now, crypto.enc(n["body"]), excerpt_sha(n["body"]) if fixture else None))
            # An amendment supersedes any still-unreviewed item for an older version of the same note.
            sup = c.execute("SELECT id FROM review_items WHERE note_id=? AND note_version<? AND state='unreviewed'",
                            (n["note_id"], n["version"])).fetchall()
            for r in sup:
                c.execute("UPDATE review_items SET state='superseded', version=version+1 WHERE id=?", (r["id"],))
                audit(c, now, enc["org"], "system", "review_item_superseded", "review_item", r["id"],
                      by_note_version=n["version"])
            c.execute("INSERT INTO review_items (id, org, encounter_id, note_id, note_version, received_at, state, "
                      "ai_state) VALUES (?,?,?,?,?,?,?,?)",
                      (item_id, enc["org"], enc["id"], n["note_id"], n["version"], now, "unreviewed", "queued"))
            owner = self._effective_owner(c, enc["primary_user"]) or self._queue_owner(c, enc["org"])
            due = iso(self.clock.now() + timedelta(minutes=NOTE_REVIEW_MINUTES))
            # Only one open note-review obligation per note: an amendment keeps the ORIGINAL deadline.
            open_ob = c.execute("SELECT id FROM obligations WHERE kind='note_review' AND status='open' AND "
                                "source_ref=?", (n["note_id"],)).fetchone()
            if not open_ob:
                c.execute("INSERT INTO obligations (id, org, encounter_id, kind, due_at, created_at, owner, source, "
                          "source_ref) VALUES (?,?,?,?,?,?,?,?,?)",
                          (_id("ob"), enc["org"], enc["id"], "note_review", due, now, owner, "note_received", n["note_id"]))
            c.execute("INSERT INTO jobs (id, review_item_id, note_id, note_version, status, next_attempt_at, "
                      "idempotency_key) VALUES (?,?,?,?,?,?,?)",
                      (_id("job"), item_id, n["note_id"], n["version"], "pending", now, f"{n['note_id']}:{n['version']}"))
            c.execute("UPDATE feed SET last_message_at=? WHERE org=?", (now, enc["org"]))
            self._run_emergency_rules(c, enc, n["body"], f"{n['note_id']} v{n['version']}", "rules_engine")
            audit(c, now, enc["org"], "ehr_simulator", "note_received", "review_item", item_id,
                  note_id=n["note_id"], note_version=n["version"])
            emit(c, now, enc["org"], "note_received", item_id=item_id, unreviewed=self._unreviewed(c, enc["org"]))
        log_event(now, "intake.note_received", item=item_id, note_id=n["note_id"], version=n["version"])
        return {"delivered": True, "review_item_id": item_id}

    # ------------------------------------------------------------------ worker
    def run_jobs_once(self):
        now = iso(self.clock.now())
        jobs = self.db.q("SELECT * FROM jobs WHERE status IN ('pending','running') AND next_attempt_at<=? "
                         "ORDER BY next_attempt_at, id", (now,))
        for j in jobs:
            self.process_job(j)
        return len(jobs)

    def _hook(self, point):
        if self.crash_hook:
            self.crash_hook(point)

    def process_job(self, job):
        now = iso(self.clock.now())
        item = self.db.one("SELECT * FROM review_items WHERE id=?", (job["review_item_id"],))
        with self.db.tx() as c:
            c.execute("UPDATE jobs SET status='running', attempts=attempts+1 WHERE id=?", (job["id"],))
            c.execute("UPDATE review_items SET ai_state='in_progress' WHERE id=? AND ai_state IN ('queued','retrying')",
                      (item["id"],))
        if item["state"] == "superseded":   # amended before we started: don't send the old text at all
            self._finish(job, item, "stale_discarded", None)
            return
        note = self.db.one("SELECT note_id, version, encounter_id, org, author_employee_id, signed_at, received_at, "
                           "dec(body) AS body, fixture_sha256 FROM notes WHERE note_id=? AND version=?",
                           (job["note_id"], job["note_version"]))
        earlier =self.db.q("SELECT note_id, version, dec(body) AS body, signed_at FROM notes WHERE encounter_id=? AND "
                            "NOT (note_id=? AND version>=?)", (note["encounter_id"], note["note_id"], note["version"]))
        hdr = self.db.one("SELECT header_json FROM encounter_headers WHERE encounter_id=?", (note["encounter_id"],))
        from .fhir import identifiers_for
        ids = identifiers_for(json.loads(hdr["header_json"])) if hdr else []
        raw_bodies = {(n["note_id"], n["version"]): n["body"] for n in earlier + [note]}
        note = dict(note, body=excerpt.scrub_ids(note["body"], ids))
        earlier = [dict(e, body=excerpt.scrub_ids(e["body"], ids)) for e in earlier]
        ex = excerpt.select(note, earlier)
        if ex.abstain_reason:
            self._finish(job, item, "not_called_" + ex.abstain_reason, None)
            return
        bodies = {(n["note_id"], n["version"]): n["body"] for n in earlier + [note]}   # identifier-scrubbed
        settings = self.s
        req = None
        call_id = _id("mc")
        try:
            self.adapter.check_fixture_boundary(ex.alias_map, ex.sources, raw_bodies, ids)
            from .adapter import build_request
            req = build_request(ex.sources, ex.matched_terms, settings)
            subst = {}
            for s in ex.sources:
                subst["{{CURRENT}}" if s["role"] == "current_note_excerpt" else "{{PRIOR}}"] = s["alias"]
            self._hook("before_call")
            rep = self.db.one("SELECT replay_note_id, replay_version FROM note_replay WHERE note_id=? AND version=?",
                              (note["note_id"], note["version"]))
            replay_key = (rep["replay_note_id"], rep["replay_version"]) if rep else (note["note_id"], note["version"])
            res = self.adapter.extract(req, replay_key, subst)
        except BoundaryViolation as e:
            report_error(now, "adapter.boundary", e, item=item["id"])
            self._record_call(call_id, item, note, None, "blocked", None, "", "", "", None)
            self._finish(job, item, "blocked_not_approved_fixture", call_id)
            return
        except Exception as e:  # never log str(e): provider errors may echo request content
            report_error(now, "adapter.extract", e, item=item["id"])
            self._record_call(call_id, item, note, req, "exception", None, "", "", "", None)
            self._retry_or_fail(job, item, "exception", call_id)
            return
        self._hook("after_call")
        done = iso(self.clock.now())

        if res.outcome != "ok":
            self._record_call(call_id, item, note, req, res.outcome, None, res.provenance, res.model,
                              res.provider_request_id, None, finished=done)
            if res.retryable:
                self._retry_or_fail(job, item, res.outcome, call_id)
            else:
                self._finish(job, item, "failed_" + res.outcome, call_id)
            return

        result = validate.validate(res.text, ex.sources, ex.alias_map, bodies, ex.repeated_sentences)
        with self.db.tx() as c:
            # Stale guard: bind to the TRUSTED job context, then re-check it at commit time.
            latest = c.execute("SELECT MAX(version) v FROM notes WHERE note_id=?", (note["note_id"],)).fetchone()["v"]
            enc = c.execute("SELECT status FROM encounters WHERE id=?", (note["encounter_id"],)).fetchone()
            cur_item = c.execute("SELECT state FROM review_items WHERE id=?", (item["id"],)).fetchone()
            stale = latest != note["version"] or enc["status"] != "active" or cur_item["state"] == "superseded"
            outcome = "stale_discarded" if stale else result["state"]
            c.execute("INSERT INTO model_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (call_id, item["id"], item["org"], item["encounter_id"], note["note_id"], note["version"],
                       settings.mode, res.provenance, res.model or settings.model, config.PROMPT_VERSION,
                       config.SCHEMA_VERSION, config.SELECTOR_VERSION, config.RULES_VERSION, now, done, outcome,
                       res.provider_request_id, crypto.enc(json.dumps(req)), crypto.enc(res.text),
                       json.dumps({"errors": result["errors"], "omitted_fields": ex.omitted_fields,
                                   "comparison_available": ex.comparison_available,
                                   "repeated": ex.repeated_sentences, "reviewer_note": result["reviewer_note"],
                                   "reviewer_note_flags": result["reviewer_note_flags"]})))
            if not stale:
                for f in result["findings"]:
                    c.execute("INSERT INTO findings VALUES (?,?,?,?,?,?)",
                              (_id("fd"), item["id"], call_id, f["idx"], json.dumps(f["finding"]), json.dumps(f["checks"])))
                c.execute("UPDATE review_items SET ai_state=?, version=version+1 WHERE id=?", (outcome, item["id"]))
            c.execute("UPDATE jobs SET status='done' WHERE id=?", (job["id"],))
            audit(c, done, item["org"], "system", "model_call_completed", "review_item", item["id"],
                  outcome=outcome, model_call=call_id, prompt_version=config.PROMPT_VERSION)
            emit(c, done, item["org"], "evidence_ready" if outcome == "findings" else "ai_status",
                 item_id=item["id"], unreviewed=self._unreviewed(c, item["org"]))
        log_event(done, "model.call_completed", item=item["id"], outcome=outcome, mode=settings.mode)

    def _record_call(self, call_id, item, note, req, outcome, resp, prov, model, rid, val, finished=None):
        now = iso(self.clock.now())
        with self.db.tx() as c:
            c.execute("INSERT INTO model_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (call_id, item["id"], item["org"], item["encounter_id"], note["note_id"], note["version"],
                       self.s.mode, prov, model or self.s.model, config.PROMPT_VERSION, config.SCHEMA_VERSION,
                       config.SELECTOR_VERSION, config.RULES_VERSION, now, finished or now, outcome, rid,
                       crypto.enc(json.dumps(req)) if req else None, crypto.enc(resp), val))

    def _retry_or_fail(self, job, item, outcome, call_id):
        j = self.db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
        now = self.clock.now()
        with self.db.tx() as c:
            if j["attempts"] < self.s.max_job_attempts:
                nxt = iso(now + timedelta(seconds=30 * (2 ** (j["attempts"] - 1))))
                c.execute("UPDATE jobs SET status='pending', next_attempt_at=?, last_error_class=? WHERE id=?",
                          (nxt, outcome, job["id"]))
                c.execute("UPDATE review_items SET ai_state='retrying' WHERE id=? AND state!='superseded'", (item["id"],))
            else:
                c.execute("UPDATE jobs SET status='failed', last_error_class=? WHERE id=?", (outcome, job["id"]))
                c.execute("UPDATE review_items SET ai_state=? WHERE id=? AND state!='superseded'",
                          ("failed_" + outcome, item["id"]))
            audit(c, iso(now), item["org"], "system", "model_call_failed", "review_item", item["id"], outcome=outcome,
                  attempt=j["attempts"])
            emit(c, iso(now), item["org"], "ai_status", item_id=item["id"], unreviewed=self._unreviewed(c, item["org"]))
        log_event(iso(now), "model.call_failed", item=item["id"], outcome=outcome, attempt=j["attempts"])

    def _finish(self, job, item, ai_state, call_id):
        now = iso(self.clock.now())
        with self.db.tx() as c:
            c.execute("UPDATE jobs SET status='done' WHERE id=?", (job["id"],))
            c.execute("UPDATE review_items SET ai_state=? WHERE id=? AND state!='superseded'", (ai_state, item["id"]))
            audit(c, now, item["org"], "system", "ai_state", "review_item", item["id"], state=ai_state)
            emit(c, now, item["org"], "ai_status", item_id=item["id"], unreviewed=self._unreviewed(c, item["org"]))

    # ------------------------------------------------------------------ clinician actions
    def decide(self, user, item_id, decision, corrections=None, comment="", expected_version=None, disclosed=False):
        it = self._item_for(user, item_id)
        if decision not in ("accept", "correct", "reject", "reviewed_no_ai"):
            raise BadInput("decision")
        if it["state"] == "superseded":
            raise Conflict("This note version was amended. Review the newer version.")
        if it["state"] == "reviewed":
            raise Conflict("Already reviewed.")
        if expected_version is not None and int(expected_version) != it["version"]:
            raise Conflict("The evidence changed while you were reviewing. Reload and check again.")
        corrections = corrections or {}
        _check_corrections(corrections)
        if comment and len(comment) > 1000:
            raise BadInput("comment too long")
        now = iso(self.clock.now())
        with self.db.tx() as c:
            r = c.execute("UPDATE review_items SET state='reviewed', reviewed_by=?, reviewed_at=?, version=version+1 "
                          "WHERE id=? AND state='unreviewed' AND version=?", (user["id"], now, item_id, it["version"]))
            if r.rowcount != 1:
                raise Conflict("Already reviewed by someone else.")
            if disclosed:
                c.execute("INSERT OR IGNORE INTO ai_disclosures VALUES (?,?,?)", (item_id, user["id"], now))
            c.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)",
                      (_id("dc"), item_id, it["encounter_id"], user["id"], now, decision, json.dumps(corrections), comment))
            # The note-review obligation completes when every unreviewed version of the note is handled.
            remaining = c.execute("SELECT COUNT(*) n FROM review_items WHERE note_id=? AND state='unreviewed'",
                                  (it["note_id"],)).fetchone()["n"]
            if remaining == 0:
                c.execute("UPDATE obligations SET status='completed', completed_at=?, completed_by=? WHERE "
                          "kind='note_review' AND source_ref=? AND status='open'", (now, user["id"], it["note_id"]))
            audit(c, now, it["org"], user["id"], "evidence_" + decision, "review_item", item_id,
                  corrected_fields=",".join(sorted(corrections)) or "none")
            emit(c, now, it["org"], "item_reviewed", item_id=item_id, unreviewed=self._unreviewed(c, it["org"]))
        return {"ok": True}

    def confirm_priority(self, user, enc_id, new_priority, reason_code, expected_version, review_item_id=None):
        e = self._enc_for(user, enc_id)
        if user["role"] not in CONFIRM_ROLES:
            raise Forbidden()
        if new_priority not in PRIORITY_LEVELS:
            raise BadInput("priority")
        if e["status"] != "active":
            raise Conflict("Encounter is no longer active.")
        if expected_version is None or int(expected_version) != e["version"]:
            raise Conflict("Priority changed since you loaded this patient. Reload before deciding.")
        if review_item_id:
            self._item_for(user, review_item_id)
        now = iso(self.clock.now())
        with self.db.tx() as c:
            self._write_priority(c, e, new_priority, user, reason_code, review_item_id)
        log_event(now, "priority.confirmed", encounter=enc_id, to=new_priority)
        return {"ok": True}

    def _write_priority(self, c, e, new_priority, user, reason_code, ref=None):
        """Version-checked priority change, inside the caller's transaction."""
        now_dt = self.clock.now()
        now = iso(now_dt)
        r = c.execute("UPDATE encounters SET priority=?, priority_confirmed_by=?, priority_confirmed_at=?, "
                      "version=version+1 WHERE id=? AND version=?", (new_priority, user["id"], now, e["id"], e["version"]))
        if r.rowcount != 1:
            raise Conflict("Priority changed since you loaded this patient.")
        c.execute("INSERT INTO priority_changes VALUES (?,?,?,?,?,?,?,?)",
                  (_id("pc"), e["id"], e["priority"], new_priority, user["id"], now, ref, reason_code[:60]))
        if new_priority == 1:
            owner = self._effective_owner(c, e["primary_user"]) or self._queue_owner(c, e["org"])
            c.execute("INSERT INTO obligations (id, org, encounter_id, kind, due_at, created_at, owner, source, "
                      "source_ref) VALUES (?,?,?,?,?,?,?,?,?)",
                      (_id("ob"), e["org"], e["id"], "bedside_review", iso(now_dt + timedelta(minutes=LEVEL1_BEDSIDE_MINUTES)),
                       now, owner, "priority_confirmed", ref or "none"))
        audit(c, now, e["org"], user["id"], "priority_confirmed", "encounter", e["id"],
              from_priority=e["priority"], to_priority=new_priority, review_item=ref or "none",
              reason_code=reason_code[:60])
        emit(c, now, e["org"], "queue_changed", reason="clinician_priority_decision", encounter_id=e["id"])

    def escalate(self, user, enc_id, reason_code):
        e = self._enc_for(user, enc_id)
        if reason_code not in ESCALATION_REASONS:
            raise BadInput("reason_code")
        now_dt = self.clock.now()
        now = iso(now_dt)
        esc_id = _id("es")
        with self.db.tx() as c:
            c.execute("INSERT INTO escalations (id, org, encounter_id, raised_by, raised_at, reason_code) VALUES (?,?,?,?,?,?)",
                      (esc_id, e["org"], enc_id, user["id"], now, reason_code))
            c.execute("INSERT INTO obligations (id, org, encounter_id, kind, due_at, created_at, owner, source, source_ref) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (_id("ob"), e["org"], enc_id, "escalation_response",
                       iso(now_dt + timedelta(minutes=ESCALATION_RESPONSE_MINUTES)), now, self._queue_owner(c, e["org"]),
                       "manual_escalation", esc_id))
            audit(c, now, e["org"], user["id"], "manual_escalation", "encounter", enc_id, reason_code=reason_code)
            emit(c, now, e["org"], "manual_escalation", encounter_id=enc_id)
        return {"ok": True, "escalation_id": esc_id}

    def obligation_action(self, user, ob_id, action):
        ob = self.db.one("SELECT * FROM obligations WHERE id=?", (ob_id,))
        if not ob or ob["org"] != user["org"] or user["role"] not in CLINICAL_ROLES:
            raise NotFound()
        now = iso(self.clock.now())
        with self.db.tx() as c:
            if action == "accept":
                r = c.execute("UPDATE obligations SET accepted_by=?, accepted_at=? WHERE id=? AND accepted_by IS NULL "
                              "AND status='open'", (user["id"], now, ob_id))
                if r.rowcount != 1:
                    raise Conflict("Already accepted by another clinician.")
            elif action == "reached":
                r = c.execute("UPDATE obligations SET reached_at=? WHERE id=? AND accepted_by=? AND reached_at IS NULL",
                              (now, ob_id, user["id"]))
                if r.rowcount != 1:
                    raise Conflict("Accept the task first (or it was already marked).")
            elif action == "complete":
                r = c.execute("UPDATE obligations SET status='completed', completed_at=?, completed_by=? WHERE id=? "
                              "AND status='open'", (now, user["id"], ob_id))
                if r.rowcount != 1:
                    raise Conflict("Already completed.")
            else:
                raise BadInput("action")
            audit(c, now, ob["org"], user["id"], "obligation_" + action, "obligation", ob_id)
            emit(c, now, ob["org"], "obligation_changed", obligation_id=ob_id)
        return {"ok": True}

    # ------------------------------------------------------------------ periodic checks
    def tick(self):
        self.purge_old_ai_text()
        self._tick_obligations()

    def _tick_obligations(self):
        """Overdue escalation and feed-staleness. Idempotent; never changes a deadline."""
        now_dt = self.clock.now()
        now = iso(now_dt)
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM obligations WHERE status='open' AND due_at<? AND escalated_at IS NULL",
                             (now,)).fetchall()
            for ob in rows:
                backup = self._queue_owner(c, ob["org"])
                c.execute("UPDATE obligations SET escalated_to=?, escalated_at=? WHERE id=?", (backup, now, ob["id"]))
                audit(c, now, ob["org"], "system", "obligation_overdue", "obligation", ob["id"], escalated_to=backup or "none")
                emit(c, now, ob["org"], "obligation_overdue", obligation_id=ob["id"])
            for f in c.execute("SELECT * FROM feed").fetchall():
                stale = f["last_message_at"] and (now_dt - parse(f["last_message_at"])) > timedelta(minutes=self.s.feed_stall_minutes)
                if bool(stale) != bool(f["stalled_sim"]):
                    c.execute("UPDATE feed SET stalled_sim=? WHERE org=?", (1 if stale else 0, f["org"]))
                    emit(c, now, f["org"], "feed_status", stale=bool(stale))

    # ------------------------------------------------------------------ helpers
    def _effective_owner(self, c, user_id):
        if not user_id:
            return None
        u = c.execute("SELECT status, covered_by FROM users WHERE id=?", (user_id,)).fetchone()
        if u and u["status"] != "on_shift" and u["covered_by"]:
            return u["covered_by"]
        return user_id

    def _queue_owner(self, c, org):
        r = c.execute("SELECT id FROM users WHERE org=? AND role='charge_rn' AND status='on_shift' AND active=1 "
                      "ORDER BY id LIMIT 1", (org,)).fetchone()
        return r["id"] if r else None

    def _unreviewed(self, c, org):
        return c.execute("SELECT COUNT(*) n FROM review_items WHERE org=? AND state='unreviewed'", (org,)).fetchone()["n"]

    # ------------------------------------------------------------------ views
    def state(self, user):
        if user["role"] not in CLINICAL_ROLES:
            raise Forbidden()
        org = user["org"]
        now_dt = self.clock.now()
        now = iso(now_dt)
        users = {u["id"]: u for u in self.db.q("SELECT id, name, title, status, covered_by FROM users WHERE org=?", (org,))}
        encs = self.db.q("SELECT * FROM encounters WHERE org=? AND status='active'", (org,))
        obs = self.db.q("SELECT * FROM obligations WHERE org=? AND status='open'", (org,))
        items = self.db.q("SELECT * FROM review_items WHERE org=? AND state='unreviewed' ORDER BY received_at, id", (org,))
        label = {e["id"]: e["label"] for e in self.db.q("SELECT id, label FROM encounters WHERE org=?", (org,))}
        flags = self.db.q("SELECT id, encounter_id, status FROM emergency_flags WHERE org=? ORDER BY CASE status WHEN "
                          "'awaiting_nurse' THEN 0 WHEN 'awaiting_charge' THEN 1 WHEN 'confirmed_emergency' THEN 2 ELSE 3 END, "
                          "raised_at DESC", (org,))

        def uname(uid):
            if not uid or uid not in users:
                return None
            u = users[uid]
            s = f"{u['name']}, {u['title']}"
            if u["status"] != "on_shift":
                cov = users.get(u["covered_by"])
                s += f", {u['status'].replace('_', ' ')}" + (f", covered by {cov['name']}" if cov else ", NOT COVERED")
            return s

        queue = []
        for e in encs:
            eobs = sorted([o for o in obs if o["encounter_id"] == e["id"]], key=lambda o: o["due_at"])
            next_due = eobs[0]["due_at"] if eobs else None
            rank = e["priority"] if e["priority"] is not None else UNASSESSED_RANK
            queue.append({
                "encounter_id": e["id"], "label": e["label"], "version": e["version"],
                "priority": e["priority"], "priority_text": f"Level {e['priority']}" if e["priority"] else "Unassessed",
                "priority_confirmed_by": uname(e["priority_confirmed_by"]), "priority_confirmed_at": e["priority_confirmed_at"],
                "arrived_at": e["arrived_at"], "primary": uname(e["primary_user"]) or "Unassigned (queue owner responsible)",
                "obligations": [_ob_view(o, now_dt, uname) for o in eobs],
                "display": self._display(e["id"]),
                "unreviewed_notes": sum(1 for i in items if i["encounter_id"] == e["id"]),
                "emergency": next((EMERGENCY_STATUS_TEXT[f["status"]] for f in flags if f["encounter_id"] == e["id"]), None),
                "emergency_open": any(f["encounter_id"] == e["id"] and f["status"] in OPEN_FLAG for f in flags),
                "restricted": bool(e["restricted"]),
                "_key": (rank, next_due or "9999", e["arrived_at"], e["id"]),
            })
        queue.sort(key=lambda q: q["_key"])
        for i, q in enumerate(queue):
            q.pop("_key")
            q["position"] = i + 1
        inbox = [{"item_id": i["id"], "encounter_id": i["encounter_id"], "label": label[i["encounter_id"]],
                  "received_at": i["received_at"], "note_version": i["note_version"],
                  "ai_state": i["ai_state"], "ai_state_text": AI_STATE_TEXT.get(i["ai_state"], i["ai_state"]),
                  "version": i["version"]} for i in items]
        overdue = sorted([dict(_ob_view(o, now_dt, uname), label=label[o["encounter_id"]])
                          for o in obs if o["due_at"] < now], key=lambda o: o["due_at"])
        feed = self.db.one("SELECT * FROM feed WHERE org=?", (org,))
        calls = self.db.q("SELECT outcome, provenance, finished_at FROM model_calls WHERE org=? ORDER BY finished_at DESC, id DESC LIMIT 1", (org,))
        failing = self.db.q("SELECT COUNT(*) n FROM review_items WHERE org=? AND state='unreviewed' AND "
                            "(ai_state LIKE 'failed_%' OR ai_state IN ('retrying','blocked_not_approved_fixture'))", (org,))[0]["n"]
        return {
            "now": now, "times_illustrative": True,
            "mode": {"setting": self.s.mode, "model": self.s.model, "fault": self.adapter.fault,
                     "last_call": calls[0] if calls else None},
            "feed": {"last_message_at": feed["last_message_at"], "stale": bool(feed["stalled_sim"]),
                     "stall_minutes": self.s.feed_stall_minutes},
            "queue_rule": QUEUE_RULE, "inbox_rule": INBOX_RULE,
            "queue": queue, "inbox": inbox, "unreviewed": len(inbox), "overdue": overdue,
            "ai_needs_manual": failing,
            "emergencies_open": sum(1 for f in flags if f["status"] in OPEN_FLAG),
            "me": {"id": user["id"], "name": user["name"], "title": user["title"], "role": user["role"],
                   "can_confirm_priority": user["role"] in CONFIRM_ROLES},
            "escalation_reasons": sorted(ESCALATION_REASONS),
            "training": self.education.training_info(user) if getattr(self, "education", None) else None,
        }

    def _display(self, enc_id):
        """Demographics for the track board (stored with the encounter; never sent to the AI)."""
        row = self.db.one("SELECT display_json FROM encounters WHERE id=?", (enc_id,))
        if row and row["display_json"]:
            return json.loads(row["display_json"])
        e = next((x for x in self.seed["encounters"] if x["id"] == enc_id), None)
        return (e or {}).get("display", {})

    # ------------------------------------------------------------------ privacy: access log + break-the-glass
    def _on_care_team(self, user, enc):
        if user["id"] == enc["primary_user"]:
            return True
        primary = self.db.one("SELECT status, covered_by FROM users WHERE id=?", (enc["primary_user"],)) if enc["primary_user"] else None
        return bool(primary and primary["status"] != "on_shift" and primary["covered_by"] == user["id"])

    def _check_access(self, user, enc, action, reason=None):
        """Every chart open is logged. Restricted records need a reason unless you are on the care team."""
        reason = (reason or "").strip()[:300]
        glass = 0
        if enc.get("restricted") and not self._on_care_team(user, enc):
            if len(reason) < 5:
                raise BreakGlassRequired()
            glass = 1
        now = iso(self.clock.now())
        with self.db.tx() as c:
            c.execute("INSERT INTO chart_access (ts, org, user_id, encounter_id, action, reason, break_glass) VALUES (?,?,?,?,?,?,?)",
                      (now, enc["org"], user["id"], enc["id"], action, reason or None, glass))
            if glass:
                audit(c, now, enc["org"], user["id"], "break_glass", "encounter", enc["id"])
                emit(c, now, enc["org"], "break_glass", encounter_id=enc["id"])

    def access_log(self, user, enc_id):
        e = self._enc_for(user, enc_id)
        rows = self.db.q("SELECT a.*, u.name, u.title FROM chart_access a JOIN users u ON u.id=a.user_id WHERE a.encounter_id=? "
                         "ORDER BY a.seq DESC LIMIT 200", (e["id"],))
        return [{"at": r["ts"], "by": f"{r['name']}, {r['title']}", "action": r["action"].replace("_", " "),
                 "reason": r["reason"], "break_glass": bool(r["break_glass"])} for r in rows]

    def privacy_summary(self, user):
        """Live privacy facts for this unit: what left the server, whether any identifier did, who opened what."""
        if user["role"] not in CLINICAL_ROLES and user["role"] != "admin":
            raise Forbidden()
        org = user["org"]
        calls = self.db.q("SELECT request_json, finished_at, outcome FROM model_calls WHERE org=?", (org,))
        sent = [crypto.dec(c["request_json"]) for c in calls if c["request_json"]]
        identifiers = set()
        for h in self.db.q("SELECT h.header_json FROM encounter_headers h JOIN encounters e ON e.id=h.encounter_id WHERE e.org=?", (org,)):
            for k, v in json.loads(h["header_json"]).items():
                if isinstance(v, str) and len(v) >= 4:
                    identifiers.add((k, v))
        for u in self.db.q("SELECT name FROM users WHERE org=?", (org,)):
            if len(u["name"]) >= 4:
                identifiers.add(("staff_name", u["name"]))
        found = sorted({k for k, v in identifiers for body in sent if v in body})
        scrubbed = sum(body.count("removed]") for body in sent)
        purged = sum(1 for c in calls if not c["request_json"])
        access = self.db.q("SELECT COUNT(*) n, SUM(break_glass) g FROM chart_access WHERE org=?", (org,))[0]
        glass = self.db.q("SELECT a.ts, a.reason, u.name, e.label FROM chart_access a JOIN users u ON u.id=a.user_id "
                          "JOIN encounters e ON e.id=a.encounter_id WHERE a.org=? AND a.break_glass=1 ORDER BY a.seq DESC LIMIT 20", (org,))
        chart_notes = self.db.one("SELECT COUNT(*) n FROM chart_notes WHERE org=?", (org,))["n"]
        return {
            "ai_requests": len(calls), "ai_requests_with_text_kept": len(sent), "ai_requests_purged": purged,
            "identifier_types_checked": sorted({k for k, _ in identifiers}),
            "identifiers_found_in_ai_requests": found,
            "scrubbed_tokens": scrubbed,
            "typed_or_dictated_notes": chart_notes, "typed_or_dictated_sent_to_ai": 0,
            "retention_days": self.s.ai_retention_days,
            "encryption_at_rest": crypto.enabled(),
            "chart_opens_logged": access["n"] or 0, "break_glass_count": access["g"] or 0,
            "break_glass_recent": [{"at": g["ts"], "by": g["name"], "patient": g["label"], "reason": g["reason"]} for g in glass],
            "deployment": self.s.deployment,
            "ai_real_notes_allowed": self.s.ai_may_send_real_notes(),
        }

    def purge_old_ai_text(self):
        """Retention: drop saved AI request/response text after N days. Metadata, findings and decisions stay."""
        cutoff = iso(self.clock.now() - timedelta(days=self.s.ai_retention_days))
        with self.db.tx() as c:
            r = c.execute("UPDATE model_calls SET request_json=NULL, response_json=NULL WHERE finished_at < ? AND "
                          "(request_json IS NOT NULL OR response_json IS NOT NULL)", (cutoff,))
            n = r.rowcount
        if n:
            log_event(iso(self.clock.now()), "retention.ai_text_purged", count=n)
        return n

    def review_item_detail(self, user, item_id, reason=None):
        it = self._item_for(user, item_id)
        enc = self._enc_for(user, it["encounter_id"])
        self._check_access(user, enc, "view_note_review", reason)
        disclosed = self.db.one("SELECT 1 FROM ai_disclosures WHERE review_item_id=?", (item_id,))
        note = self.db.one("SELECT note_id, version, signed_at, received_at, dec(body) AS body FROM notes WHERE note_id=? AND version=?",
                           (it["note_id"], it["note_version"]))
        others = self.db.q("SELECT note_id, version, signed_at, dec(body) AS body FROM notes WHERE encounter_id=? AND NOT "
                           "(note_id=? AND version=?) ORDER BY signed_at, version", (enc["id"], it["note_id"], it["note_version"]))
        call = self.db.one("SELECT * FROM model_calls WHERE review_item_id=? ORDER BY started_at DESC, rowid DESC LIMIT 1", (item_id,))
        fds = self.db.q("SELECT * FROM findings WHERE review_item_id=? ORDER BY idx", (item_id,))
        decs = self.db.q("SELECT d.*, u.name FROM decisions d JOIN users u ON u.id=d.user_id WHERE d.review_item_id=?", (item_id,))
        val = json.loads(call["validation_json"]) if call and call["validation_json"] else {}
        return {
            "item_id": it["id"], "encounter_id": enc["id"], "label": enc["label"], "state": it["state"],
            "version": it["version"], "encounter_version": enc["version"], "priority": enc["priority"],
            "ai_state": it["ai_state"], "ai_state_text": AI_STATE_TEXT.get(it["ai_state"], it["ai_state"]),
            "note": note, "other_notes": others,
            "findings": [dict(json.loads(f["finding_json"]), checks=json.loads(f["checks_json"]), id=f["id"]) for f in fds],
            "reviewer_note": val.get("reviewer_note", ""), "reviewer_note_flags": val.get("reviewer_note_flags", []),
            "model_call": _call_meta(call),
            "decisions": [{"by": d["name"], "at": d["decided_at"], "decision": d["decision"],
                           "corrections": json.loads(d["corrections_json"] or "{}"), "comment": d["comment"]} for d in decs],
            "label_ai": "AI-assisted, unverified. Check every item against the original note.",
            "ai_disclosed": bool(disclosed),
        }

    def ai_request_view(self, user, item_id, reason=None):
        """'What Claude received' panel: read-only view of exactly what was sent."""
        it = self._item_for(user, item_id)
        self._check_access(user, self._enc_for(user, it["encounter_id"]), "view_ai_request", reason)
        call = self.db.one("SELECT * FROM model_calls WHERE review_item_id=? AND request_json IS NOT NULL "
                           "ORDER BY started_at DESC, rowid DESC LIMIT 1", (item_id,))
        note = self.db.one("SELECT fixture_sha256 FROM notes WHERE note_id=? AND version=?", (it["note_id"], it["note_version"]))
        if not call or not note or not note["fixture_sha256"]:
            raise NotFound()
        req = json.loads(crypto.dec(call["request_json"]))
        val = json.loads(call["validation_json"] or "{}")
        return {"mode": call["mode"], "provenance": call["provenance"], "model": call["model"],
                "outcome": call["outcome"], "provider_request_id": call["provider_request_id"],
                "versions": {"prompt": call["prompt_version"], "schema": call["schema_version"],
                             "selector": call["selector_version"], "rules": call["rules_version"]},
                "endpoint": "POST https://api.anthropic.com/v1/messages (only in live mode)",
                "outbound_user_content": json.loads(req["messages"][0]["content"]),
                "outbound_other_fields": {"model": req["model"], "max_tokens": req["max_tokens"],
                                          "system": "(frozen extraction instructions, " + config.PROMPT_VERSION + ")",
                                          "output_config": {"format": "json_schema " + config.SCHEMA_VERSION,
                                                            "effort": req["output_config"].get("effort")},
                                          "tools": "none", "thinking": "model default (adaptive)"},
                "omitted_fields": val.get("omitted_fields", []),
                "returned_text": crypto.dec(call["response_json"])}

    def encounter_detail(self, user, enc_id, reason=None):
        e = self._enc_for(user, enc_id)
        self._check_access(user, e, "view_chart", reason)
        st = self.state(user)
        row = next((q for q in st["queue"] if q["encounter_id"] == enc_id), None)
        notes = self.db.q("SELECT note_id, version, signed_at, received_at, dec(body) AS body FROM notes WHERE encounter_id=? "
                          "ORDER BY signed_at, version", (enc_id,))
        items = self.db.q("SELECT id, note_id, note_version, state, ai_state FROM review_items WHERE encounter_id=? "
                          "AND state='unreviewed' ORDER BY received_at, id", (enc_id,))
        escs = self.db.q("SELECT id, raised_at, reason_code, status FROM escalations WHERE encounter_id=? ORDER BY raised_at", (enc_id,))
        docs = self.db.q("SELECT d.id, d.note_type, d.created_at, dec(d.body) AS body, d.input_method, u.name, u.title FROM chart_notes d "
                         "JOIN users u ON u.id=d.author_user WHERE d.encounter_id=? ORDER BY d.created_at, d.id", (enc_id,))
        return {"encounter_id": e["id"], "label": e["label"], "status": e["status"], "version": e["version"],
                "queue_row": row, "notes": notes,
                "unreviewed_items": [dict(i, ai_state_text=AI_STATE_TEXT.get(i["ai_state"], i["ai_state"])) for i in items],
                "escalations": escs,
                "documents": [{"id": d["id"], "note_type": d["note_type"], "note_type_text": NOTE_TYPES[d["note_type"]],
                               "created_at": d["created_at"], "body": d["body"], "input_method": d["input_method"],
                               "author": f"{d['name']}, {d['title']}"} for d in docs],
                "emergencies": self.emergencies(user, enc_id)["flags"],
                "note_types": NOTE_TYPES, "restricted": bool(e["restricted"])}

    # ------------------------------------------------------------------ chart documents (nurse-written)
    def add_chart_note(self, user, enc_id, body, note_type="nursing_note", input_method="typed"):
        """A nurse documents in the chart (typed or dictated). Stored locally; NEVER sent to the AI
        (only approved synthetic fixtures may leave the server). Emergency rules run locally."""
        e = self._enc_for(user, enc_id)
        body = (body or "").strip()
        if not (3 <= len(body) <= 4000):
            raise BadInput("note length")
        if note_type not in NOTE_TYPES or input_method not in ("typed", "dictated"):
            raise BadInput("note type")
        if e["status"] != "active":
            raise Conflict("Encounter is no longer active.")
        now = iso(self.clock.now())
        nid = _id("doc")
        with self.db.tx() as c:
            c.execute("INSERT INTO chart_notes VALUES (?,?,?,?,?,?,?,?)",
                      (nid, e["org"], enc_id, user["id"], note_type, now, crypto.enc(body), input_method))
            audit(c, now, e["org"], user["id"], "chart_note_added", "encounter", enc_id, note=nid, method=input_method)
            flags = self._run_emergency_rules(c, e, body, nid, user["id"])
            emit(c, now, e["org"], "chart_note_added", encounter_id=enc_id)
        return {"ok": True, "note_id": nid, "emergency_flags_raised": flags}

    # ------------------------------------------------------------------ possible emergencies
    def _run_emergency_rules(self, c, enc, text, note_ref, actor):
        raised = 0
        for hit in emergency.evaluate(text):
            open_same = c.execute("SELECT 1 FROM emergency_flags WHERE encounter_id=? AND rule_id=? AND status IN "
                                  "('awaiting_nurse','awaiting_charge')", (enc["id"], hit["rule_id"])).fetchone()
            if open_same:
                continue
            self._new_flag(c, enc, "rule", hit["rule_id"], hit["label"], hit["evidence"], note_ref, actor)
            raised += 1
        return raised

    def _new_flag(self, c, enc, source, rule_id, label, evidence, note_ref, actor, reason=None):
        now_dt = self.clock.now()
        now = iso(now_dt)
        fid = _id("em")
        c.execute("INSERT INTO emergency_flags (id, org, encounter_id, source, rule_id, factor_label, evidence, note_ref, "
                  "raised_by, raised_at, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (fid, enc["org"], enc["id"], source, rule_id, label, evidence, note_ref, actor, now, "awaiting_nurse"))
        c.execute("INSERT INTO emergency_events (flag_id, org, at, user_id, action, decision, reason) VALUES (?,?,?,?,?,?,?)",
                  (fid, enc["org"], now, actor, "raised", source, reason))
        nurse = self._effective_owner(c, enc["primary_user"]) or self._queue_owner(c, enc["org"])
        for kind, mins_, owner in (("emergency_check", EMERGENCY_CHECK_MINUTES, nurse),
                                   ("emergency_decision", EMERGENCY_DECISION_MINUTES, self._queue_owner(c, enc["org"]))):
            c.execute("INSERT INTO obligations (id, org, encounter_id, kind, due_at, created_at, owner, source, source_ref) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (_id("ob"), enc["org"], enc["id"], kind,
                                                     iso(now_dt + timedelta(minutes=mins_)), now, owner, "emergency_flag", fid))
        audit(c, now, enc["org"], actor, "emergency_flag_raised", "encounter", enc["id"], flag=fid, rule=rule_id or "manual")
        emit(c, now, enc["org"], "emergency_flag", flag_id=fid, encounter_id=enc["id"])
        return fid

    def raise_manual_flag(self, user, enc_id, reason):
        e = self._enc_for(user, enc_id)
        reason = emergency.clean_reason(reason)
        if len(reason) < 5:
            raise BadInput("Please write why you think this may be an emergency.")
        with self.db.tx() as c:
            fid = self._new_flag(c, e, "nurse", None, "Flagged by a nurse", None, None, user["id"], reason)
        return {"ok": True, "flag_id": fid}

    def _flag_for(self, user, flag_id):
        f = self.db.one("SELECT * FROM emergency_flags WHERE id=?", (flag_id,))
        if not f or f["org"] != user["org"] or user["role"] not in CLINICAL_ROLES:
            raise NotFound()
        return f

    def assess_flag(self, user, flag_id, assessment, reason, expected_version=None):
        """Any nurse: is this really an emergency? A written reason is required."""
        f = self._flag_for(user, flag_id)
        if assessment not in ("emergency", "not_emergency"):
            raise BadInput("assessment")
        reason = emergency.clean_reason(reason)
        if len(reason) < 5:
            raise BadInput("Please write why you think this is or is not an emergency.")
        if expected_version is not None and int(expected_version) != f["version"]:
            raise Conflict("Someone else updated this flag. Reload to see their decision first.")
        now_dt = self.clock.now()
        now = iso(now_dt)
        with self.db.tx() as c:
            r = c.execute("UPDATE emergency_flags SET nurse_assessment=?, nurse_by=?, nurse_at=?, status='awaiting_charge', "
                          "version=version+1 WHERE id=? AND version=?", (assessment, user["id"], now, flag_id, f["version"]))
            if r.rowcount != 1:
                raise Conflict("Someone else updated this flag. Reload to see their decision first.")
            c.execute("INSERT INTO emergency_events (flag_id, org, at, user_id, action, decision, reason) VALUES (?,?,?,?,?,?,?)",
                      (flag_id, f["org"], now, user["id"], "nurse_assessment", assessment, reason))
            c.execute("UPDATE obligations SET status='completed', completed_at=?, completed_by=? WHERE kind='emergency_check' "
                      "AND source_ref=? AND status='open'", (now, user["id"], flag_id))
            if f["status"] in ("confirmed_emergency", "not_emergency"):
                # re-opened after a final decision: the charge nurse gets a fresh decision task
                c.execute("INSERT INTO obligations (id, org, encounter_id, kind, due_at, created_at, owner, source, source_ref) "
                          "VALUES (?,?,?,?,?,?,?,?,?)", (_id("ob"), f["org"], f["encounter_id"], "emergency_decision",
                                                         iso(now_dt + timedelta(minutes=EMERGENCY_DECISION_MINUTES)), now,
                                                         self._queue_owner(c, f["org"]), "emergency_reassessed", flag_id))
            audit(c, now, f["org"], user["id"], "emergency_nurse_assessment", "emergency_flag", flag_id, decision=assessment)
            emit(c, now, f["org"], "emergency_updated", flag_id=flag_id, encounter_id=f["encounter_id"])
        return {"ok": True}

    def determine_flag(self, user, flag_id, determination, reason, expected_version=None):
        """Charge nurse: final determination (can be changed again later). Confirmed -> Level 1."""
        f = self._flag_for(user, flag_id)
        if user["role"] not in CONFIRM_ROLES:
            raise Forbidden()
        if determination not in ("confirmed_emergency", "not_emergency"):
            raise BadInput("determination")
        reason = emergency.clean_reason(reason)
        if len(reason) < 5:
            raise BadInput("Please write the reason for your decision.")
        if expected_version is not None and int(expected_version) != f["version"]:
            raise Conflict("Someone else updated this flag. Reload to see their decision first.")
        now = iso(self.clock.now())
        with self.db.tx() as c:
            r = c.execute("UPDATE emergency_flags SET final_determination=?, final_by=?, final_at=?, status=?, version=version+1 "
                          "WHERE id=? AND version=?", (determination, user["id"], now, determination, flag_id, f["version"]))
            if r.rowcount != 1:
                raise Conflict("Someone else updated this flag. Reload to see their decision first.")
            c.execute("INSERT INTO emergency_events (flag_id, org, at, user_id, action, decision, reason) VALUES (?,?,?,?,?,?,?)",
                      (flag_id, f["org"], now, user["id"], "charge_determination", determination, reason))
            c.execute("UPDATE obligations SET status='completed', completed_at=?, completed_by=? WHERE kind IN "
                      "('emergency_decision','emergency_check') AND source_ref=? AND status='open'", (now, user["id"], flag_id))
            moved = False
            if determination == "confirmed_emergency":
                e = dict(c.execute("SELECT * FROM encounters WHERE id=?", (f["encounter_id"],)).fetchone())
                if e["status"] == "active" and e["priority"] != 1:
                    self._write_priority(c, e, 1, user, "emergency_confirmed", flag_id)
                    moved = True
            audit(c, now, f["org"], user["id"], "emergency_determination", "emergency_flag", flag_id, decision=determination)
            emit(c, now, f["org"], "emergency_updated", flag_id=flag_id, encounter_id=f["encounter_id"])
        return {"ok": True, "moved_to_level_1": moved}

    def emergencies(self, user, encounter_id=None):
        if user["role"] not in CLINICAL_ROLES:
            raise Forbidden()
        names = {u["id"]: f"{u['name']}, {u['title']}" for u in self.db.q("SELECT * FROM users WHERE org=?", (user["org"],))}
        names.update({"rules_engine": "Emergency rules (automatic)", "ehr_simulator": "Records feed"})
        labels = {e["id"]: e["label"] for e in self.db.q("SELECT id, label FROM encounters WHERE org=?", (user["org"],))}
        sql = "SELECT * FROM emergency_flags WHERE org=?" + (" AND encounter_id=?" if encounter_id else "") + \
              " ORDER BY CASE status WHEN 'awaiting_nurse' THEN 0 WHEN 'awaiting_charge' THEN 1 ELSE 2 END, raised_at, id"
        rows = self.db.q(sql, (user["org"], encounter_id) if encounter_id else (user["org"],))
        out = []
        for f in rows:
            ev = self.db.q("SELECT * FROM emergency_events WHERE flag_id=? ORDER BY seq", (f["id"],))
            out.append({"id": f["id"], "encounter_id": f["encounter_id"], "label": labels.get(f["encounter_id"]),
                        "source": f["source"], "rule_id": f["rule_id"], "factor": f["factor_label"],
                        "why": next((r["why"] for r in emergency.describe_rules() if r["id"] == f["rule_id"]), None),
                        "evidence": f["evidence"], "note_ref": f["note_ref"], "raised_at": f["raised_at"],
                        "raised_by": names.get(f["raised_by"], f["raised_by"]), "status": f["status"],
                        "status_text": EMERGENCY_STATUS_TEXT[f["status"]], "version": f["version"],
                        "history": [{"at": x["at"], "by": names.get(x["user_id"], x["user_id"]), "action": x["action"],
                                     "decision": x["decision"], "reason": x["reason"]} for x in ev]})
        return {"flags": out, "rules": emergency.describe_rules(), "rules_version": emergency.RULES_VERSION,
                "can_determine": user["role"] in CONFIRM_ROLES}

    def encounter_obligations(self, user, enc_id):
        self._enc_for(user, enc_id)
        return self.db.q("SELECT * FROM obligations WHERE encounter_id=? ORDER BY due_at", (enc_id,))

    # ------------------------------------------------------------------ simulator (demo only)
    def sim_close(self, org, enc_id, status="closed"):
        e = self.db.one("SELECT * FROM encounters WHERE id=? AND org=?", (enc_id, org))
        if not e:
            raise NotFound()
        now = iso(self.clock.now())
        with self.db.tx() as c:
            c.execute("UPDATE encounters SET status=?, version=version+1 WHERE id=?", (status, enc_id))
            audit(c, now, org, "ehr_simulator", "encounter_" + status, "encounter", enc_id)
            emit(c, now, org, "encounter_closed", encounter_id=enc_id)

    def sim_revoke(self, user_id):
        u = self.db.one("SELECT * FROM users WHERE id=?", (user_id,))
        if not u:
            raise NotFound()
        now = iso(self.clock.now())
        with self.db.tx() as c:
            c.execute("UPDATE users SET active=0 WHERE id=?", (user_id,))
            c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            audit(c, now, u["org"], "admin_simulator", "access_revoked", "user", user_id)
            emit(c, now, u["org"], "access_revoked", user_id=user_id)


AI_STATE_TEXT = {
    "queued": "AI extraction queued",
    "in_progress": "AI extraction running",
    "retrying": "AI extraction failed, retrying; manual review available now",
    "findings": "AI-assisted, unverified: candidate evidence ready",
    "no_qualifying_finding": "AI-assisted: no qualifying finding identified; review the note",
    "insufficient_context": "AI-assisted: insufficient context; review the note",
    "conflicting_evidence": "AI-assisted, unverified: conflicting evidence; review the note",
    "malformed": "AI output failed validation; manual review required",
    "stale_discarded": "AI result discarded because the note changed; manual review required",
    "blocked_not_approved_fixture": "Not sent to AI (not approved for AI in this deployment); manual review required",
    "not_called_no_watch_terms": "Not sent to AI (no watched terms found by local selector); manual review required",
    "not_called_context_exceeds_bound": "Not sent to AI (context too long to send safely); manual review required",
    "failed_timeout": "AI extraction timed out; manual review required",
    "failed_rate_limited": "AI extraction rate-limited; manual review required",
    "failed_provider_error": "AI service unavailable; manual review required",
    "failed_connection_error": "AI service unreachable; manual review required",
    "failed_exception": "AI extraction error; manual review required",
    "failed_refused": "AI declined this request; manual review required",
    "failed_truncated": "AI output incomplete; manual review required",
    "failed_auth_error": "AI credentials rejected; manual review required",
    "failed_bad_request": "AI request rejected; manual review required",
    "failed_no_replay": "No saved replay for this note; manual review required",
    "not_run_seeded": "Reviewed before demo start",
}

_CORRECTABLE = {"subject": validate._ENUMS["subject"], "assertion": validate._ENUMS["assertion"],
                "time_context": validate._ENUMS["time_context"], "change": validate._ENUMS["change"]}


def _check_corrections(corr):
    if not isinstance(corr, dict):
        raise BadInput("corrections")
    for fid, fields in corr.items():
        if not isinstance(fields, dict):
            raise BadInput("corrections")
        for k, v in fields.items():
            if k not in _CORRECTABLE or v not in _CORRECTABLE[k]:
                raise BadInput("correction field")


def _ob_view(o, now_dt, uname):
    due = parse(o["due_at"])
    mins = int((due - now_dt).total_seconds() // 60)
    return {"id": o["id"], "encounter_id": o["encounter_id"], "kind": o["kind"],
            "kind_text": o["kind"].replace("_", " "), "due_at": o["due_at"],
            "overdue": due < now_dt, "minutes": mins,
            "owner": uname(o["owner"]) or "Unassigned (queue owner responsible)",
            "accepted_by": uname(o["accepted_by"]), "accepted_at": o["accepted_at"], "reached_at": o["reached_at"],
            "escalated_to": uname(o["escalated_to"]), "escalated_at": o["escalated_at"]}


def _call_meta(call):
    if not call:
        return None
    return {k: call[k] for k in ("mode", "provenance", "model", "prompt_version", "schema_version", "selector_version",
                                 "rules_version", "started_at", "finished_at", "outcome", "provider_request_id")}


_DEMO_HASH = None


def _demo_password_hash():
    """Demo accounts share the training password "demo" (hashed once per process)."""
    global _DEMO_HASH
    if _DEMO_HASH is None:
        _DEMO_HASH = accounts.hash_password("demo")
    return _DEMO_HASH


def excerpt_sha(body):
    from .adapter import sha
    return sha(body)
