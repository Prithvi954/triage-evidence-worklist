"""Canary tests: which synthetic canaries reach which destinations, including the failed-request path."""
import json
import unittest

from tests.helpers import (CANARY_HEADER, CANARY_ORG2, CANARY_OTHER_PATIENT, CANARY_PASSAGE, make_app, read, user)

ALL = (CANARY_HEADER, CANARY_OTHER_PATIENT, CANARY_PASSAGE, CANARY_ORG2)


class TestCanaries(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.svc = self.app.svc
        self.riley, self.casey = user(self.app, "u_riley"), user(self.app, "u_casey")
        self.item = self.svc.deliver_fixture("org_n", "note_B2", 1)["review_item_id"]
        self.svc.run_jobs_once()
        self.app.dispatch_outbox()

    def destinations(self):
        a = self.app
        return {
            "outbound_request": json.dumps(a.adapter.sent_requests),
            "app_log": read(a.settings.log_path),
            "error_reports": read(a.settings.error_report_path),
            "audit_events": json.dumps(a.store.q("SELECT * FROM audit")),
            "outbox_events": json.dumps(a.store.q("SELECT * FROM outbox")),
            "browser_state_org_n": json.dumps(self.svc.state(self.riley)),
            "browser_item_detail_org_n": json.dumps(self.svc.review_item_detail(self.riley, self.item)),
            "browser_state_org_s": json.dumps(self.svc.state(self.casey)),
        }

    def test_canary_matrix(self):
        expected = {   # destination -> canaries that SHOULD be present (everything else must be absent)
            # The passage canary sits in a neighbour sentence that IS selected; the identifier scrubber
            # removes it before sending (it adds no clinical meaning). Reviewers still see it.
            "outbound_request": set(),
            "app_log": set(), "error_reports": set(), "audit_events": set(), "outbox_events": set(),
            "browser_state_org_n": set(),                              # queue/inbox views carry no note text
            "browser_item_detail_org_n": {CANARY_PASSAGE},             # authorized reviewer sees the note
            "browser_state_org_s": set(),
        }
        found = {d: {c for c in ALL if c in text} for d, text in self.destinations().items()}
        self.matrix = found
        for d, exp in expected.items():
            self.assertEqual(found[d], exp, d)

    def test_failed_request_path_does_not_leak(self):
        # Simulate a provider error whose message echoes the whole request (a realistic leak vector).
        def boom(req, key, subst):
            raise RuntimeError("provider echoed request: " + json.dumps(req))
        self.app.adapter.extract = boom
        self.svc.deliver_fixture("org_n", "note_C2", 1)
        self.svc.run_jobs_once()
        errs, log = read(self.app.settings.error_report_path), read(self.app.settings.log_path)
        self.assertIn("RuntimeError", errs)
        for c in ALL + ("Demo signal A resolved last week",):
            self.assertNotIn(c, errs)
            self.assertNotIn(c, log)
        it = self.app.store.one("SELECT ai_state FROM review_items WHERE note_id='note_C2'")
        self.assertEqual(it["ai_state"], "retrying")

    def test_other_patient_canary_never_in_b_request(self):
        for req in self.app.adapter.sent_requests:
            body = json.dumps(req)
            self.assertNotIn(CANARY_OTHER_PATIENT, body)
            self.assertNotIn(CANARY_HEADER, body)
            for field in ("Synthetic Person B", "2 Fictional Way", "000-000-0002", "EMP-SYN-118", "2026-11-14"):
                self.assertNotIn(field, body)

    def test_announcements_are_generic(self):
        from app.server import ANNOUNCE
        for r in self.app.store.q("SELECT * FROM outbox"):
            msg = ANNOUNCE.get(r["type"], lambda p: None)(json.loads(r["payload_json"])) or ""
            for bad in ALL + ("Patient B", "demo signal"):
                self.assertNotIn(bad, msg)


if __name__ == "__main__":
    unittest.main()
