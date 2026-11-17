"""Education mode: practice scenarios, student case builder, sandboxed training sessions, and scoring.

How it works
- A *scenario* is synthetic patients + notes released in timed "events" + an answer key.
- Built-in scenarios ship with the app. Instructors write more. Students build their own cases
  (practice privately, then submit to the class); an instructor reviews and approves them.
- Starting a scenario creates a private *sandbox*: its own unit (org), its own copy of the patients,
  and a training identity for the learner. Everything in the real workflow works there: AI evidence,
  reviews, emergency flags, escalation, priority changes. Nothing touches real patients or classmates.
- Finishing scores the learner's decisions against the answer key, with feedback per item.

Safety
- Scenario text is checked for things that look like real patient information and must be marked
  as made up. Only built-in or instructor-approved scenario text may ever be sent to the AI.
"""
import json
import re
import secrets
from datetime import timedelta

from . import crypto
from .adapter import sha
from .clock import iso
from .db import audit
from .services import BadInput, Conflict, Forbidden, NotFound

ROLE_CHOICES = {"rn": "Staff nurse", "charge_rn": "Charge nurse", "provider": "Resident / provider"}
LIMITS = {"patients": 10, "notes": 40, "text": 1500, "events": 10}
LABEL_RE = re.compile(r"^(Patient|Bed|Pt)\s[A-Za-z0-9-]{1,12}$")

