"""Headless Chromium checks of focus, announcements and reading order (Playwright).

These observe the DOM and the accessibility tree. They do NOT establish what VoiceOver speaks;
that requires a manual VoiceOver + Safari session (docs/07-test-report.md).
"""
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer

try:
    from playwright.sync_api import sync_playwright
except ImportError:   # pragma: no cover
    sync_playwright = None

from app.server import make_handler
from tests.helpers import CANARY_PASSAGE, make_app

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class TestBrowserFocus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):   # fresh app + server per test so fixtures are never duplicates
        self.app = make_app()
        self.app.start_worker()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.srv.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.app._stop.set()
        self.srv.shutdown()

    def signin(self, who):
        # bypass_csp only so Playwright's own string predicates can run; app CSP is tested in test_http.
        ctx = self.browser.new_context(bypass_csp=True)
        page = ctx.new_page()
        page.goto(self.base + "/")
        page.get_by_role("button", name=f"Sign in as {who}").click()
        page.get_by_role("button", name="Review Patient A").wait_for()
        return page

    def active_text(self, page):
        return page.evaluate("document.activeElement && document.activeElement.textContent")

    def test_signature_two_stage_flow(self):
        riley = self.signin("Riley, RN (demo)")
        a_btn = riley.get_by_role("button", name="Review Patient A")
        a_btn.focus()
        riley.evaluate("window.__aNode = document.activeElement")
        order0 = riley.locator("#queue .open").all_text_contents()
        self.assertLess(order0.index("Review Patient A"), order0.index("Review Patient B"))

        # Stage 1: a signed synthetic note for B arrives while the nurse is on A.
        self.app.svc.deliver_fixture("org_n", "note_B2", 1)
        riley.wait_for_function("document.getElementById('announcer').textContent.includes('New evidence awaiting clinical review')",
                                timeout=8000)
        spoken = riley.locator("#announcer").text_content()
        self.assertNotIn("Patient B", spoken)
        self.assertNotIn("demo signal", spoken)
        self.assertNotIn(CANARY_PASSAGE, spoken)
        self.assertEqual(self.active_text(riley), "Review Patient A")
        self.assertTrue(riley.evaluate("document.activeElement === window.__aNode"))
        self.assertEqual(riley.locator("#queue .open").all_text_contents(), order0)   # care order unchanged

        # Stage 2: a second authorized clinician confirms B's change in another session.
        sam = self.signin("Sam, Charge RN (demo; qualified confirmer role)")
        item = next(i["item_id"] for i in self.app.svc.state(self.app.store.one("SELECT * FROM users WHERE id='u_sam'"))["inbox"]
                    if i["encounter_id"] == "enc_B")
        res = sam.evaluate("""async (item) => {
            const d = await (await fetch('/api/review-items/' + item)).json();
            const r = await fetch('/api/encounters/enc_B/priority', {method: 'POST', headers: {'Content-Type': 'application/json',
              'X-Requested-With': 'triage-ui'}, body: JSON.stringify({priority: 1, reason_code: 'new_evidence_confirmed',
              expected_version: d.encounter_version, review_item_id: item})});
            return r.status; }""", item)
        self.assertEqual(res, 200)
        for page in (riley, sam):   # B moves ahead of A on both screens
            page.wait_for_function("document.querySelector('#queue .open').textContent === 'Review Patient B'", timeout=8000)
            order = page.locator("#queue .open").all_text_contents()
            self.assertLess(order.index("Review Patient B"), order.index("Review Patient A"))
        self.assertTrue(riley.evaluate("document.activeElement === window.__aNode"), "focus moved off A's node")
        self.assertEqual(self.active_text(riley), "Review Patient A")

        riley.keyboard.press("Enter")   # must open A, not whatever row is now at A's old position
        riley.get_by_role("heading", name="Patient A, encounter").wait_for()
        detail = riley.locator("#detail-body").inner_text()
        self.assertIn("Encounter ID enc_A", detail)
        self.assertIn("Reassessment: due 10:30", detail)        # A's reassessment still on schedule
        self.assertEqual(self.active_text(riley), "Patient A, encounter")

        # Deliberately reach B: back to queue (focus returns to A's row), then up one row.
        riley.get_by_role("button", name="Back to care queue (Patient A)").click()
        self.assertEqual(self.active_text(riley), "Review Patient A")
        riley.locator("#queue-h").focus()
        riley.keyboard.press("Tab")
        self.assertEqual(self.active_text(riley), "Review Patient B")
        with open(os.path.join(OUT, "browser_queue_aria.txt"), "w") as f:
            f.write(riley.locator("#queue").aria_snapshot())

    def test_beginner_guide_walks_through_steps(self):
        page = self.signin("Riley, RN (demo)")
        guide = page.locator("#guide-step")
        self.assertEqual(page.locator("#guide-progress").text_content(), "Step 1 of 5")
        page.get_by_role("button", name="Send Patient B's new note").click()
        page.wait_for_function("document.getElementById('guide-progress').textContent === 'Step 2 of 5'", timeout=8000)
        page.get_by_role("button", name="Open Patient B's new note").click()
        page.get_by_role("heading", name="Note review: Patient B (AI-assisted, unverified)").wait_for()
        self.assertIn("How to check this note", page.locator("#detail-body").inner_text())
        self.assertEqual(page.locator("#guide-progress").text_content(), "Step 3 of 5")
        page.get_by_label("Accept AI interpretation as shown").check()
        page.get_by_role("button", name="Record review decision").click()
        page.wait_for_function("document.getElementById('guide-progress').textContent === 'Step 4 of 5'", timeout=8000)
        self.assertIn("sign in as Sam", guide.text_content())
        # Text size control and hiding tips
        page.get_by_role("button", name="Extra large").click()
        self.assertEqual(page.evaluate("document.documentElement.style.fontSize"), "145%")
        page.get_by_role("button", name="Hide tips").click()
        self.assertTrue(page.locator("#guide-step").is_hidden())
        page.get_by_role("button", name="Show tips").click()
        self.assertTrue(page.locator("#guide-step").is_visible())
        page.screenshot(path=os.path.join(OUT, "screenshot_guide.png"), full_page=True)

    def test_workstation_layout_split_view_and_pins(self):
        ctx = self.browser.new_context(bypass_csp=True, viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(self.base + "/")
        page.get_by_role("button", name="Sign in as Riley").click()
        page.get_by_role("button", name="Review Patient A").click()
        page.get_by_role("heading", name="Patient A, encounter").wait_for()
        # chart opens beside the list; the list stays visible; the page itself does not scroll
        self.assertTrue(page.locator("#queue").is_visible())
        self.assertTrue(page.locator("#detail").is_visible())
        self.assertEqual(page.evaluate("document.documentElement.scrollHeight"), 900)
        page.get_by_role("tab", name="Notes & documents").click()
        page.get_by_role("button", name="Pin Clinical note note_A1 v1").click()
        self.assertTrue(page.locator(".pin").is_visible())
        page.get_by_role("button", name="Pinned notes (1) · Hide").click()
        self.assertTrue(page.locator("#pins-list").is_hidden())
        # sidebar page switch keeps the chart and pins
        page.get_by_role("button", name="Overdue").click()
        self.assertTrue(page.locator("#ob-table").is_visible())
        self.assertTrue(page.locator("#detail").is_visible())
        self.assertEqual(page.evaluate("document.activeElement.id"), "ob-h")
        page.screenshot(path=os.path.join(OUT, "screenshot_workstation.png"))
        ctx.close()

    def test_emergency_flag_nurse_check_and_charge_decision(self):
        riley = self.signin("Riley, RN (demo)")
        self.app.svc.deliver_fixture("org_n", "note_D1", 1)
        riley.wait_for_function("!document.getElementById('emerg-banner').hidden", timeout=8000)
        urgent = riley.locator("#announcer-urgent").text_content()
        self.assertIn("Possible emergency flagged", urgent)
        self.assertNotIn("Patient D", urgent)
        order = riley.locator("#queue .open").all_text_contents()
        self.assertEqual(order[0], "Review Patient D")          # D was already first; the flag moved nobody
        riley.get_by_role("button", name="Open emergencies").click()
        riley.get_by_role("button", name="Check Patient D's possible emergency").first.click()
        riley.get_by_role("heading", name="Possible emergency: Patient D").wait_for()
        self.assertIn("Patient reports demo red flag R", riley.locator("#detail-body").inner_text())
        riley.get_by_label("Yes, I think this is an emergency").check()
        riley.get_by_role("button", name="Save my check").click()
        self.assertIn("reason", riley.locator(".form-result").first.text_content())   # reason is required
        riley.get_by_label("Reason (required)").first.fill("Patient looks pale and sweaty at the bedside.")
        riley.get_by_role("button", name="Save my check").click()
        riley.wait_for_function("document.getElementById('detail-body').innerText.includes('Nurse check: this IS an emergency')", timeout=8000)
        self.assertEqual(len(riley.get_by_role("button", name="Save final decision").all()), 0)   # nurses can't decide

        sam = self.signin("Sam, Charge RN")
        sam.locator(".nav[data-view='emergencies']").click()
        sam.get_by_role("button", name="Check Patient D's possible emergency").first.click()
        sam.get_by_role("heading", name="Possible emergency: Patient D").wait_for()
        self.assertIn("Patient looks pale and sweaty", sam.locator("#detail-body").inner_text())   # charge sees the nurse's reason
        sam.get_by_label("Confirmed emergency (moves patient to Level 1)").check()
        sam.locator("textarea[id^='determine-']").fill("Agree with Riley; confirmed at bedside.")
        sam.get_by_role("button", name="Save final decision").click()
        riley.wait_for_function("[...document.querySelectorAll('#queue li')][0].innerText.includes('Level 1')", timeout=8000)
        self.assertIn("Emergency confirmed", riley.locator("#queue li").first.inner_text())

    def test_chart_tabs_search_and_typed_voice_commands(self):
        page = self.signin("Riley, RN (demo)")
        page.get_by_role("button", name="Voice").click()
        page.get_by_label("Or type a command").fill("bring up patient bee")
        page.get_by_role("button", name="Go", exact=True).click()
        page.get_by_role("heading", name="Patient B, encounter").wait_for()
        self.assertIn("Opened Patient B's chart", page.locator("#voice-result").text_content())
        page.get_by_label("Or type a command").fill("search for denies in patient B")
        page.get_by_role("button", name="Go", exact=True).click()
        page.wait_for_function("document.querySelectorAll('#detail-body mark').length > 0", timeout=5000)
        self.assertEqual(page.locator("#detail-body mark").first.text_content().lower(), "denies")
        self.assertIn("1 of 1 notes match", page.locator("#chart-search-count").text_content())
        # write a note (typed; headless Chromium has no microphone) and see it in the chart
        page.get_by_role("tab", name="Write note").click()
        page.get_by_label("Note for Patient B").fill("Patient reports demo red flag S right now. Called charge nurse.")
        page.get_by_role("button", name="Save note to chart").click()
        page.get_by_role("tab", name="Notes & documents (2)").wait_for(timeout=8000)
        self.assertIn("Nursing note · Riley", page.locator("#detail-body").inner_text())
        page.wait_for_function("!document.getElementById('emerg-banner').hidden", timeout=8000)   # rule fired on nurse note
        page.get_by_role("tab", name="Summary").focus()
        page.keyboard.press("ArrowRight")
        self.assertEqual(page.evaluate("document.activeElement.textContent"), "Notes & documents (2)")
        page.get_by_label("Or type a command").fill("open emergencies")
        page.get_by_role("button", name="Go", exact=True).click()
        self.assertEqual(page.evaluate("document.activeElement.id"), "em-h")

    def test_access_revoked_clears_patient_content(self):
        page = self.signin("Jordan, RN (demo)")
        page.get_by_role("button", name="Review Patient A").click()
        page.get_by_role("heading", name="Patient A, encounter").wait_for()
        self.app.svc.sim_revoke("u_jordan")
        page.wait_for_function("document.getElementById('login-h').textContent.includes('Your access has ended')", timeout=20000)
        self.assertEqual(page.locator("#detail-body").inner_html(), "")
        self.assertEqual(page.locator("#queue").inner_html(), "")
        self.assertTrue(page.locator("#app").is_hidden())
        self.assertEqual(page.evaluate("document.activeElement.id"), "login-h")

    def test_draft_survives_incoming_updates_and_return_path(self):
        riley = self.signin("Riley, RN (demo)")
        self.app.svc.deliver_fixture("org_n", "note_C2", 1)
        riley.get_by_role("button", name="Notes to review").click()   # sidebar page switch
        btn = riley.get_by_role("button", name="Open note review for Patient C, received 10:00")
        btn.wait_for(timeout=8000)
        riley.wait_for_function("[...document.querySelectorAll('#inbox .desc')].some(d => d.textContent.includes('candidate evidence'))",
                                timeout=8000)
        btn.click()
        riley.get_by_role("heading", name="Note review: Patient C (AI-assisted, unverified)").wait_for()
        riley.get_by_label("Comment (optional)").fill("Draft in progress")
        self.app.svc.deliver_fixture("org_n", "note_C3", 1)          # update arrives mid-draft
        riley.wait_for_function("document.getElementById('inbox-h').textContent.includes('2 unreviewed')", timeout=8000)
        self.assertEqual(riley.get_by_label("Comment (optional)").input_value(), "Draft in progress")
        # Exact quote and full sentence are present in reading order, original note one action away
        body = riley.locator("#detail-body").inner_text()
        self.assertIn("Demo signal A resolved last week and has not recurred.", body)
        self.assertLess(body.index("AI-assisted, unverified"), body.index("Candidate findings"))
        with open(os.path.join(OUT, "browser_evidence_aria.txt"), "w") as f:
            f.write(riley.locator("#detail").aria_snapshot())


if __name__ == "__main__":
    unittest.main()
