"""Possible-emergency flags, nurse checks, charge decisions, and nurse-written chart notes."""
import unittest

from app import emergency
from app.services import BadInput, Conflict, Forbidden, NotFound
from tests.helpers import make_app, user


class TestRules(unittest.TestCase):
    def test_rule_fires_only_on_affirmed_current_patient_statements(self):
        self.assertEqual([h["rule_id"] for h in emergency.evaluate("Patient reports demo red flag R now.")], ["ER-1"])
        for text in ("Patient denies demo red flag R.", "No demo red flag S.", "Patient's mother had demo red flag R.",
                     "Demo red flag S resolved last week."):
            self.assertEqual(emergency.evaluate(text), [], text)
        self.assertEqual([h["rule_id"] for h in emergency.evaluate("Patient reports demo signal A. Also reports demo signal B.")],
                         ["ER-3"])


class TestFlags(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.svc = self.app.svc
        self.riley, self.jordan, self.sam, self.casey = (user(self.app, u) for u in ("u_riley", "u_jordan", "u_sam", "u_casey"))

    def order(self):
        return [(q["label"], q["priority"]) for q in self.svc.state(self.riley)["queue"]]

    def flag(self):
        return self.svc.emergencies(self.riley)["flags"][0]

    def test_rule_flag_does_not_move_anyone_and_creates_tasks(self):
        before = self.order()
        self.svc.deliver_fixture("org_n", "note_D1", 1)
        f = self.flag()
        self.assertEqual((f["rule_id"], f["status"]), ("ER-1", "awaiting_nurse"))
        self.assertIn("demo red flag R", f["evidence"])
        self.assertEqual(self.order(), before)
        kinds = {o["kind"] for o in self.app.store.q("SELECT kind FROM obligations WHERE source_ref=?", (f["id"],))}
        self.assertEqual(kinds, {"emergency_check", "emergency_decision"})
        self.assertEqual(len([x for x in self.svc.emergencies(self.riley)["flags"] if x["rule_id"] == "ER-2"]), 0)  # denied S

    def test_nurse_check_requires_reason_and_any_nurse_can_check(self):
        self.svc.deliver_fixture("org_n", "note_D1", 1)
        f = self.flag()
        with self.assertRaises(BadInput):
            self.svc.assess_flag(self.riley, f["id"], "emergency", "  ")
        self.svc.assess_flag(self.jordan, f["id"], "not_emergency", "Patient calm, symptom stopped.", f["version"])
        f2 = self.flag()
        self.assertEqual(f2["status"], "awaiting_charge")
        self.assertEqual(f2["history"][-1]["reason"], "Patient calm, symptom stopped.")
        with self.assertRaises(Conflict):   # stale version
            self.svc.assess_flag(self.riley, f["id"], "emergency", "I disagree, looks worse.", f["version"])

    def test_only_charge_decides_and_confirmation_moves_to_level_1(self):
        self.svc.deliver_fixture("org_n", "note_D1", 1)
        f = self.flag()
        self.svc.assess_flag(self.riley, f["id"], "emergency", "Looks unwell at bedside.")
        with self.assertRaises(Forbidden):
            self.svc.determine_flag(self.riley, f["id"], "confirmed_emergency", "I am sure.")
        r = self.svc.determine_flag(self.sam, f["id"], "confirmed_emergency", "Agree after seeing patient.")
        self.assertTrue(r["moved_to_level_1"])
        self.assertEqual(self.order()[0], ("Patient D", 1))
        self.assertTrue(self.app.store.one("SELECT 1 FROM obligations WHERE encounter_id='enc_D' AND kind='bedside_review'"))
        # charge nurse can determine again; nurses can re-check, which re-opens it for the charge nurse
        self.svc.assess_flag(self.riley, f["id"], "not_emergency", "Symptom fully gone now.")
        self.assertEqual(self.flag()["status"], "awaiting_charge")
        self.svc.determine_flag(self.sam, f["id"], "not_emergency", "Resolved; keep Level 1 until bedside review is done.")
        f3 = self.flag()
        self.assertEqual(f3["status"], "not_emergency")
        self.assertEqual([h["action"] for h in f3["history"]],
                         ["raised", "nurse_assessment", "charge_determination", "nurse_assessment", "charge_determination"])
        self.assertEqual(self.order()[0], ("Patient D", 1))   # a later "not emergency" never silently downgrades

    def test_manual_flag_requires_reason_and_other_clinic_cannot_see(self):
        with self.assertRaises(BadInput):
            self.svc.raise_manual_flag(self.riley, "enc_A", "")
        fid = self.svc.raise_manual_flag(self.riley, "enc_A", "Patient says chest feels odd.")["flag_id"]
        self.assertEqual(self.svc.emergencies(self.casey)["flags"], [])
        with self.assertRaises(NotFound):
            self.svc.assess_flag(self.casey, fid, "emergency", "trying to see it")

    def test_chart_note_saved_never_sent_to_ai_and_rules_run(self):
        sent_before = len(self.app.adapter.sent_requests)
        r = self.svc.add_chart_note(self.riley, "enc_C", "Patient reports demo red flag S at rest. Charge nurse aware.",
                                    "nursing_note", "dictated")
        self.svc.run_jobs_once()
        self.assertEqual(r["emergency_flags_raised"], 1)
        self.assertEqual(len(self.app.adapter.sent_requests), sent_before)
        docs = self.svc.encounter_detail(self.riley, "enc_C")["documents"]
        self.assertEqual((docs[0]["input_method"], docs[0]["note_type"]), ("dictated", "nursing_note"))
        with self.assertRaises(NotFound):
            self.svc.add_chart_note(self.casey, "enc_C", "other clinic writing here")
        with self.assertRaises(BadInput):
            self.svc.add_chart_note(self.riley, "enc_C", "x" * 5000)


if __name__ == "__main__":
    unittest.main()
