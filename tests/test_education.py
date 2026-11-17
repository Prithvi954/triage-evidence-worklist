"""Education mode: scenario library, sandboxed sessions, scoring, case builder, instructor review."""
import unittest

from app.services import BadInput, BreakGlassRequired, Forbidden, NotFound
from tests.helpers import make_app, user

CASE = {
    "title": "Night shift surprise", "role": "rn", "synthetic_attested": True,
    "summary": "A made-up practice case.", "goals": "Spot the emergency.",
    "spec": {
        "patients": [{"key": "H", "label": "Patient H", "age": 40, "sex": "M", "complaint": "Demo concern Y",
                      "location": "Room 3", "priority": 3, "answer": {"emergency": True, "escalate": True}}],
        "notes": [{"key": "H0", "patient": "H", "event": 0, "text": "Triage note. Patient reports demo signal B."},
                  {"key": "H1", "patient": "H", "event": 1, "text": "Nursing note. Patient reports demo red flag R right now. Patient also reports demo signal A."}],
    },
}


def deep(d):
    import json
    return json.loads(json.dumps(d))


class Base(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.svc, self.edu = self.app.svc, self.app.education
        self.taylor, self.avery, self.morgan = (user(self.app, u) for u in ("u_taylor", "u_avery", "u_morgan"))

    def start(self, learner, sid):
        r = self.edu.start_session(learner, sid, "parent-token")
        shadow = self.svc.user_for_token(r["token"])
        return r["session_id"], shadow


class TestLibraryAndSandbox(Base):
    def test_builtin_library_for_learners_only(self):
        titles = [s["title"] for s in self.edu.list_scenarios(self.taylor)]
        self.assertIn("The changing story", titles)
        self.assertIn("Emergency or not?", titles)
        with self.assertRaises(Forbidden):
            self.edu.list_scenarios(user(self.app, "u_riley"))

    def test_session_is_isolated_from_real_unit(self):
        real_items = self.app.store.one("SELECT COUNT(*) n FROM review_items WHERE org='org_n'")["n"]
        tid, shadow = self.start(self.taylor, "sc_builtin_story")
        st = self.svc.state(shadow)
        self.assertEqual(len(st["queue"]), 4)
        self.assertEqual(st["training"]["event_index"], 1)
        self.assertTrue(shadow["org"].startswith("trn_"))
        self.svc.run_jobs_once()
        item = st["inbox"][0]["item_id"] if st["inbox"] else self.svc.state(shadow)["inbox"][0]["item_id"]
        d = self.svc.review_item_detail(shadow, item)
        self.assertEqual(d["ai_state"], "findings")                      # AI ran (replayed) on approved scenario text
        self.assertEqual(self.app.store.one("SELECT COUNT(*) n FROM review_items WHERE org='org_n'")["n"], real_items)
        with self.assertRaises(NotFound):                                # sandbox user can't see the real unit
            self.svc.encounter_detail(shadow, "enc_A")
        with self.assertRaises(NotFound):                                # real nurses can't see the sandbox
            self.svc.encounter_detail(user(self.app, "u_riley"), f"{shadow['org']}_A")


class TestScoring(Base):
    def play_story(self, catch_mistake):
        tid, me = self.start(self.taylor, "sc_builtin_story")
        org = me["org"]

        def review_all(decision_for):
            self.svc.run_jobs_once()
            for it in self.svc.state(me)["inbox"]:
                d = self.svc.review_item_detail(me, it["item_id"])
                decision, corr = decision_for(d)
                self.svc.decide(me, it["item_id"], decision, corr, "", d["version"])

        def a3_decider(d):
            if d["note"]["note_id"].endswith(":A3") and catch_mistake:
                fid = d["findings"][0]["id"]
                return "correct", {fid: {"assertion": "denied"}}
            return "accept", {}

        review_all(a3_decider)                                           # event 1: B2
        v = self.app.store.one("SELECT version FROM encounters WHERE id=?", (f"{org}_B",))["version"]
        self.svc.confirm_priority(me, f"{org}_B", 2, "new_evidence_confirmed", v)
        for _ in range(3):                                               # events 2-4: A3 (trap), C2, D1 (emergency)
            self.edu.next_event(me)
            review_all(a3_decider)
        flag = self.svc.emergencies(me)["flags"][0]
        self.svc.assess_flag(me, flag["id"], "emergency", "Patient reports the warning sign right now and looks unwell.")
        self.svc.determine_flag(me, flag["id"], "confirmed_emergency", "Confirmed at bedside, moving to Level 1.")
        r = self.edu.finish(me)
        self.assertEqual(r["parent_token"], "parent-token")
        self.assertIsNone(self.svc.user_for_token("x"))
        return self.edu.session_result(self.taylor, tid)

    def test_perfect_run_scores_100(self):
        res = self.play_story(catch_mistake=True)
        sc = res["score"]
        self.assertEqual((sc["points"], sc["possible"], sc["percent"]), (sc["possible"], sc["possible"], 100))
        areas = {i["area"] for i in sc["items"]}
        self.assertTrue({"Note review", "Catching an AI mistake", "Emergency decision", "Clear reason", "Acuity"} <= areas)
        self.assertTrue(res["emergency_reasons"])

    def test_accepting_the_ai_mistake_loses_points_with_feedback(self):
        sc = self.play_story(catch_mistake=False)["score"]
        trap = next(i for i in sc["items"] if i["area"] == "Catching an AI mistake")
        self.assertEqual((trap["points"], trap["outcome"]), (0, "missed"))
        self.assertIn("full sentence", trap["feedback"])
        self.assertLess(sc["percent"], 100)

    def test_results_visibility(self):
        res = self.play_story(catch_mistake=True)
        self.assertEqual(self.edu.session_result(self.morgan, res["id"])["id"], res["id"])   # instructor sees class
        with self.assertRaises(NotFound):
            self.edu.session_result(self.avery, res["id"])                                  # classmate doesn't
        self.assertEqual(self.edu.sessions(self.morgan)[0]["percent"], 100)


class TestEmergencyScenario(Base):
    def test_restricted_record_and_over_calling(self):
        tid, me = self.start(self.avery, "sc_builtin_emergency")
        org = me["org"]
        with self.assertRaises(BreakGlassRequired):
            self.svc.encounter_detail(me, f"{org}_G")
        self.svc.encounter_detail(me, f"{org}_G", reason="Checking on a patient in my section")
        flags = self.svc.emergencies(me)["flags"]
        self.assertEqual([f["label"] for f in flags], ["Patient E"])      # F (daughter) and G (past) don't fire
        self.svc.assess_flag(me, flags[0]["id"], "emergency", "Pale and clammy with the warning sign right now.")
        self.svc.raise_manual_flag(me, f"{org}_F", "Worried about this one too")   # over-call
        self.edu.next_event(me)
        sc = self.edu.session_result(self.avery, self.edu.finish(me)["session_id"])["score"]
        by = {(i["patient"], i["area"]): i for i in sc["items"]}
        self.assertEqual(by[("Patient E", "Emergency decision")]["outcome"], "correct")
        self.assertEqual(by[("Patient F", "Emergency decision")]["outcome"], "missed")
        self.assertEqual(by[("Patient G", "Restricted record")]["outcome"], "correct")


class TestCaseBuilder(Base):
    def test_phi_guard_and_label_rules(self):
        for path, value in ((("spec", "notes", 1, "text"), "Mr. Smith reports demo red flag R."),
                            (("spec", "notes", 1, "text"), "DOB 01/02/1980, demo red flag R."),
                            (("spec", "notes", 1, "text"), "Call 404-555-0101 about demo red flag R."),
                            (("spec", "patients", 0, "label"), "John Smith")):
            case = deep(CASE)
            obj = case
            for k in path[:-1]:
                obj = obj[k]
            obj[path[-1]] = value
            with self.assertRaises(BadInput):
                self.edu.save_scenario(self.taylor, None, case)
        case = deep(CASE)
        case["synthetic_attested"] = False
        with self.assertRaises(BadInput):
            self.edu.save_scenario(self.taylor, None, case)

    def test_student_draft_is_private_and_not_sent_to_ai_until_approved(self):
        sid = self.edu.save_scenario(self.taylor, None, deep(CASE))["id"]
        self.assertNotIn(sid, [s["id"] for s in self.edu.list_scenarios(self.avery)])
        with self.assertRaises(NotFound):
            self.edu.get_scenario(self.avery, sid)
        # practicing an unapproved draft: the note is never sent to the AI
        _, me = self.start(self.taylor, sid)
        self.svc.run_jobs_once()
        st = self.svc.state(me)
        self.assertEqual(st["inbox"][0]["ai_state"], "blocked_not_approved_fixture")
        self.assertEqual(len(self.svc.emergencies(me)["flags"]), 1)      # local emergency rules still work
        # submit -> instructor returns with a comment -> student fixes -> instructor approves
        self.edu.submit_scenario(self.taylor, sid)
        with self.assertRaises(BadInput):
            self.edu.review_scenario(self.morgan, sid, "return", "")
        self.edu.review_scenario(self.morgan, sid, "return", "Add the patient's location to the note please.")
        self.assertEqual(self.edu.get_scenario(self.taylor, sid)["status"], "draft")
        self.edu.submit_scenario(self.taylor, sid)
        self.edu.review_scenario(self.morgan, sid, "approve")
        self.assertIn(sid, [s["id"] for s in self.edu.list_scenarios(self.avery)])   # now in the class library
        sent = len(self.app.adapter.sent_requests)
        _, me2 = self.start(self.avery, sid)
        self.svc.run_jobs_once()
        self.assertGreater(len(self.app.adapter.sent_requests), sent)    # approved text may now go to the AI
        with self.assertRaises(Forbidden):
            self.edu.review_scenario(self.taylor, sid, "approve")


if __name__ == "__main__":
    unittest.main()
