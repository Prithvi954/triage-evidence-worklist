"""Record a ~2-minute silent demo video: captions + highlights, no voice-over.

  .venv/bin/python -m scripts.make_demo_video

Writes docs/tutorial/demo-2min.mp4 (H.264, burned-in captions + soft subtitle track) and demo-2min.vtt.
The app runs live against a fresh synthetic database; nothing is mocked.
"""
import os
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright

from app.server import make_handler
from scripts.make_tutorial import FFMPEG, H, OUT, SAM_API, W, Tour, ts
from tests.helpers import make_app

REPO = "github.com/Prithvi954/triage-evidence-worklist"


class Silent:
    """No audio: each caption is held for comfortable reading time."""

    def clip(self, text):
        words = len(text.split())
        return None, max(3.2, words / 3.3 + 1.0)


def script(t, page, app, sam):
    g = page.get_by_role
    S = t.say
    nav = lambda v: page.locator(f".nav[data-view='{v}']")

    t.card("A.I Triage System", "2-minute demo · EmoryHacks 2026 · Healthcare · Coder for Good",
           "A.I Triage System. Two-minute demo.")

    S("The idea", "A nurse who uses a screen reader signs in. New notes arrive. Claude finds the evidence; people decide.",
      spot=g("button", name="Sign in as Riley"),
      action=lambda: (g("button", name="Sign in as Riley").click(), g("button", name="Review Patient A").wait_for()))
    S("Care queue", "The official order: top is seen first. Colors show urgency. The AI can never move a patient.",
      spot="#queue")

    S("A new note arrives", "A new note for Patient B arrives…", spot=g("button", name="Send Patient B's new note"),
      action=lambda: g("button", name="Send Patient B's new note").click())
    g("button", name="Open Patient B's new note").wait_for()
    t._sleep(0.8)
    S("Private by design", "Screen reader hears: “New evidence awaiting clinical review.” No name, no symptom. Order unchanged.",
      spot=".sidebar ul")

    S("Claude finds the evidence", "Exact quotes from the note, labeled AI-assisted and unverified.",
      action=lambda: (g("button", name="Open Patient B's new note").click(),
                      g("heading", name="Note review: Patient B (AI-assisted, unverified)").wait_for()),
      after=page.locator("#detail-body .finding").nth(0))
    S("Claude finds the evidence", "It caught the change: denied at triage, now reported.",
      spot=page.locator("#detail-body .finding").nth(0))
    S("Claude finds the evidence", "And kept the mother’s history separate. A keyword search gets this wrong.",
      spot=page.locator("#detail-body .finding").nth(1))

    def sent():
        d = page.locator("#detail-body summary", has_text="What Claude received")
        d.evaluate("e => e.scrollIntoView({block:'center'})")
        d.click()
        page.locator("#detail-body h4", has_text="Exact outbound excerpts").wait_for()
    S("Privacy", "What Claude received: two short excerpts. No name, no record number, no dates.", action=sent,
      after=page.locator("#detail-body ul").filter(has_text="current note excerpt").first)

    def accept():
        page.get_by_label("Accept AI interpretation as shown").evaluate("e => e.scrollIntoView({block:'center'})")
        page.get_by_label("Accept AI interpretation as shown").check()
        t._sleep(0.4)
        g("button", name="Record review decision").click()
        page.locator("#dec-result").wait_for()
    S("The nurse checks it", "Riley checks it against the real note and accepts. Saved with her name.",
      action=accept, after="#dec-result")

    def back():
        g("button", name="Back to care queue").click()
        g("button", name="Review Patient A").focus()
    S("Focus stays put", "Riley goes back to Patient A. Meanwhile the charge nurse approves Level 1 for B…",
      action=back, after=g("button", name="Review Patient A"))
    sam.evaluate(SAM_API["confirm_b"])
    page.wait_for_function("document.querySelector('#queue .open').textContent === 'Review Patient B'", timeout=8000)
    S("Focus stays put", "B jumps to the top, but Riley’s place stays on A. Enter opens Patient A, not the wrong patient.",
      spot=g("button", name="Review Patient A"),
      action=lambda: (page.keyboard.press("Enter"), g("heading", name="Patient A, encounter").wait_for()), after="#detail")

    def d1():
        nav("tools").click()
        t._sleep(0.5)
        page.locator("#deliver-buttons button").filter(has_text="Patient D").first.click()
        page.wait_for_function("!document.getElementById('emerg-banner').hidden", timeout=10000)
    S("Possible emergencies", "A rule flags a possible emergency for Patient D. Urgent alert, still no name.",
      action=d1, after="#emerg-banner")

    def check():
        nav("emergencies").click()
        g("button", name="Check Patient D's possible emergency").first.click()
        g("heading", name="Possible emergency: Patient D").wait_for()
        page.get_by_label("Yes, I think this is an emergency").evaluate("e => e.scrollIntoView({block:'center'})")
        page.get_by_label("Yes, I think this is an emergency").check()
        t.type(page.locator("textarea[id^='assess-']"), "Pale and sweaty, getting worse.")
        g("button", name="Save my check").click()
        page.wait_for_function("document.getElementById('detail-body').innerText.includes('Nurse check: this IS an emergency')", timeout=8000)
    S("Possible emergencies", "Any nurse checks it — and must write why.", action=check,
      after=page.locator("#detail-body .history"))

    def decide():
        sam.evaluate(SAM_API["decide_d"])
        page.wait_for_function("[...document.querySelectorAll('#queue li')].some(li => li.innerText.includes('Emergency confirmed'))",
                               timeout=10000)
        nav("patients").click()
    S("Possible emergencies", "The charge nurse reads the reason and confirms. Patient D moves to Level 1, with full history.",
      action=decide, after=page.locator("#queue li").filter(has_text="Patient D").first)

    def chart():
        g("button", name="Review Patient B").click()
        g("heading", name="Patient B, encounter").wait_for()
        g("tab", name="Notes & documents").click()
        t.type(page.locator("#chart-search"), "denies")
    S("Patient chart", "An Epic-style chart with tabs. Search the notes — matches light up.", action=chart,
      after=page.locator("#detail-body .doc").first)

    def voice():
        page.locator("#voice-btn").click()
        t.type(page.locator("#voice-text"), "bring up patient C")
        page.get_by_role("button", name="Go", exact=True).click()
        g("heading", name="Patient C, encounter").wait_for()
    S("Voice", "Hands busy? Speak: “bring up patient C.” Dictation writes notes too.", action=voice, after="#detail")
    page.locator("#voice-close").click()

    def outage():
        nav("tools").click()
        page.select_option("#fault", "outage")
        g("button", name="Apply", exact=True).click()
        page.locator("#deliver-buttons button", has_text="went away last week").click()
        for _ in range(4):
            time.sleep(0.3)
            app.clock.advance(minutes=2)
        nav("inbox").click()
        page.wait_for_function("[...document.querySelectorAll('#inbox .desc')].some(d => d.textContent.includes('manual review required'))",
                               timeout=15000)
    S("Works without AI", "If the AI goes down, notes still show up for manual review. Nothing is lost.", action=outage,
      after=page.locator("#inbox li").filter(has_text="manual review required").first)

    t.card("The AI suggests. People decide.", f"Accessible · Private · Works without AI · 57 automated tests · {REPO}",
           "The AI suggests. People decide. Accessible, private, and works without AI.")


