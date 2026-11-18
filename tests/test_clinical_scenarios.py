"""Clinical practice scenarios: red-flag rules fire on the real emergencies only, and perfect play scores 100%."""
import unittest

from app.scenarios_clinical import CLINICAL_SCENARIOS
from tests.helpers import make_app, user


class TestClinicalScenarios(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.svc, self.edu = self.app.svc, self.app.education
        self.taylor = user(self.app, "u_taylor")

    def play_perfectly(self, sc):
        r = self.edu.start_session(self.taylor, sc["id"], "parent")
        me = self.svc.user_for_token(r["token"])
        org = me["org"]
        traps = {f"{org}:{n['key']}": n["ai_trap"] for n in sc["spec"]["notes"] if n.get("ai_trap")}
        while True:
            self.svc.run_jobs_once()
            for it in self.svc.state(me)["inbox"]:
                d = self.svc.review_item_detail(me, it["item_id"])
                trap = traps.get(d["note"]["note_id"])
                if not d["findings"]:
                    self.svc.decide(me, it["item_id"], "reviewed_no_ai", {}, "", d["version"])
                elif trap:
                    corr = {f["id"]: {trap["field"]: trap["correct"]} for f in d["findings"]}
                    self.svc.decide(me, it["item_id"], "correct", corr, "", d["version"])
                else:
                    self.svc.decide(me, it["item_id"], "accept", {}, "", d["version"])
            info = self.edu.training_info(me)
            if info["event_index"] >= info["events_total"]:
                break
            self.edu.next_event(me)
        flags = self.svc.emergencies(me)["flags"]
        for p in sc["spec"]["patients"]:
            eid, ans = f"{org}_{p['key']}", p["answer"]
            mine = [f for f in flags if f["encounter_id"] == eid]
            if ans["emergency"] is True:
                self.assertTrue(mine, f"{sc['title']}: no red flag fired for {p['label']}")
                for f in mine:
                    self.svc.assess_flag(me, f["id"], "emergency", "New warning signs in the latest note; needs to be seen now.")
                    if me["role"] in ("charge_rn", "provider"):
                        self.svc.determine_flag(me, f["id"], "confirmed_emergency", "Confirmed: moving up to be seen now.")
            elif ans["emergency"] is False:
                self.assertFalse(mine, f"{sc['title']}: false red flag for {p['label']}: {[f['label'] for f in mine]}")
            if ans["max_acuity"] and me["role"] in ("charge_rn", "provider"):
                enc = self.app.store.one("SELECT priority, version FROM encounters WHERE id=?", (eid,))
                if enc["priority"] is None or enc["priority"] > ans["max_acuity"]:
                    self.svc.confirm_priority(me, eid, ans["max_acuity"], "clinical_judgement", enc["version"])
        tid = self.edu.finish(me)["session_id"]
        return self.edu.session_result(self.taylor, tid)["score"]

    def test_every_clinical_scenario_scores_100_when_played_correctly(self):
        for sc in CLINICAL_SCENARIOS:
            with self.subTest(sc["title"]):
                score = self.play_perfectly(sc)
                missed = [i for i in score["items"] if i["outcome"] != "correct" and i["max"]]
                self.assertEqual(missed, [])
                self.assertEqual(score["percent"], 100)

    def test_clinical_scenarios_in_library_with_review_status(self):
        lib = {s["title"]: s for s in self.edu.list_scenarios(self.taylor)}
        for sc in CLINICAL_SCENARIOS:
            self.assertIn("Pending review", lib[sc["title"]]["clinical_review"])
        titles = list(lib)
        self.assertLess(titles.index("Chest pain in the waiting room"), titles.index("The changing story"))

    def test_esi_5_is_allowed(self):
        sam = user(self.app, "u_sam")
        v = self.app.store.one("SELECT version FROM encounters WHERE id='enc_C'")["version"]
        self.svc.confirm_priority(sam, "enc_C", 5, "clinical_judgement", v)
        self.assertEqual(self.app.store.one("SELECT priority FROM encounters WHERE id='enc_C'")["priority"], 5)


if __name__ == "__main__":
    unittest.main()
