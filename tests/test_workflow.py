"""Integration tests on the real service + SQLite with a controllable clock."""
import json
import sqlite3
import unittest

from app.adapter import BoundaryViolation, build_request
from app.services import Conflict, Forbidden, NotFound
from tests.helpers import make_app, user


def official(app, uid="u_riley"):
    return [(q["label"], q["priority"]) for q in app.svc.state(user(app, uid))["queue"]]


def deadlines(app):
    return {r["id"]: r["due_at"] for r in app.store.q("SELECT id, due_at FROM obligations")}


class Base(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.svc = self.app.svc
        self.riley, self.sam, self.casey = user(self.app, "u_riley"), user(self.app, "u_sam"), user(self.app, "u_casey")

    def deliver(self, nid, v=1):
        r = self.svc.deliver_fixture("org_n", nid, v)
        return r.get("review_item_id")


class TestOrdering(Base):
    def test_four_patients_deterministic_order_and_tiebreak(self):
        q = self.svc.state(self.riley)["queue"]
        self.assertEqual([x["label"] for x in q], ["Patient D", "Patient A", "Patient C", "Patient B"])
        # C and B share Level 3; C sorts first because its open deadline (overdue reassessment) is earlier.
        c = next(x for x in q if x["label"] == "Patient C")
        self.assertTrue(c["obligations"][0]["overdue"])
        self.assertIsNone(q[0]["priority"])   # D unassessed

    def test_inbox_is_fifo_regardless_of_ai_output(self):
        i1 = self.deliver("note_C3")
        self.app.clock.advance(minutes=1)
        i2 = self.deliver("note_B2")
        self.svc.run_jobs_once()
        self.assertEqual([i["item_id"] for i in self.svc.state(self.riley)["inbox"]], [i1, i2])


class TestModelHasNoAuthority(Base):
    def test_valid_malformed_and_malicious_outputs_never_change_order(self):
        before, dl = official(self.app), deadlines(self.app)
        for fault in ("none", "malformed", "no_finding"):
            self.app.adapter.fault = fault
            for nid in ("note_B2", "note_A2"):
                self.app.store.conn.execute("DELETE FROM notes WHERE note_id=?", (nid,))
                self.app.store.conn.execute("DELETE FROM review_items WHERE note_id=?", (nid,))
                self.app.store.conn.execute("DELETE FROM jobs WHERE note_id=?", (nid,))
                self.deliver(nid)
            self.svc.run_jobs_once()
            self.assertEqual(official(self.app), before, fault)
        # injection note specifically
        st = self.svc.state(self.riley)
        a2 = next(i for i in st["inbox"] if i["label"] == "Patient A")
        self.assertEqual(official(self.app), before)
        for k, v in dl.items():
            self.assertEqual(deadlines(self.app)[k], v)
        # No tools were offered, so nothing in the response could be "invoked".
        for req in self.app.adapter.sent_requests:
            self.assertNotIn("tools", req)
            self.assertEqual(set(req), {"model", "max_tokens", "system", "messages", "output_config"})
        self.assertTrue(a2)

    def test_only_authorized_clinician_changes_priority(self):
        enc = self.app.store.one("SELECT version FROM encounters WHERE id='enc_B'")
        with self.assertRaises(Forbidden):
            self.svc.confirm_priority(self.riley, "enc_B", 1, "clinical_judgement", enc["version"])
        self.svc.confirm_priority(self.sam, "enc_B", 1, "new_evidence_confirmed", enc["version"])
        self.assertEqual(official(self.app)[0], ("Patient B", 1))
        # A's reassessment survives the reorder unchanged
        self.assertEqual(deadlines(self.app)["ob_A_re1"], "2026-11-14T10:30:00Z")

    def test_stale_downgrade_rejected(self):
        v = self.app.store.one("SELECT version FROM encounters WHERE id='enc_B'")["version"]
        self.svc.confirm_priority(self.sam, "enc_B", 1, "new_evidence_confirmed", v)
        with self.assertRaises(Conflict):
            self.svc.confirm_priority(self.sam, "enc_B", 3, "clinical_judgement", v)   # stale version
        self.assertEqual(self.app.store.one("SELECT priority FROM encounters WHERE id='enc_B'")["priority"], 1)


class TestFailures(Base):
    def test_amended_while_in_flight_old_response_not_current(self):
        item_v1 = self.deliver("note_B2", 1)

        def hook(point):
            if point == "after_call":
                self.svc.crash_hook = None
                self.deliver("note_B2", 2)          # amendment lands while v1 call is in flight
        self.svc.crash_hook = hook
        self.svc.run_jobs_once()
        v1 = self.app.store.one("SELECT * FROM review_items WHERE id=?", (item_v1,))
        self.assertEqual(v1["state"], "superseded")
        self.assertEqual(self.app.store.q("SELECT * FROM findings WHERE review_item_id=?", (item_v1,)), [])
        call = self.app.store.one("SELECT outcome FROM model_calls WHERE review_item_id=?", (item_v1,))
        self.assertEqual(call["outcome"], "stale_discarded")   # history preserved, not active evidence
        self.svc.run_jobs_once()
        inbox = self.svc.state(self.riley)["inbox"]
        self.assertEqual([(i["label"], i["note_version"]) for i in inbox], [("Patient B", 2)])
        self.assertEqual(self.app.store.one("SELECT COUNT(*) n FROM notes WHERE note_id='note_B2'")["n"], 2)

    def test_transferred_patient_result_discarded(self):
        self.deliver("note_B2")

        def hook(point):
            if point == "after_call":
                self.svc.crash_hook = None
                self.svc.sim_close("org_n", "enc_B", "transferred")
        self.svc.crash_hook = hook
        self.svc.run_jobs_once()
        self.assertEqual(self.app.store.one("SELECT outcome FROM model_calls")["outcome"], "stale_discarded")

    def test_provider_failure_preserves_care_and_manual_paths(self):
        dl = deadlines(self.app)
        before = official(self.app)
        for fault in ("timeout", "rate_limit", "outage"):
            self.app.store.conn.execute("DELETE FROM notes WHERE note_id='note_B2'")
            self.app.store.conn.execute("DELETE FROM review_items WHERE note_id='note_B2'")
            self.app.store.conn.execute("DELETE FROM jobs WHERE note_id='note_B2'")
            self.app.adapter.fault = fault
            item = self.deliver("note_B2")
            for _ in range(4):      # retries back off on the controllable clock, then fail visibly
                self.svc.run_jobs_once()
                self.app.clock.advance(minutes=2)
            it = self.app.store.one("SELECT ai_state, state FROM review_items WHERE id=?", (item,))
            self.assertTrue(it["ai_state"].startswith("failed_"), it)
            self.assertEqual(it["state"], "unreviewed")                       # still in the inbox
            self.assertGreaterEqual(self.svc.state(self.riley)["ai_needs_manual"], 1)
        self.assertEqual(official(self.app), before)
        for k, v in dl.items():
            self.assertEqual(deadlines(self.app)[k], v)
        self.svc.escalate(self.riley, "enc_B", "concern_deterioration")      # manual path works during outage
        self.svc.decide(self.riley, item, "reviewed_no_ai", {}, "read note manually")

    def test_crash_after_priority_commit_before_notification(self):
        v = self.app.store.one("SELECT version FROM encounters WHERE id='enc_B'")["version"]
        self.svc.confirm_priority(self.sam, "enc_B", 1, "new_evidence_confirmed", v)
        pending = self.app.store.q("SELECT * FROM outbox WHERE delivered_at IS NULL AND type='queue_changed'")
        self.assertEqual(len(pending), 1)      # durable: survives a crash before dispatch
        # "restart": a new process opening the same DB delivers it
        from app.clock import Clock
        from app.server import App
        app2 = App(settings=self.app.settings, clock=Clock())
        q, ev = app2.broker.subscribe("org_n", "u_riley")
        app2.dispatch_outbox()
        self.assertTrue(any(r["type"] == "queue_changed" for r in q))
        self.assertEqual(app2.svc.state(self.riley)["queue"][0]["label"], "Patient B")

    def test_two_clinicians_accept_same_task(self):
        self.svc.obligation_action(self.riley, "ob_D_init", "accept")
        with self.assertRaises(Conflict):
            self.svc.obligation_action(self.sam, "ob_D_init", "accept")

    def test_accepted_but_never_reached_escalates_to_backup_with_original_deadline(self):
        v = self.app.store.one("SELECT version FROM encounters WHERE id='enc_B'")["version"]
        self.svc.confirm_priority(self.sam, "enc_B", 1, "new_evidence_confirmed", v)
        ob = self.app.store.one("SELECT * FROM obligations WHERE kind='bedside_review'")
        self.svc.obligation_action(self.riley, ob["id"], "accept")
        self.app.clock.advance(minutes=11)
        self.svc.tick(); self.svc.tick()       # idempotent
        ob2 = self.app.store.one("SELECT * FROM obligations WHERE id=?", (ob["id"],))
        self.assertEqual(ob2["due_at"], ob["due_at"])
        self.assertEqual(ob2["escalated_to"], "u_sam")
        self.assertIsNone(ob2["reached_at"])
        overdue = self.svc.state(self.sam)["overdue"]
        row = next(o for o in overdue if o["id"] == ob["id"])
        self.assertEqual(row["label"], "Patient B")
        self.assertIn("Riley", row["accepted_by"])
        self.assertEqual(self.app.store.one("SELECT COUNT(*) n FROM outbox WHERE type='obligation_overdue' AND "
                                            "payload_json LIKE ?", (f'%{ob["id"]}%',))["n"], 1)

    def test_deadline_cannot_be_reset(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.app.store.tx() as c:
                c.execute("UPDATE obligations SET due_at='2026-11-14T23:00:00Z' WHERE id='ob_C_re1'")

    def test_duplicate_ehr_event_idempotent(self):
        self.deliver("note_B2")
        dl = deadlines(self.app)
        r = self.svc.deliver_fixture("org_n", "note_B2", 1)
        self.assertTrue(r["duplicate"])
        self.assertEqual(deadlines(self.app), dl)
        self.assertEqual(self.app.store.one("SELECT COUNT(*) n FROM review_items WHERE note_id='note_B2'")["n"], 1)

    def test_feed_stall_detected(self):
        self.app.clock.advance(minutes=16)
        self.svc.tick()
        self.assertTrue(self.svc.state(self.riley)["feed"]["stale"])

    def test_clinician_rejects_and_item_not_reopened(self):
        item = self.deliver("note_A3")
        self.svc.run_jobs_once()
        d = self.svc.review_item_detail(self.riley, item)
        fid = d["findings"][0]["id"]
        self.svc.decide(self.riley, item, "correct", {fid: {"assertion": "denied"}}, "denied at rest", d["version"])
        self.svc.run_jobs_once()
        self.assertNotIn(item, [i["item_id"] for i in self.svc.state(self.riley)["inbox"]])
        with self.assertRaises(Conflict):
            self.svc.decide(self.sam, item, "accept")
        dd = self.svc.review_item_detail(self.riley, item)
        self.assertEqual(dd["decisions"][0]["corrections"], {fid: {"assertion": "denied"}})
        self.assertIn("The full sentence contains", dd["findings"][0]["checks"]["warnings"][0])  # source + history kept


class TestBoundary(Base):
    def test_modified_fixture_never_sent(self):
        n = next(x for x in self.app.seed["deliverable_notes"] if x["note_id"] == "note_B2")
        enc = self.app.store.one("SELECT * FROM encounters WHERE id='enc_B'")
        tampered = dict(n, body=n["body"].replace("waiting area", "waiting area. Real patient detail"))
        self.svc.ingest_note(enc, tampered, fixture=True)
        self.svc.run_jobs_once()
        self.assertEqual(self.app.adapter.sent_requests, [])
        self.assertEqual(self.app.store.one("SELECT ai_state FROM review_items WHERE note_id='note_B2'")["ai_state"],
                         "blocked_not_approved_fixture")

    def test_unapproved_outbound_feature_rejected(self):
        req = build_request([{"alias": "SRC-1", "role": "current_note_excerpt", "signed": "today", "text": "x"}],
                            ["demo signal A"], self.app.settings)
        for extra in ({"tools": [{"type": "web_fetch_20260209", "name": "web_fetch"}]},
                      {"mcp_servers": [{"type": "url", "url": "https://x.invalid", "name": "x"}]},
                      {"thread": {"id": "t"}}, {"fallbacks": "default"}):
            with self.assertRaises(BoundaryViolation):
                self.app.adapter.check_features(dict(req, **extra))

    def test_cross_org_access_denied_everywhere(self):
        item = self.deliver("note_B2")
        self.svc.run_jobs_once()
        for fn in (lambda: self.svc.review_item_detail(self.casey, item),
                   lambda: self.svc.ai_request_view(self.casey, item),
                   lambda: self.svc.encounter_detail(self.casey, "enc_B"),
                   lambda: self.svc.decide(self.casey, item, "accept"),
                   lambda: self.svc.confirm_priority(self.casey, "enc_B", 1, "x", 1),
                   lambda: self.svc.escalate(self.casey, "enc_B", "concern_deterioration"),
                   lambda: self.svc.obligation_action(self.casey, "ob_A_re1", "accept"),
                   lambda: self.svc.encounter_detail(self.riley, "enc_S"),
                   lambda: self.svc.encounter_detail(self.riley, "enc_DOES_NOT_EXIST")):
            with self.assertRaises((NotFound, Forbidden)):
                fn()
        with self.assertRaises(NotFound):
            self.svc.deliver_fixture("org_s", "note_B2", 1)

    def test_revocation_ends_session(self):
        tok, _ = self.svc.login("u_riley")
        self.assertIsNotNone(self.svc.user_for_token(tok))
        self.svc.sim_revoke("u_riley")
        self.assertIsNone(self.svc.user_for_token(tok))

    def test_versions_recorded_on_every_call(self):
        self.deliver("note_B2")
        self.svc.run_jobs_once()
        c = self.app.store.one("SELECT * FROM model_calls")
        for k in ("model", "prompt_version", "schema_version", "selector_version", "rules_version", "mode", "provenance"):
            self.assertTrue(c[k], k)
        self.assertEqual(c["provenance"], "replay:authored_fixture")   # replay never reported as a live call


if __name__ == "__main__":
    unittest.main()
