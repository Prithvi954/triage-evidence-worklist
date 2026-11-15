"""Record a narrated video tour of the live app (synthetic data only).

  .venv/bin/python -m scripts.make_tutorial                  # neural voice (Kokoro, af_heart)
  .venv/bin/python -m scripts.make_tutorial --voice am_michael
  .venv/bin/python -m scripts.make_tutorial --voice say      # fall back to macOS `say`

Neural voice: Kokoro (open-weight TTS, runs offline). Model files go in .models/:
  kokoro-v1.0.onnx and voices-v1.0.bin from github.com/thewh1teagle/kokoro-onnx releases (model-files-v1.0).
Pass 1 (no video) pre-generates and caches every narration clip; pass 2 records the video in real time.
Writes docs/tutorial/tutorial.mp4 (video + voice + soft subtitles), tutorial.vtt, TRANSCRIPT.md.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from http.server import ThreadingHTTPServer

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

from app.server import make_handler
from tests.helpers import make_app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "tutorial")
MODELS = os.path.join(ROOT, ".models")
CACHE = os.path.join(MODELS, "tts_cache")
W, H = 1440, 900
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

OVERLAY_JS = """
(() => {
  if (document.getElementById('tour-cap')) return;
  const cap = document.createElement('div'); cap.id = 'tour-cap';
  Object.assign(cap.style, {position:'fixed', left:'270px', bottom:'22px', width:'780px', zIndex:99999,
    background:'rgba(10,26,42,.94)', color:'#fff', borderRadius:'14px', padding:'14px 20px 16px',
    font:'600 23px/1.4 "Avenir Next", Avenir, sans-serif', boxShadow:'0 12px 40px rgba(0,0,0,.35)', pointerEvents:'none'});
  const ch = document.createElement('div'); ch.id = 'tour-ch';
  Object.assign(ch.style, {font:'800 13px/1.2 "Avenir Next", Avenir, sans-serif', letterSpacing:'.08em',
    textTransform:'uppercase', color:'#7fd6dc', marginBottom:'6px'});
  const tx = document.createElement('div'); tx.id = 'tour-tx';
  cap.append(ch, tx); document.body.append(cap);
  const sp = document.createElement('div'); sp.id = 'tour-spot';
  Object.assign(sp.style, {position:'fixed', zIndex:99998, border:'4px solid #ffb020', borderRadius:'12px',
    boxShadow:'0 0 0 9999px rgba(10,26,42,.28), 0 0 24px 4px rgba(255,176,32,.8)', pointerEvents:'none',
    transition:'all .35s ease', display:'none'});
  document.body.append(sp);
  const card = document.createElement('div'); card.id = 'tour-card';
  Object.assign(card.style, {position:'fixed', inset:'0', zIndex:100000, display:'none', placeItems:'center',
    background:'linear-gradient(160deg,#0f2a44 0%,#154c6b 55%,#0b6e78 100%)', color:'#fff', textAlign:'center',
    font:'600 26px/1.45 "Avenir Next", Avenir, sans-serif'});
  document.body.append(card);
})();
"""


class Voice:
    """Narration clips, cached by (engine, voice, text) so the recorded pass never waits on synthesis."""

    def __init__(self, voice):
        self.voice = voice
        os.makedirs(CACHE, exist_ok=True)
        self.kokoro = None
        if voice != "say":
            from kokoro_onnx import Kokoro
            self.kokoro = Kokoro(os.path.join(MODELS, "kokoro-v1.0.onnx"), os.path.join(MODELS, "voices-v1.0.bin"))

    def clip(self, text):
        key = hashlib.sha1(f"{self.voice}|{text}".encode()).hexdigest()[:16]
        path = os.path.join(CACHE, f"{key}.wav")
        if not os.path.exists(path):
            if self.kokoro:
                import soundfile as sf
                samples, sr = self.kokoro.create(text, voice=self.voice, speed=1.0, lang="en-us")
                sf.write(path, samples, sr, subtype="PCM_16")
            else:
                subprocess.run(["say", "-v", "Samantha", "-r", "182", "-o", path, "--data-format=LEI16@22050", text], check=True)
        with wave.open(path) as w:
            return path, w.getnframes() / w.getframerate()


class Tour:
    def __init__(self, page, voice, fast):
        self.page, self.voice, self.fast = page, voice, fast
        self.t0 = time.monotonic()
        self.timeline = []

    def _sleep(self, s):
        if not self.fast and s > 0:
            time.sleep(s)

    def overlay(self):
        self.page.evaluate(OVERLAY_JS)

    def say(self, chapter, text, spot=None, action=None, after=None, pad=0.6):
        wav, dur = self.voice.clip(text)
        self.overlay()
        if spot is not None:
            self.spot(spot)
        self.page.evaluate("([c,t]) => { document.getElementById('tour-ch').textContent = c;"
                           " document.getElementById('tour-tx').textContent = t; document.getElementById('tour-cap').style.display='block'; }",
                           [chapter, text])
        start = time.monotonic() - self.t0
        if action:
            self._sleep(min(1.2, dur * 0.3))
            action()
        if after is not None:
            self.spot(after)
        self._sleep(start + dur + pad - (time.monotonic() - self.t0))
        self.timeline.append((start, time.monotonic() - self.t0, chapter, text, wav))
        self.unspot()

    def card(self, title, sub, spoken):
        wav, dur = self.voice.clip(spoken)
        self.overlay()
        self.page.evaluate("([a,b]) => { const c = document.getElementById('tour-card'); c.innerHTML = '';"
                           " const box = document.createElement('div'); const h = document.createElement('div'); h.textContent = a;"
                           " h.style.cssText = 'font-size:52px;font-weight:800;margin-bottom:18px';"
                           " const p = document.createElement('div'); p.textContent = b; p.style.cssText='max-width:920px;opacity:.92';"
                           " box.append(h,p); c.append(box); c.style.display='grid';"
                           " document.getElementById('tour-cap').style.display='none'; }", [title, sub])
        start = time.monotonic() - self.t0
        self._sleep(dur + 0.9)
        self.page.evaluate("document.getElementById('tour-card').style.display='none'")
        self.timeline.append((start, time.monotonic() - self.t0, title, spoken, wav))

    def spot(self, loc, pad=8):
        if isinstance(loc, str):
            loc = self.page.locator(loc).first
        try:
            loc.evaluate("e => e.scrollIntoView({block: 'center', inline: 'nearest'})", timeout=3000)
            self._sleep(0.35)
        except Exception:
            pass
        box = loc.bounding_box()
        if not box:
            return
        low = box["y"] + box["height"] > H - 190
        self.page.evaluate("(low) => { const c = document.getElementById('tour-cap'); if (!c) return;"
                           " if (low) { c.style.top = '96px'; c.style.bottom = 'auto'; } else { c.style.top = 'auto'; c.style.bottom = '22px'; } }",
                           low)
        self.page.evaluate("([x,y,w,h]) => { const s = document.getElementById('tour-spot'); Object.assign(s.style,"
                           " {left:x+'px', top:y+'px', width:w+'px', height:h+'px', display:'block'}); }",
                           [box["x"] - pad, box["y"] - pad, box["width"] + 2 * pad, box["height"] + 2 * pad])
        self._sleep(0.4)

    def unspot(self):
        self.page.evaluate("(() => { const s = document.getElementById('tour-spot'); if (s) s.style.display='none'; })()")

    def type(self, locator, text):
        locator.click()
        locator.type(text, delay=0 if self.fast else 35)


SAM_API = {
    "confirm_b": """async () => {
      const st = await (await fetch('/api/state')).json();
      const b = st.queue.find(q => q.encounter_id === 'enc_B');
      const r = await fetch('/api/encounters/enc_B/priority', {method:'POST', headers:{'Content-Type':'application/json',
        'X-Requested-With':'triage-ui'}, body: JSON.stringify({priority:1, reason_code:'new_evidence_confirmed', expected_version:b.version})});
      return r.status; }""",
    "decide_d": """async () => {
      const em = await (await fetch('/api/emergencies')).json();
      const f = em.flags.find(x => x.encounter_id === 'enc_D');
      const r = await fetch('/api/emergency-flags/' + f.id + '/determine', {method:'POST', headers:{'Content-Type':'application/json',
        'X-Requested-With':'triage-ui'}, body: JSON.stringify({determination:'confirmed_emergency',
        reason:'I agree with Riley. Saw the patient myself; treating as an emergency.', expected_version:f.version})});
      return r.status; }""",
}


def tour_script(t, page, app, sam):
    g = page.get_by_role
    S = t.say
    nav = lambda v: page.locator(f".nav[data-view='{v}']")

    t.card("Triage Evidence Worklist", "A guided tour for first-time users · practice demo with made-up patients",
           "Welcome to the Triage Evidence Worklist. This is a practice demo with made-up patients. "
           "In about seven minutes, you'll see everything it does, step by step.")

    # 1. Sign in
    S("1 · Sign in", "First, choose who you are. Riley is a nurse. Sam is the charge nurse, who makes the final decisions.",
      spot="#login-list")
    S("1 · Sign in", "We'll start as Riley.", spot=g("button", name="Sign in as Riley"),
      action=lambda: (g("button", name="Sign in as Riley").click(), g("button", name="Review Patient A").wait_for()))

    # 2. Layout
    S("2 · The screen", "This is the main screen. Everything fits on one page, so there's no long scrolling.")
    S("2 · The screen", "On the left are the pages: Patients, Emergencies, Notes to review, Overdue, Practice tools, and Help. "
      "The colored badges show how many things need attention.", spot=".sidebar ul")
    S("2 · The screen", "Across the top is the system status, plus the Voice button and text-size buttons.", spot=".topbar")
    S("2 · The screen", "The blue box is your guide. It always tells you what to do next.", spot="#guide")
    S("2 · The screen", "This is the care queue. The patient at the top is seen first. The colored edge and badge show how urgent "
      "each patient is. Level 1 is the most urgent, and purple means nobody has assessed them yet.", spot="#queue")

    # 3. A new note
    S("3 · A new note arrives", "Let's see what happens when a new note arrives. The guide says: send Patient B's new note.",
      spot=g("button", name="Send Patient B's new note"), action=lambda: g("button", name="Send Patient B's new note").click())
    g("button", name="Open Patient B's new note").wait_for()
    t._sleep(1.0)
    S("3 · A new note arrives", "A screen reader user hears a short message with no patient name, so nothing private is spoken out loud. "
      "And notice: the care queue did not change. The AI cannot move patients.", spot="#queue")

    # 4. Check the AI
    S("4 · Check the AI's work", "Let's open the new note. It opens on the right, and the list stays visible.",
      spot=g("button", name="Open Patient B's new note"),
      action=lambda: (g("button", name="Open Patient B's new note").click(),
                      g("heading", name="Note review: Patient B (AI-assisted, unverified)").wait_for()), after="#detail")
    S("4 · Check the AI's work", "The yellow label says AI-assisted, unverified. A person always has to check it.",
      spot="#detail-body .warn")
    S("4 · Check the AI's work", "The first finding quotes the note exactly: Patient B now reports demo signal A. "
      "It also found that earlier, the patient denied it. So something changed.", spot=page.locator("#detail-body .finding").nth(0))
    S("4 · Check the AI's work", "The second finding is about the patient's mother, not the patient, so the AI kept it separate. "
      "A simple keyword search would get this wrong.", spot=page.locator("#detail-body .finding").nth(1))

    def show_sent():
        det = page.locator("#detail-body summary", has_text="What Claude received")
        det.evaluate("e => e.scrollIntoView({block:'center'})")
        det.click()
        page.locator("#detail-body h4", has_text="Exact outbound excerpts").wait_for()
    S("4 · Privacy", "Under What Claude received, you can see exactly what was sent to the AI: two short excerpts, "
      "with no name, no record number, and no dates.", action=show_sent,
      after=page.locator("#detail-body ul").filter(has_text="current note excerpt").first)

    def accept():
        page.get_by_label("Accept AI interpretation as shown").evaluate("e => e.scrollIntoView({block:'center'})")
        page.get_by_label("Accept AI interpretation as shown").check()
        t._sleep(0.6)
        g("button", name="Record review decision").click()
        page.locator("#dec-result").wait_for()
    S("4 · Your decision", "Now record your decision: Accept, Correct, or Reject. We'll accept. "
      "Your decision is saved with your name.", action=accept, after="#dec-result")

    # 5. Approval
    def back_to_a():
        g("button", name="Back to care queue").click()
        g("button", name="Review Patient A").focus()
    S("5 · Approval", "Only a charge nurse can change how urgent a patient is. Riley goes back to the list and keeps her place on Patient A.",
      action=back_to_a, after=g("button", name="Review Patient A"))
    S("5 · Approval", "Now Sam, in another window, approves Level 1 for Patient B. Watch the list.",
      action=lambda: sam.evaluate(SAM_API["confirm_b"]), after="#queue")
    page.wait_for_function("document.querySelector('#queue .open').textContent === 'Review Patient B'", timeout=8000)
    S("5 · Approval", "Patient B moved to the top, but Riley's place stayed on Patient A. Pressing Enter opens Patient A, not someone else.",
      spot=g("button", name="Review Patient A"),
      action=lambda: (page.keyboard.press("Enter"), g("heading", name="Patient A, encounter").wait_for()), after="#detail")

    # 6. Emergencies
    def send_d1():
        nav("tools").click()
        t._sleep(0.8)
        page.locator("#deliver-buttons button").filter(has_text="Patient D").first.click()
    S("6 · Possible emergencies", "Next, emergencies. Clear, written rules can flag a possible emergency. Let's send a practice note "
      "where Patient D reports a warning sign.", action=send_d1)
    page.wait_for_function("!document.getElementById('emerg-banner').hidden", timeout=10000)
    S("6 · Possible emergencies", "A red banner appears, and the Emergencies badge lights up. A screen reader user hears an urgent alert, "
      "still without the patient's name.", spot="#emerg-banner")
    S("6 · Possible emergencies", "A flag never moves a patient by itself. Any nurse can check it.", spot=nav("emergencies"),
      action=lambda: nav("emergencies").click(), after="#em-list")

    def open_flag():
        g("button", name="Check Patient D's possible emergency").first.click()
        g("heading", name="Possible emergency: Patient D").wait_for()
    S("6 · Possible emergencies", "Open it to see why it was flagged: the rule, and the exact sentence from the note.",
      action=open_flag, after=page.locator("#detail-body blockquote").first)

    def nurse_check():
        page.get_by_label("Yes, I think this is an emergency").evaluate("e => e.scrollIntoView({block:'center'})")
        page.get_by_label("Yes, I think this is an emergency").check()
        t.type(page.locator("textarea[id^='assess-']"), "Saw the patient. Pale and sweaty, getting worse.")
    S("6 · Possible emergencies", "Riley checks the patient and answers yes or no. Writing a reason is required, "
      "so everyone can see why.", action=nurse_check, after=page.locator(".em-form").first)

    def save_check():
        g("button", name="Save my check").click()
        page.wait_for_function("document.getElementById('detail-body').innerText.includes('Nurse check: this IS an emergency')", timeout=8000)
    S("6 · Possible emergencies", "Save the check. It's added to the history, and the charge nurse can now see it.",
      action=save_check, after=page.locator("#detail-body .history"))

    def sam_decides():
        sam.evaluate(SAM_API["decide_d"])
        page.wait_for_function("[...document.querySelectorAll('#queue li')].some(li => li.innerText.includes('Emergency confirmed'))",
                               timeout=10000)
        page.locator(".nav[data-view='emergencies']").click()
        g("button", name="View Patient D's emergency decision").first.click()
        g("heading", name="Possible emergency: Patient D").wait_for()
    S("6 · Possible emergencies", "Sam, the charge nurse, reads Riley's reason and makes the final decision: confirmed emergency. "
      "Sam can change it again later if things change.", action=sam_decides, after=page.locator("#detail-body .history"))
    S("6 · Possible emergencies", "Confirming the emergency moved Patient D to Level 1, and every step is saved with a name, a time, and a reason.",
      spot=page.locator("#detail-body .history"))

    # 7. Chart
    def open_b_chart():
        nav("patients").click()
        g("button", name="Review Patient B").click()
        g("heading", name="Patient B, encounter").wait_for()
    S("7 · Patient chart", "Now the patient chart. Like big hospital systems, it has tabs: Summary, Notes and documents, "
      "Write note, and Emergencies.", action=open_b_chart, after="[role='tablist']")
    S("7 · Patient chart", "The Notes and documents tab shows every note, newest first.",
      action=lambda: g("tab", name="Notes & documents").click(), after="#detail-body .doc-list")
    S("7 · Patient chart", "Use the search bar to find words. Let's search for denies. Every match is highlighted in yellow.",
      action=lambda: t.type(page.locator("#chart-search"), "denies"), after=page.locator("#detail-body .doc").first)

    def write():
        g("tab", name="Write note").click()
        t.type(page.get_by_label("Note for Patient B"), "Reassessed patient. Resting comfortably, family at bedside.")
    S("7 · Write a note", "To write a note, open Write note. You can type, or press Start dictation and simply speak.",
      action=write, after="#doc-body")
    S("7 · Write a note", "Notes you write or dictate stay on this server. They're never sent to the AI, "
      "and the emergency rules check them automatically.", spot="#dict-btn")

    def save_note():
        g("button", name="Save note to chart").click()
        g("tab", name="Notes & documents").wait_for()
        page.wait_for_function("document.getElementById('detail-body').innerText.includes('Nursing note · Riley')", timeout=8000)
    S("7 · Write a note", "Save it, and it appears in the chart with your name and the time.",
      action=save_note, after=page.locator("#detail-body .doc").first)

    # 8. Voice
    def voice_cmd():
        page.locator("#voice-btn").click()
        t._sleep(0.8)
        t.type(page.locator("#voice-text"), "bring up patient C")
    S("8 · Voice", "The Voice button lets you control the app by speaking. Press Start listening and say something like: "
      "bring up patient C. You can also type the command.", action=voice_cmd, after="#voice-panel")

    def voice_go():
        page.get_by_role("button", name="Go", exact=True).click()
        g("heading", name="Patient C, encounter").wait_for()
    S("8 · Voice", "And Patient C's chart opens. This helps nurses who can't easily use a mouse, or whose hands are busy.",
      action=voice_go, after="#detail")
    S("8 · Voice", "Try other commands too, like take a note for patient B, search for denies, or open emergencies.",
      spot="#voice-panel", action=lambda: page.locator("#voice-panel summary").click())
    page.locator("#voice-close").click()

    # 9. Sticky notes + overdue
    def pin():
        g("tab", name="Notes & documents").click()
        page.locator("#detail-body .doc button").first.click()
    S("9 · Sticky notes", "Pin any note to keep it on screen as a sticky note while you look at other patients.",
      action=pin, after="#pins")
    S("9 · Overdue tasks", "The Overdue page shows late tasks and who is responsible. If someone runs late, a backup is assigned automatically.",
      action=lambda: nav("overdue").click(), after="#ob-table")

    # 10. AI down
    def outage():
        nav("tools").click()
        t._sleep(0.5)
        page.select_option("#fault", "outage")
        t._sleep(0.5)
        g("button", name="Apply", exact=True).click()
        page.locator("#deliver-buttons button", has_text="went away last week").click()
        for _ in range(4):
            time.sleep(0.4)
            app.clock.advance(minutes=2)
        nav("inbox").click()
        page.wait_for_function("[...document.querySelectorAll('#inbox .desc')].some(d => d.textContent.includes('manual review required'))",
                               timeout=15000)
    S("10 · If the AI stops working", "What if the AI stops working? Here we pretend it's down, and send another note.", action=outage,
      after=page.locator("#inbox li").filter(has_text="manual review required").first)
    S("10 · If the AI stops working", "The note still shows up, marked manual review required. Everything else keeps working without the AI.",
      spot=page.locator("#inbox li").filter(has_text="manual review required").first)

    # 11. Help
    def help_size():
        nav("help").click()
        t._sleep(1.0)
        page.get_by_role("button", name="Extra large text size").click()
        t._sleep(1.4)
        page.get_by_role("button", name="Normal text size").click()
    S("11 · Help", "Need a reminder of what a word means? Open Help and words. And use the A buttons to make text bigger.",
      action=help_size, after=".textsize")

    t.card("You're ready", "The AI suggests. People decide. Everything works without the AI. "
           "To practice again: Practice tools → Reset all demo data.",
           "That's the tour. Remember: the AI suggests, and people decide. "
           "To practice again, open Practice tools and press Reset all demo data.")


def run_pass(voice, record, work):
    app = make_app()
    app.start_worker()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    srv.daemon_threads = True
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    video = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        sam_ctx = browser.new_context(bypass_csp=True)
        sam = sam_ctx.new_page()
        sam.goto(base)
        sam.get_by_role("button", name="Sign in as Sam").click()
        sam.get_by_role("button", name="Review Patient A").wait_for()
        opts = {"bypass_csp": True, "viewport": {"width": W, "height": H}}
        if record:
            opts.update(record_video_dir=work, record_video_size={"width": W, "height": H})
        ctx = browser.new_context(**opts)
        page = ctx.new_page()
        tour = Tour(page, voice, fast=not record)
        page.goto(base)
        page.get_by_role("button", name="Sign in as Riley").wait_for()
        tour_script(tour, page, app, sam)
        if record:
            video = page.video.path()
        ctx.close()
        sam_ctx.close()
        browser.close()
    app._stop.set()
    srv.shutdown()
    return tour.timeline, video


def ts(sec):
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def build_outputs(timeline, video_src, voice_name):
    os.makedirs(OUT, exist_ok=True)
    vtt = os.path.join(OUT, "tutorial.vtt")
    with open(vtt, "w") as f:
        f.write("WEBVTT\n\n")
        for i, (a, b, ch, text, _) in enumerate(timeline, 1):
            f.write(f"{i}\n{ts(a)} --> {ts(b)}\n{text}\n\n")
    inputs, filters = ["-i", video_src], []
    for i, (a, _, _, _, wav) in enumerate(timeline, 1):
        inputs += ["-i", wav]
        ms = int(a * 1000)
        filters.append(f"[{i}:a]aresample=44100,adelay={ms}|{ms}[a{i}]")
    n = len(timeline)
    mix = "".join(f"[a{i}]" for i in range(1, n + 1)) + f"amix=inputs={n}:normalize=0,loudnorm=I=-16:TP=-1.5[aout]"
    mp4 = os.path.join(OUT, "tutorial.mp4")
    cmd = [FFMPEG, "-y", *inputs, "-i", vtt, "-filter_complex", ";".join(filters + [mix]),
           "-map", "0:v", "-map", "[aout]", "-map", f"{n + 1}:s",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "23",
           "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
           "-movflags", "+faststart", "-shortest", mp4]
    subprocess.run(cmd, check=True, capture_output=True)
    md = ["# Video tour transcript", "",
          f"Narrated walkthrough of the live practice demo (made-up patients). Voice: {voice_name}. "
          "Video: `tutorial.mp4` (subtitles built in, also `tutorial.vtt`). "
          "Regenerate with `.venv/bin/python -m scripts.make_tutorial`.", ""]
    last = None
    for a, b, ch, text, _ in timeline:
        if ch != last:
            md += ["", f"## {ch} ({ts(a)[3:8]})"]
            last = ch
        md.append(f"- **{ts(a)[3:8]}** {text}")
    with open(os.path.join(OUT, "TRANSCRIPT.md"), "w") as f:
        f.write("\n".join(md) + "\n")
    return mp4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="af_heart", help="Kokoro voice (af_heart, af_bella, am_michael, ...) or 'say'")
    args = ap.parse_args()
    voice = Voice(args.voice)
    work = tempfile.mkdtemp(prefix="tour-")
    t0 = time.time()
    run_pass(voice, record=False, work=work)          # pass 1: generate + cache every narration clip
    print(f"narration ready in {time.time() - t0:.0f}s", flush=True)
    timeline, video = run_pass(voice, record=True, work=work)
    mp4 = build_outputs(timeline, video, "Kokoro " + args.voice if args.voice != "say" else "macOS say")
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({"mp4": mp4, "seconds": round(timeline[-1][1], 1), "captions": len(timeline)}))


if __name__ == "__main__":
    main()