def build(timeline, video):
    vtt = os.path.join(OUT, "demo-2min.vtt")
    with open(vtt, "w") as f:
        f.write("WEBVTT\n\n")
        for i, (a, b, _, text, _) in enumerate(timeline, 1):
            f.write(f"{i}\n{ts(a)} --> {ts(b)}\n{text}\n\n")
    mp4 = os.path.join(OUT, "demo-2min.mp4")
    subprocess.run([FFMPEG, "-y", "-i", video, "-i", vtt, "-map", "0:v", "-map", "1:s",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "22",
                    "-c:s", "mov_text", "-metadata:s:s:0", "language=eng", "-movflags", "+faststart", mp4],
                   check=True, capture_output=True)
    return mp4


def main():
    work = tempfile.mkdtemp(prefix="demo-")
    app = make_app()
    app.start_worker()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    srv.daemon_threads = True
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        sam_ctx = b.new_context(bypass_csp=True)
        sam = sam_ctx.new_page()
        sam.goto(base)
        sam.get_by_role("button", name="Sign in as Sam").click()
        sam.get_by_role("button", name="Review Patient A").wait_for()
        ctx = b.new_context(bypass_csp=True, viewport={"width": W, "height": H}, record_video_dir=work,
                            record_video_size={"width": W, "height": H})
        page = ctx.new_page()
        t = Tour(page, Silent(), fast=False)
        page.goto(base)
        page.get_by_role("button", name="Sign in as Riley").wait_for()
        script(t, page, app, sam)
        video = page.video.path()
        ctx.close()
        sam_ctx.close()
        b.close()
    app._stop.set()
    srv.shutdown()
    mp4 = build(t.timeline, video)
    shutil.rmtree(work, ignore_errors=True)
    print({"mp4": mp4, "seconds": round(t.timeline[-1][1], 1), "captions": len(t.timeline)})


if __name__ == "__main__":
    main()