# Patterns that suggest real patient information. Synthetic cases should use labels like "Patient E".
PHI_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "a Social Security number"),
    (re.compile(r"\b\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b"), "a phone number"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "an email address"),
    (re.compile(r"https?://|www\.", re.I), "a web link"),
    (re.compile(r"\b\d{6,}\b"), "a long ID number (like a record number)"),
    (re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"), "a full date"),
    (re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b", re.I), "a full date"),
    (re.compile(r"\b(DOB|MRN|SSN|date of birth|medical record number)\b", re.I), "an identifier label"),
    (re.compile(r"\b(Mr|Mrs|Ms|Miss|Dr)\.?\s+[A-Z][a-z]+"), "a person's name"),
    (re.compile(r"\b\d{1,5}\s+[A-Z][a-z]+\s+(St|Street|Ave|Avenue|Rd|Road|Blvd|Lane|Ln|Drive|Dr|Way|Ct|Court)\b"), "a street address"),
]


def phi_problems(texts):
    found = []
    for t in texts:
        for rx, what in PHI_PATTERNS:
            m = rx.search(t or "")
            if m:
                found.append(f"“{m.group(0)}” looks like {what}")
    return sorted(set(found))


def validate_spec(spec):
    """Return a cleaned spec or raise BadInput with a plain-language message."""
    if not isinstance(spec, dict):
        raise BadInput("Please send a scenario.")
    pats, notes = spec.get("patients") or [], spec.get("notes") or []
    if not (1 <= len(pats) <= LIMITS["patients"]):
        raise BadInput(f"Please add between 1 and {LIMITS['patients']} patients.")
    if not (1 <= len(notes) <= LIMITS["notes"]):
        raise BadInput(f"Please add between 1 and {LIMITS['notes']} notes.")
    clean_p, keys = [], set()
    for i, p in enumerate(pats, 1):
        key = str(p.get("key") or f"P{i}")[:8]
        if not re.match(r"^[A-Za-z0-9]{1,8}$", key) or key in keys:
            raise BadInput(f"Patient {i} needs a unique short key (letters/numbers).")
        keys.add(key)
        label = str(p.get("label") or "").strip()
        if not LABEL_RE.match(label):
            raise BadInput(f"Patient {i}: please use a made-up label like “Patient E” or “Bed 3”, not a real name.")
        try:
            age = int(p.get("age")) if p.get("age") not in (None, "") else None
        except (TypeError, ValueError):
            raise BadInput(f"{label}: age must be a number.")
        if age is not None and not 0 <= age <= 120:
            raise BadInput(f"{label}: age must be between 0 and 120.")
        prio = p.get("priority")
        prio = int(prio) if prio not in (None, "", "U", "u") else None
        if prio is not None and prio not in (1, 2, 3, 4):
            raise BadInput(f"{label}: starting acuity must be 1-4 or unassessed.")
        ans = p.get("answer") or {}
        maxa = ans.get("max_acuity")
        maxa = int(maxa) if maxa not in (None, "") else None
        if maxa is not None and maxa not in (1, 2, 3, 4):
            raise BadInput(f"{label}: expected acuity must be 1-4.")
        clean_p.append({
            "key": key, "label": label, "age": age, "sex": str(p.get("sex") or "")[:1].upper() or None,
            "complaint": str(p.get("complaint") or "")[:60], "location": str(p.get("location") or "")[:30],
            "priority": prio, "restricted": bool(p.get("restricted")),
            "answer": {"emergency": _tri(ans.get("emergency")), "max_acuity": maxa, "escalate": _tri(ans.get("escalate"))},
        })
    clean_n, nkeys = [], set()
    for i, n in enumerate(notes, 1):
        key = str(n.get("key") or f"N{i}")[:8]
        if not re.match(r"^[A-Za-z0-9]{1,8}$", key) or key in nkeys:
            raise BadInput(f"Note {i} needs a unique short key.")
        nkeys.add(key)
        if n.get("patient") not in keys:
            raise BadInput(f"Note {i}: pick which patient it belongs to.")
        text = str(n.get("text") or "").strip()
        if not (5 <= len(text) <= LIMITS["text"]):
            raise BadInput(f"Note {i}: write between 5 and {LIMITS['text']} characters.")
        try:
            event = int(n.get("event") or 0)
        except (TypeError, ValueError):
            raise BadInput(f"Note {i}: the event number must be a number.")
        if not 0 <= event <= LIMITS["events"]:
            raise BadInput(f"Note {i}: event must be 0 (already in chart) to {LIMITS['events']}.")
        trap = n.get("ai_trap")
        if trap:
            if trap.get("field") not in ("assertion", "subject", "time_context"):
                raise BadInput(f"Note {i}: the AI-check field must be assertion, subject or time context.")
            trap = {"field": trap["field"], "correct": str(trap.get("correct"))[:20]}
        clean_n.append({"key": key, "patient": n["patient"], "event": event, "text": text,
                        "ai_trap": trap or None, "replay": n.get("replay")})
    if not any(n["event"] >= 1 for n in clean_n):
        raise BadInput("Add at least one note with event 1 or later, so something new arrives during practice.")
    problems = phi_problems([n["text"] for n in clean_n] + [p["complaint"] for p in clean_p] + [p["location"] for p in clean_p])
    if problems:
        raise BadInput("This looks like it may contain real patient information: " + "; ".join(problems[:4])
                       + ". Please use only made-up details.")
    return {"patients": clean_p, "notes": clean_n}


def _tri(v):
    if v in (True, "yes", "true", 1):
        return True
    if v in (False, "no", "false", 0):
        return False
    return None


class Education:
    def __init__(self, svc):
        self.svc = svc
        self.db = svc.db
        svc.education = self

    # ------------------------------------------------------------------ built-in library
    def seed_builtin(self):
        if self.db.one("SELECT 1 FROM scenarios WHERE status='builtin'"):
            return
        seed = self.svc.seed
        texts = {(n["note_id"], n["version"]): n["body"] for n in seed["seeded_notes"] + seed["deliverable_notes"]}
        disp = {e["id"]: e for e in seed["encounters"]}

        def pat(key, enc, prio, answer):
            d = disp[enc]["display"]
            return {"key": key, "label": disp[enc]["label"], "age": d["age"], "sex": d["sex"], "complaint": d["complaint"],
                    "location": d["location"], "priority": prio, "restricted": False, "answer": answer}

        s1 = {"patients": [
                pat("A", "enc_A", 2, {"emergency": None, "max_acuity": None, "escalate": None}),
                pat("B", "enc_B", 3, {"emergency": None, "max_acuity": 2, "escalate": None}),
                pat("C", "enc_C", 3, {"emergency": None, "max_acuity": None, "escalate": None}),
                pat("D", "enc_D", None, {"emergency": True, "max_acuity": 1, "escalate": None})],
              "notes": [
                {"key": "A1", "patient": "A", "event": 0, "text": texts[("note_A1", 1)], "ai_trap": None, "replay": None},
                {"key": "B1", "patient": "B", "event": 0, "text": texts[("note_B1", 1)], "ai_trap": None, "replay": None},
                {"key": "C1", "patient": "C", "event": 0, "text": texts[("note_C1", 1)], "ai_trap": None, "replay": None},
                {"key": "B2", "patient": "B", "event": 1, "text": texts[("note_B2", 1)], "ai_trap": None, "replay": "note_B2:1"},
                {"key": "A3", "patient": "A", "event": 2, "text": texts[("note_A3", 1)],
                 "ai_trap": {"field": "assertion", "correct": "denied"}, "replay": "note_A3:1"},
                {"key": "C2", "patient": "C", "event": 3, "text": texts[("note_C2", 1)], "ai_trap": None, "replay": "note_C2:1"},
                {"key": "D1", "patient": "D", "event": 4, "text": texts[("note_D1", 1)], "ai_trap": None, "replay": None}]}
        s2 = {"patients": [
                {"key": "E", "label": "Patient E", "age": 58, "sex": "M", "complaint": "Demo concern Z", "location": "Lobby",
                 "priority": 3, "restricted": False, "answer": {"emergency": True, "max_acuity": None, "escalate": True}},
                {"key": "F", "label": "Patient F", "age": 22, "sex": "F", "complaint": "Demo concern Y", "location": "Room 6",
                 "priority": 3, "restricted": False, "answer": {"emergency": False, "max_acuity": None, "escalate": None}},
                {"key": "G", "label": "Patient G", "age": 70, "sex": "F", "complaint": "Demo concern X", "location": "Room 8",
                 "priority": 2, "restricted": True, "answer": {"emergency": False, "max_acuity": None, "escalate": None}}],
              "notes": [
                {"key": "F0", "patient": "F", "event": 0, "text": "Triage note. Patient reports demo signal B, mild.", "ai_trap": None, "replay": None},
                {"key": "G0", "patient": "G", "event": 0, "text": "Triage note. Patient reports demo signal B. Record marked restricted at patient request.", "ai_trap": None, "replay": None},
                {"key": "E1", "patient": "E", "event": 1, "text": "Nursing note. Patient reports demo red flag R that began five minutes ago. Skin pale and clammy.", "ai_trap": None, "replay": None},
                {"key": "F1", "patient": "F", "event": 1, "text": "Nursing note. Patient denies demo red flag R. Patient's daughter had demo red flag R last year.", "ai_trap": None, "replay": None},
                {"key": "G2", "patient": "G", "event": 2, "text": "Late entry. Demo red flag S resolved last week and has not recurred. Patient reports demo signal B is unchanged.", "ai_trap": None, "replay": None}]}
        now = iso(self.svc.clock.now())
        with self.db.tx() as c:
            for sid, title, summary, goals, role, spec in (
                ("sc_builtin_story", "The changing story",
                 "Four patients in an urgent care lobby. Notes arrive over time: a symptom that was denied is now reported, "
                 "the AI makes a mistake, an old finding looks new, and a possible emergency appears.",
                 "Check AI evidence against the note · Catch an AI misreading · Tell past from present · Respond to a possible emergency",
                 "charge_rn", s1),
                ("sc_builtin_emergency", "Emergency or not?",
                 "Three patients. One note describes a real warning sign, one only mentions a relative's history, and one "
                 "describes something that already resolved. One record is restricted.",
                 "Decide when a flag is a real emergency · Write clear reasons · Escalate early · Respect restricted records",
                 "rn", s2)):
                c.execute("INSERT INTO scenarios VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (sid, "builtin", title, summary, goals, role, "builtin", "system", now, now, json.dumps(spec), None))
                for n in spec["notes"]:
                    c.execute("INSERT OR IGNORE INTO approved_texts VALUES (?,?,?)", (sha(n["text"]), "builtin", now))

    # ------------------------------------------------------------------ library
    def _learner(self, user):
        if user["role"] not in ("student", "instructor"):
            raise Forbidden()

    def list_scenarios(self, user):
        self._learner(user)
        rows = self.db.q("SELECT s.*, u.name AS author FROM scenarios s LEFT JOIN users u ON u.id=s.created_by "
                         "WHERE s.status='builtin' OR (s.org=? AND (s.status='approved' OR s.created_by=? OR ?)) "
                         "ORDER BY CASE s.status WHEN 'builtin' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, s.updated_at DESC",
                         (user["org"], user["id"], 1 if user["role"] == "instructor" else 0))
        return [self._summary(r, user) for r in rows]

    def _summary(self, r, user):
        spec = json.loads(r["spec_json"])
        return {"id": r["id"], "title": r["title"], "summary": r["summary"], "goals": r["goals"], "role": r["role"],
                "role_text": ROLE_CHOICES.get(r["role"], r["role"]), "status": r["status"],
                "author": "Built-in" if r["status"] == "builtin" else (r["author"] or "Unknown"),
                "mine": r["created_by"] == user["id"], "patients": len(spec["patients"]),
                "events": max(n["event"] for n in spec["notes"]), "review_comment": r["review_comment"],
                "ai_enabled": r["status"] in ("builtin", "approved")}

    def _scenario(self, user, sid, for_edit=False):
        r = self.db.one("SELECT s.*, u.name AS author FROM scenarios s LEFT JOIN users u ON u.id=s.created_by WHERE s.id=?", (sid,))
        if not r:
            raise NotFound()
        visible = r["status"] == "builtin" or (r["org"] == user["org"] and (
            r["status"] == "approved" or r["created_by"] == user["id"] or user["role"] == "instructor"))
        if not visible:
            raise NotFound()
        if for_edit and not (r["created_by"] == user["id"] or (user["role"] == "instructor" and r["org"] == user["org"])):
            raise Forbidden("Only the author or an instructor can edit this case.")
        if for_edit and r["status"] == "builtin":
            raise Forbidden("Built-in scenarios can't be edited. Make a copy instead.")
        return r

    def get_scenario(self, user, sid):
        self._learner(user)
        r = self._scenario(user, sid)
        out = self._summary(r, user)
        out["spec"] = json.loads(r["spec_json"])
        out["can_edit"] = r["status"] != "builtin" and (r["created_by"] == user["id"] or user["role"] == "instructor")
        return out

    def save_scenario(self, user, sid, payload):
        self._learner(user)
        if not payload.get("synthetic_attested"):
            raise BadInput("Please confirm that every detail is made up and contains no real patient information.")
        title = str(payload.get("title") or "").strip()[:80]
        if len(title) < 3:
            raise BadInput("Please give the case a title.")
        role = payload.get("role") if payload.get("role") in ROLE_CHOICES else "charge_rn"
        spec = validate_spec(payload.get("spec"))
        summary = str(payload.get("summary") or "").strip()[:500] or "Student-built practice case."
        goals = str(payload.get("goals") or "").strip()[:300] or "Practice reviewing notes and emergency decisions."
        problems = phi_problems([title, summary, goals])
        if problems:
            raise BadInput("This looks like it may contain real patient information: " + "; ".join(problems[:3]) + ".")
        now = iso(self.svc.clock.now())
        with self.db.tx() as c:
            if sid:
                r = self._scenario(user, sid, for_edit=True)
                status = r["status"] if user["role"] == "instructor" else "draft"
                c.execute("UPDATE scenarios SET title=?, summary=?, goals=?, role=?, spec_json=?, status=?, updated_at=? WHERE id=?",
                          (title, summary, goals, role, json.dumps(spec), status, now, sid))
                if status == "approved":
                    self._approve_texts(c, spec, user)
            else:
                sid = "sc_" + secrets.token_hex(5)
                status = "approved" if user["role"] == "instructor" else "draft"
                c.execute("INSERT INTO scenarios VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (sid, user["org"], title, summary, goals, role, status, user["id"], now, now, json.dumps(spec), None))
                if status == "approved":
                    self._approve_texts(c, spec, user)
            audit(c, now, user["org"], user["id"], "scenario_saved", "scenario", sid, status=status)
        return {"ok": True, "id": sid, "status": status}

    def _approve_texts(self, c, spec, user):
        now = iso(self.svc.clock.now())
        for n in spec["notes"]:
            c.execute("INSERT OR IGNORE INTO approved_texts VALUES (?,?,?)", (sha(n["text"]), user["id"], now))

    def submit_scenario(self, user, sid):
        if user["role"] != "student":
            raise Forbidden()
        r = self._scenario(user, sid, for_edit=True)
        if r["status"] not in ("draft",):
            raise Conflict("This case was already submitted.")
        with self.db.tx() as c:
            c.execute("UPDATE scenarios SET status='submitted', review_comment=NULL, updated_at=? WHERE id=?",
                      (iso(self.svc.clock.now()), sid))
            audit(c, iso(self.svc.clock.now()), user["org"], user["id"], "scenario_submitted", "scenario", sid)
        return {"ok": True}

    def review_scenario(self, user, sid, action, comment=""):
        if user["role"] != "instructor":
            raise Forbidden()
        r = self._scenario(user, sid, for_edit=True)
        if action not in ("approve", "return"):
            raise BadInput("Choose approve or return.")
        comment = (comment or "").strip()[:500]
        if action == "return" and len(comment) < 5:
            raise BadInput("Please tell the student what to change.")
        with self.db.tx() as c:
            if action == "approve":
                c.execute("UPDATE scenarios SET status='approved', review_comment=?, updated_at=? WHERE id=?",
                          (comment or None, iso(self.svc.clock.now()), sid))
                self._approve_texts(c, json.loads(r["spec_json"]), user)
            else:
                c.execute("UPDATE scenarios SET status='draft', review_comment=?, updated_at=? WHERE id=?",
                          (comment, iso(self.svc.clock.now()), sid))
            audit(c, iso(self.svc.clock.now()), user["org"], user["id"], "scenario_" + action, "scenario", sid)
        return {"ok": True}

    def delete_scenario(self, user, sid):
        self._learner(user)
        r = self._scenario(user, sid, for_edit=True)
        if r["status"] == "approved" and user["role"] != "instructor":
            raise Forbidden("Approved cases can only be removed by an instructor.")
        with self.db.tx() as c:
            c.execute("DELETE FROM scenarios WHERE id=?", (sid,))
            audit(c, iso(self.svc.clock.now()), user["org"], user["id"], "scenario_deleted", "scenario", sid)
        return {"ok": True}

    # ------------------------------------------------------------------ sandbox sessions
    def start_session(self, user, sid, parent_token):
        self._learner(user)
        r = self._scenario(user, sid)
        spec = json.loads(r["spec_json"])
        hexid = secrets.token_hex(5)
        org, shadow, tid = f"trn_{hexid}", f"trn_u_{hexid}", f"ts_{hexid}"
        now_dt = self.svc.clock.now()
        now = iso(now_dt)
        events_total = max(n["event"] for n in spec["notes"])
        with self.db.tx() as c:
            c.execute("INSERT INTO orgs VALUES (?,?)", (org, f"Practice: {r['title']}"))
            c.execute("INSERT INTO feed (org, last_message_at) VALUES (?,?)", (org, now))
            c.execute("INSERT INTO users (id, org, name, role, title, status) VALUES (?,?,?,?,?,?)",
                      (shadow, org, user["name"], r["role"], f"Trainee · {ROLE_CHOICES[r['role']]}", "on_shift"))
            if r["role"] != "charge_rn":
                c.execute("INSERT INTO users (id, org, name, role, title, status) VALUES (?,?,?,?,?,?)",
                          (f"trn_c_{hexid}", org, "Sim charge nurse", "charge_rn", "Simulated charge nurse", "on_shift"))
            for i, p in enumerate(spec["patients"]):
                eid = f"{org}_{p['key']}"
                arrived = iso(now_dt - timedelta(minutes=45 - 7 * i))
                c.execute("INSERT INTO encounters (id, org, label, arrived_at, priority, priority_confirmed_by, priority_confirmed_at, "
                          "primary_user, restricted, display_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (eid, org, p["label"], arrived, p["priority"], shadow if p["priority"] else None,
                           arrived if p["priority"] else None, None if p["restricted"] else shadow, 1 if p["restricted"] else 0,
                           json.dumps({"age": p["age"], "sex": p["sex"], "complaint": p["complaint"], "location": p["location"]})))
                c.execute("INSERT INTO encounter_headers VALUES (?,?)",
                          (eid, json.dumps({"mrn": f"SIM-{hexid}-{p['key']}"})))
                kind, mins = ("reassessment", 30 + 5 * i) if p["priority"] else ("initial_assessment", 15)
                c.execute("INSERT INTO obligations (id, org, encounter_id, kind, due_at, created_at, owner, source) VALUES (?,?,?,?,?,?,?,?)",
                          (f"ob_{hexid}_{p['key']}", org, eid, kind, iso(now_dt + timedelta(minutes=mins)), now,
                           None if p["restricted"] else shadow, "scenario"))
            for n in spec["notes"]:
                nid = f"{org}:{n['key']}"
                if n.get("replay"):
                    rn, rv = n["replay"].split(":")
                    c.execute("INSERT INTO note_replay VALUES (?,?,?,?)", (nid, 1, rn, int(rv)))
                if n["event"] == 0:
                    signed = iso(now_dt - timedelta(minutes=40))
                    c.execute("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?)",
                              (nid, 1, f"{org}_{n['patient']}", org, "SIM", signed, signed, crypto.enc(n["text"]), None))
                    c.execute("INSERT INTO review_items (id, org, encounter_id, note_id, note_version, received_at, state, ai_state, "
                              "reviewed_by, reviewed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (f"ri_{secrets.token_hex(5)}", org, f"{org}_{n['patient']}", nid, 1, signed, "reviewed",
                               "not_run_seeded", shadow, signed))
            c.execute("INSERT INTO training_sessions (id, scenario_id, user_id, home_org, sandbox_org, shadow_user, parent_token, "
                      "started_at, event_index, events_total) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (tid, sid, user["id"], user["org"], org, shadow, parent_token or "", now, 0, events_total))
            audit(c, now, user["org"], user["id"], "training_started", "scenario", sid, session=tid)
        shadow_user = self.db.one("SELECT * FROM users WHERE id=?", (shadow,))
        token = self.svc._new_session(shadow_user)
        self.next_event(shadow_user)
        return {"ok": True, "session_id": tid, "token": token}

    def _session_for_shadow(self, user):
        t = self.db.one("SELECT * FROM training_sessions WHERE shadow_user=?", (user["id"],))
        if not t:
            raise NotFound()
        return t

    def training_info(self, user):
        if not user["id"].startswith("trn_u_"):
            return None
        t = self._session_for_shadow(user)
        sc = self.db.one("SELECT title, goals FROM scenarios WHERE id=?", (t["scenario_id"],))
        return {"session_id": t["id"], "title": sc["title"] if sc else "Practice", "goals": sc["goals"] if sc else "",
                "event_index": t["event_index"], "events_total": t["events_total"], "finished": bool(t["finished_at"])}

    def next_event(self, user):
        t = self._session_for_shadow(user)
        if t["finished_at"]:
            raise Conflict("This practice session is finished.")
        if t["event_index"] >= t["events_total"]:
            raise Conflict("No more events. Finish when you're ready.")
        k = t["event_index"] + 1
        sc = self.db.one("SELECT spec_json FROM scenarios WHERE id=?", (t["scenario_id"],))
        spec = json.loads(sc["spec_json"]) if sc else {"notes": []}
        self.svc.clock.advance(minutes=1)
        for n in [n for n in spec["notes"] if n["event"] == k]:
            enc = self.db.one("SELECT * FROM encounters WHERE id=?", (f"{t['sandbox_org']}_{n['patient']}",))
            if not enc or enc["status"] != "active":
                continue
            self.svc.ingest_note(enc, {"note_id": f"{t['sandbox_org']}:{n['key']}", "version": 1,
                                       "signed_at": iso(self.svc.clock.now() - timedelta(minutes=1)),
                                       "body": n["text"], "author_employee_id": "SIM"}, fixture=True)
        with self.db.tx() as c:
            c.execute("UPDATE training_sessions SET event_index=? WHERE id=?", (k, t["id"]))
        return {"ok": True, "event_index": k, "events_total": t["events_total"]}

    def finish(self, user):
        t = self._session_for_shadow(user)
        if not t["finished_at"]:
            score = self.score(t)
            now = iso(self.svc.clock.now())
            with self.db.tx() as c:
                c.execute("UPDATE training_sessions SET finished_at=?, score_json=? WHERE id=?", (now, json.dumps(score), t["id"]))
                c.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
                c.execute("UPDATE sessions SET last_seen=? WHERE token=?", (__import__("time").time(), t["parent_token"]))
                audit(c, now, t["home_org"], t["user_id"], "training_finished", "training_session", t["id"])
        return {"ok": True, "session_id": t["id"], "parent_token": t["parent_token"]}

    def leave(self, user):
        t = self._session_for_shadow(user)
        with self.db.tx() as c:
            c.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        return {"ok": True, "parent_token": t["parent_token"]}

    # ------------------------------------------------------------------ scoring
    def score(self, t):
        org, shadow = t["sandbox_org"], t["shadow_user"]
        sc = self.db.one("SELECT * FROM scenarios WHERE id=?", (t["scenario_id"],))
        spec = json.loads(sc["spec_json"])
        role = sc["role"]
        items = []

        def add(area, patient, points, maximum, outcome, feedback):
            items.append({"area": area, "patient": patient, "points": points, "max": maximum, "outcome": outcome, "feedback": feedback})

        label = {p["key"]: p["label"] for p in spec["patients"]}
        delivered = [n for n in spec["notes"] if 1 <= n["event"] <= t["event_index"]]
        review_minutes = []
        for n in delivered:
            nid = f"{org}:{n['key']}"
            item = self.db.one("SELECT * FROM review_items WHERE note_id=? ORDER BY note_version DESC LIMIT 1", (nid,))
            reviewed = item and item["state"] == "reviewed"
            if reviewed and item["reviewed_at"] and item["received_at"]:
                from .clock import parse
                review_minutes.append((parse(item["reviewed_at"]) - parse(item["received_at"])).total_seconds() / 60)
            add("Note review", label[n["patient"]], 1 if reviewed else 0, 1, "correct" if reviewed else "missed",
                "Reviewed the new note." if reviewed else "This note was never reviewed. Every new note needs a person to read it.")
            if n.get("ai_trap"):
                trap = n["ai_trap"]
                if not item:
                    continue
                findings = self.db.q("SELECT finding_json FROM findings WHERE review_item_id=?", (item["id"],))
                if not findings:
                    add("Catching an AI mistake", label[n["patient"]], 0, 0, "not_scored",
                        "The AI didn't run on this note in this session, so there was no AI mistake to catch.")
                    continue
                dec = self.db.one("SELECT * FROM decisions WHERE review_item_id=? ORDER BY decided_at DESC LIMIT 1", (item["id"],))
                ok = False
                if dec and dec["decision"] == "reject":
                    ok = True
                elif dec and dec["decision"] == "correct":
                    corr = json.loads(dec["corrections_json"] or "{}")
                    ok = any(f.get(trap["field"]) == trap["correct"] for f in corr.values())
                add("Catching an AI mistake", label[n["patient"]], 2 if ok else 0, 2, "correct" if ok else "missed",
                    (f"You caught it: the note's {trap['field'].replace('_', ' ')} is “{trap['correct']}”, not what the AI said."
                     if ok else f"The AI got the {trap['field'].replace('_', ' ')} wrong (the note says “{trap['correct']}”). "
                     "Always read the full sentence in the original note before accepting."))
        for p in spec["patients"]:
            ans, eid = p["answer"], f"{org}_{p['key']}"
            pending = [n for n in spec["notes"] if n["patient"] == p["key"] and n["event"] > t["event_index"]]
            if pending and any(v is not None for v in ans.values()):
                add("Not reached", p["label"], 0, 0, "not_scored",
                    "You finished before this patient's later notes arrived, so their decisions weren't scored.")
                continue
            flags = self.db.q("SELECT * FROM emergency_flags WHERE encounter_id=?", (eid,))
            events = self.db.q("SELECT e.* FROM emergency_events e JOIN emergency_flags f ON f.id=e.flag_id "
                               "WHERE f.encounter_id=? AND e.user_id=?", (eid, shadow))
            calls = [e["decision"] for e in events if e["action"] in ("nurse_assessment", "charge_determination")]
            manual = any(e["action"] == "raised" and e["decision"] == "nurse" for e in events)
            reasons = [e["reason"] for e in events if e["reason"]]
            if ans["emergency"] is True:
                yes = any(c in ("emergency", "confirmed_emergency") for c in calls) or manual
                add("Emergency decision", p["label"], 2 if yes else 0, 2, "correct" if yes else "missed",
                    "You recognized the emergency." if yes else
                    ("A possible emergency was flagged but you didn't confirm it." if flags else "This patient had an emergency that wasn't flagged or checked."))
                good = any(len(r) >= 15 for r in reasons)
                add("Clear reason", p["label"], 1 if good else 0, 1, "correct" if good else "missed",
                    "Your reason explains what you saw." if good else "Write what you saw or heard (at least a full sentence) so the next person understands.")
            elif ans["emergency"] is False:
                over = any(c in ("emergency", "confirmed_emergency") for c in calls) or manual
                if over:
                    add("Emergency decision", p["label"], 0, 2, "missed",
                        "This wasn't an emergency. Check who the sign belongs to (patient vs. family) and when it happened.")
                elif flags and not calls:
                    add("Emergency decision", p["label"], 1, 2, "partial", "A flag was raised but you never recorded your check.")
                else:
                    add("Emergency decision", p["label"], 2, 2, "correct", "Correct: not an emergency.")
            if ans["max_acuity"] is not None and role in ("charge_rn", "provider"):
                pr = self.db.one("SELECT priority FROM encounters WHERE id=?", (eid,))["priority"]
                ok = pr is not None and pr <= ans["max_acuity"]
                add("Acuity", p["label"], 2 if ok else 0, 2, "correct" if ok else "missed",
                    f"Acuity set to Level {pr}." if ok else f"Expected Level {ans['max_acuity']} or more urgent; it ended at "
                    f"{'unassessed' if pr is None else 'Level ' + str(pr)}.")
            if ans["escalate"] is True:
                esc = self.db.one("SELECT 1 FROM escalations WHERE encounter_id=? AND raised_by=?", (eid, shadow))
                ok = bool(esc) or manual or any(c in ("emergency", "confirmed_emergency") for c in calls)
                add("Escalation", p["label"], 1 if ok else 0, 1, "correct" if ok else "missed",
                    "You escalated." if ok else "This patient needed escalation to the charge nurse.")
            if p["restricted"]:
                glass = self.db.q("SELECT reason FROM chart_access WHERE encounter_id=? AND user_id=? AND break_glass=1", (eid, shadow))
                if glass:
                    add("Restricted record", p["label"], 1, 1, "correct", f"You gave a reason to open a restricted record: “{glass[0]['reason']}”.")
        points = sum(i["points"] for i in items)
        possible = sum(i["max"] for i in items)
        return {"points": points, "possible": possible, "percent": round(100 * points / possible) if possible else 0,
                "items": items, "events_completed": t["event_index"], "events_total": t["events_total"],
                "avg_review_minutes": round(sum(review_minutes) / len(review_minutes), 1) if review_minutes else None}

    # ------------------------------------------------------------------ results
    def sessions(self, user):
        self._learner(user)
        if user["role"] == "instructor":
            rows = self.db.q("SELECT t.*, u.name, u.title, s.title AS sc_title FROM training_sessions t JOIN users u ON u.id=t.user_id "
                             "LEFT JOIN scenarios s ON s.id=t.scenario_id WHERE t.home_org=? ORDER BY t.started_at DESC LIMIT 300", (user["org"],))
        else:
            rows = self.db.q("SELECT t.*, u.name, u.title, s.title AS sc_title FROM training_sessions t JOIN users u ON u.id=t.user_id "
                             "LEFT JOIN scenarios s ON s.id=t.scenario_id WHERE t.user_id=? ORDER BY t.started_at DESC", (user["id"],))
        out = []
        for r in rows:
            sc = json.loads(r["score_json"]) if r["score_json"] else None
            out.append({"id": r["id"], "student": r["name"], "student_title": r["title"], "scenario": r["sc_title"] or "(deleted)",
                        "started_at": r["started_at"], "finished_at": r["finished_at"],
                        "percent": sc["percent"] if sc else None, "points": sc["points"] if sc else None,
                        "possible": sc["possible"] if sc else None})
        return out

    def session_result(self, user, tid):
        self._learner(user)
        t = self.db.one("SELECT * FROM training_sessions WHERE id=?", (tid,))
        if not t or not (t["user_id"] == user["id"] or (user["role"] == "instructor" and t["home_org"] == user["org"])):
            raise NotFound()
        sc = self.db.one("SELECT title, goals FROM scenarios WHERE id=?", (t["scenario_id"],))
        student = self.db.one("SELECT name, title FROM users WHERE id=?", (t["user_id"],))
        reasons = self.db.q("SELECT e.at, e.action, e.decision, e.reason, enc.label FROM emergency_events e "
                            "JOIN emergency_flags f ON f.id=e.flag_id JOIN encounters enc ON enc.id=f.encounter_id "
                            "WHERE f.org=? AND e.user_id=? ORDER BY e.seq", (t["sandbox_org"], t["shadow_user"]))
        decisions = self.db.q("SELECT d.decided_at, d.decision, d.corrections_json, d.comment, enc.label FROM decisions d "
                              "JOIN encounters enc ON enc.id=d.encounter_id WHERE enc.org=? AND d.user_id=? ORDER BY d.decided_at",
                              (t["sandbox_org"], t["shadow_user"]))
        return {"id": t["id"], "scenario": sc["title"] if sc else "(deleted)", "goals": sc["goals"] if sc else "",
                "student": f"{student['name']}, {student['title']}", "started_at": t["started_at"], "finished_at": t["finished_at"],
                "score": json.loads(t["score_json"]) if t["score_json"] else None,
                "emergency_reasons": [dict(r) for r in reasons],
                "decisions": [{"at": d["decided_at"], "patient": d["label"], "decision": d["decision"],
                               "corrected": sorted({k for v in json.loads(d["corrections_json"] or "{}").values() for k in v}),
                               "comment": d["comment"]} for d in decisions]}
