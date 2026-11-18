"""Public sign-up with an emailed activation code; training users; adding practice patients."""
import re
import unittest

from app import mailer
from app.services import BadInput, Conflict, Forbidden
from tests.helpers import make_app, user


class TestSignup(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.svc = self.app.svc
        self.svc.signup_throttle.fails.clear()
        mailer.sent.clear()

    def code(self):
        return re.search(r"\b(\d{6})\b", mailer.sent[-1][2]).group(1)

    def signup(self, email="Pat.Learner@Example.edu"):
        return self.svc.signup(email, "Pat Learner", "nursing", "practice123", "1.2.3.4")

    def test_full_flow(self):
        r = self.signup()
        self.assertEqual(r["email"], "pat.learner@example.edu")
        self.assertEqual(mailer.sent[-1][0], "pat.learner@example.edu")
        with self.assertRaises(Forbidden) as cm:                     # can't sign in before activation
            self.svc.login_password("pat.learner@example.edu", "practice123")
        self.assertIn("activate", str(cm.exception))
        with self.assertRaises(BadInput):
            self.svc.verify_signup("pat.learner@example.edu", "000000" if self.code() != "000000" else "111111")
        tok, u = self.svc.verify_signup("pat.learner@example.edu", self.code())
        self.assertEqual((u["role"], u["title"], u["org"]), ("student", "Nursing student", "org_school"))
        self.assertIsNotNone(self.svc.user_for_token(tok))
        tok2, _ = self.svc.login_password("PAT.LEARNER@example.edu ", "practice123")
        self.assertTrue(tok2)
        with self.assertRaises(Conflict):
            self.signup()

    def test_validation(self):
        for args in (("bad-email", "Pat", "nursing", "practice123"), ("a@b.edu", "", "nursing", "practice123"),
                     ("a@b.edu", "Pat", "wizard", "practice123"), ("a@b.edu", "Pat", "nursing", "short")):
            with self.assertRaises(BadInput):
                self.svc.signup(*args, client="1.2.3.4")

    def test_code_attempt_limit_and_resend_cooldown(self):
        self.signup()
        good = self.code()
        wrong = "123456" if good != "123456" else "654321"
        for _ in range(5):
            with self.assertRaises(BadInput):
                self.svc.verify_signup("pat.learner@example.edu", wrong)
        with self.assertRaises(Forbidden):
            self.svc.verify_signup("pat.learner@example.edu", good)
        with self.assertRaises(Conflict):                           # resend too soon
            self.svc.resend_code("pat.learner@example.edu", "1.2.3.4")
        self.app.store.conn.execute("UPDATE signups SET last_sent_at=last_sent_at-120")
        self.svc.resend_code("pat.learner@example.edu", "1.2.3.4")
        self.svc.verify_signup("pat.learner@example.edu", self.code())   # new code works, attempts reset

    def test_expired_code(self):
        self.signup()
        self.app.store.conn.execute("UPDATE signups SET expires_at=0")
        with self.assertRaises(BadInput) as cm:
            self.svc.verify_signup("pat.learner@example.edu", self.code())
        self.assertIn("expired", str(cm.exception))

    def test_signup_throttle_per_network(self):
        for i in range(10):
            self.svc.signup(f"s{i}@example.edu", "Pat", "medical", "practice123", "9.9.9.9")
        with self.assertRaises(Forbidden):
            self.svc.signup("s99@example.edu", "Pat", "medical", "practice123", "9.9.9.9")

    def test_signup_closed(self):
        self.app.settings.signup_open = False
        with self.assertRaises(Forbidden):
            self.signup()


class TestPersonasAndAdding(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.svc, self.edu = self.app.svc, self.app.education
        self.taylor = user(self.app, "u_taylor")

    def start(self, persona):
        r = self.edu.start_persona(self.taylor, persona, "parent")
        return self.svc.user_for_token(r["token"])

    def test_practice_as_each_training_user(self):
        roles = {}
        for p in ("riley", "sam", "drlee"):
            me = self.start(p)
            roles[p] = me["role"]
            self.assertIn("Practicing as", me["title"])
            self.assertEqual(len(self.svc.state(me)["queue"]), 5)
        self.assertEqual(roles, {"riley": "rn", "sam": "charge_rn", "drlee": "provider"})

    def test_add_patient_and_emergency_note(self):
        me = self.start("riley")
        r = self.edu.add_patient(me, {"label": "Patient Z", "age": "61", "sex": "M", "complaint": "Demo concern Z",
                                      "note": "Nursing note. Patient reports demo red flag R right now."})
        st = self.svc.state(me)
        self.assertIn("Patient Z", [q["label"] for q in st["queue"]])
        self.assertIn("Patient Z", [f["label"] for f in self.svc.emergencies(me)["flags"]])
        self.edu.add_note(me, r["encounter_id"], "Nursing note. Patient reports demo signal A.")
        with self.assertRaises(BadInput):
            self.edu.add_patient(me, {"label": "John Smith"})
        with self.assertRaises(BadInput):
            self.edu.add_note(me, r["encounter_id"], "Mr. Smith, call 404-555-0101.")
        self.svc.run_jobs_once()
        self.assertEqual(self.app.adapter.sent_requests and
                         [q for q in self.app.adapter.sent_requests if "demo red flag R right now" in str(q)], [])
        # finishing still scores (added patients aren't scored)
        tid = self.edu.finish(me)["session_id"]
        self.assertIsNotNone(self.edu.session_result(self.taylor, tid)["score"])


if __name__ == "__main__":
    unittest.main()
