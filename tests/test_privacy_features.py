"""Privacy features: encryption at rest, access log, break-the-glass, retention, dashboard, AI disclosure."""
import os
import unittest

from app import crypto
from app.server import check_deployment
from app.services import BreakGlassRequired
from tests.helpers import make_app, user


class TestEncryptionAtRest(unittest.TestCase):
    def setUp(self):
        from cryptography.fernet import Fernet
        os.environ["TRIAGE_DATA_KEY"] = Fernet.generate_key().decode()
        crypto.reset_for_tests()
        self.app = make_app()

    def tearDown(self):
        os.environ.pop("TRIAGE_DATA_KEY", None)
        crypto.reset_for_tests()

    def test_note_text_and_ai_payloads_are_encrypted_on_disk(self):
        svc = self.app.svc
        item = svc.deliver_fixture("org_n", "note_B2", 1)["review_item_id"]
        svc.run_jobs_once()
        svc.add_chart_note(user(self.app, "u_riley"), "enc_A", "Patient resting comfortably.")
        raw = self.app.store.conn
        bodies = [r[0] for r in raw.execute("SELECT body FROM notes")] + [r[0] for r in raw.execute("SELECT body FROM chart_notes")]
        self.assertTrue(bodies and all(b.startswith(crypto.PREFIX) for b in bodies))
        req, resp = raw.execute("SELECT request_json, response_json FROM model_calls").fetchone()
        self.assertTrue(req.startswith(crypto.PREFIX) and resp.startswith(crypto.PREFIX))
        self.assertNotIn("demo signal", open(self.app.settings.db_path, "rb").read().decode("latin-1"))
        # ...but authorized views decrypt normally
        d = svc.review_item_detail(user(self.app, "u_riley"), item)
        self.assertIn("now reports demo signal A", d["note"]["body"])
        self.assertTrue(svc.privacy_summary(user(self.app, "u_riley"))["encryption_at_rest"])


class TestAccessAndBreakGlass(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.svc = self.app.svc
        self.riley, self.sam = user(self.app, "u_riley"), user(self.app, "u_sam")

    def test_every_chart_open_is_logged(self):
        self.svc.encounter_detail(self.riley, "enc_A")
        log = self.svc.access_log(self.sam, "enc_A")
        self.assertEqual((log[0]["action"], log[0]["break_glass"]), ("view chart", False))
        self.assertIn("Riley", log[0]["by"])

    def test_restricted_record_needs_reason_unless_on_care_team(self):
        with self.app.store.tx() as c:
            c.execute("UPDATE encounters SET restricted=1 WHERE id='enc_A'")   # Riley is Patient A's nurse
        self.svc.encounter_detail(self.riley, "enc_A")                       # care team: no reason needed
        with self.assertRaises(BreakGlassRequired):
            self.svc.encounter_detail(self.sam, "enc_A")
        with self.assertRaises(BreakGlassRequired):
            self.svc.encounter_detail(self.sam, "enc_A", reason="x")         # too short
        self.svc.encounter_detail(self.sam, "enc_A", reason="Charge nurse covering a code")
        log = self.svc.access_log(self.riley, "enc_A")
        self.assertTrue(log[0]["break_glass"])
        self.assertEqual(log[0]["reason"], "Charge nurse covering a code")
        summary = self.svc.privacy_summary(self.sam)
        self.assertEqual(summary["break_glass_count"], 1)
        self.assertEqual(summary["break_glass_recent"][0]["patient"], "Patient A")
        self.assertTrue(self.app.store.one("SELECT 1 FROM outbox WHERE type='break_glass'"))


class TestRetentionAndDashboard(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.svc = self.app.svc
        self.riley = user(self.app, "u_riley")

    def test_dashboard_reports_zero_identifiers_sent(self):
        self.svc.deliver_fixture("org_n", "note_B2", 1)
        self.svc.run_jobs_once()
        s = self.svc.privacy_summary(self.riley)
        self.assertEqual(s["ai_requests"], 1)
        self.assertEqual(s["identifiers_found_in_ai_requests"], [])
        self.assertIn("mrn", s["identifier_types_checked"])
        self.assertGreaterEqual(s["scrubbed_tokens"], 1)          # the wristband code
        self.assertEqual(s["typed_or_dictated_sent_to_ai"], 0)

    def test_dashboard_would_catch_a_leak(self):
        self.svc.deliver_fixture("org_n", "note_B2", 1)
        self.svc.run_jobs_once()
        with self.app.store.tx() as c:   # simulate a bug that sent the patient's MRN
            c.execute("UPDATE model_calls SET request_json = request_json || ' CANARY-HDR-7Q2X'")
        self.assertEqual(self.svc.privacy_summary(self.riley)["identifiers_found_in_ai_requests"], ["mrn"])

    def test_ai_text_purged_after_retention_but_decisions_kept(self):
        item = self.svc.deliver_fixture("org_n", "note_B2", 1)["review_item_id"]
        self.svc.run_jobs_once()
        self.svc.decide(self.riley, item, "accept", disclosed=True)
        self.app.clock.advance(minutes=60 * 24 * 8)
        self.svc.tick()
        row = self.app.store.one("SELECT request_json, response_json, outcome FROM model_calls")
        self.assertEqual((row["request_json"], row["response_json"], row["outcome"]), (None, None, "findings"))
        d = self.svc.review_item_detail(self.riley, item)
        self.assertTrue(d["findings"])                    # evidence the nurse reviewed is kept
        self.assertEqual(d["decisions"][0]["decision"], "accept")
        self.assertTrue(d["ai_disclosed"])
        self.assertEqual(self.svc.privacy_summary(self.riley)["ai_requests_purged"], 1)


class TestClinicalModeGuard(unittest.TestCase):
    def test_clinical_mode_refuses_unsafe_settings(self):
        s = make_app().settings
        s.deployment = "clinical"
        problems = check_deployment(s)
        self.assertTrue(any("TRIAGE_DATA_KEY" in p for p in problems))
        self.assertTrue(any("DEMO_LOGIN" in p for p in problems))
        self.assertFalse(s.ai_may_send_real_notes())


if __name__ == "__main__":
    unittest.main()
