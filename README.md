# Triage Evidence Worklist (EmoryHacks · Coder for Good)

An accessible, clinician-controlled worklist **and a training platform for nursing students, medical students and residents**, set in a **simulated** adult urgent-care facility. When a new or amended note arrives, Claude extracts source-quoted candidate evidence of what changed. A nurse verifies it against the original note, and only an authorized clinician can change the official care order.

> **Synthetic data only. Not a medical device. Not for clinical use.** All patients, notes, times, and staffing are fictional. "Demo signal A/B" are placeholders, not clinical findings.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install anthropic          # + playwright for browser tests
./scripts/run_demo.sh            # replay mode: no network, no credentials
# open http://127.0.0.1:8765 in two windows; sign in as Riley (RN) and Sam (Charge RN)
```

**New here? Watch the 5-minute video tour first:** [`docs/tutorial/tutorial.mp4`](docs/tutorial/tutorial.mp4) (narrated, with built-in subtitles; written transcript in [`docs/tutorial/TRANSCRIPT.md`](docs/tutorial/TRANSCRIPT.md)). It is a real recording of the app. Re-record it after UI changes with `.venv/bin/python -m scripts.make_tutorial` (needs `pip install playwright imageio-ffmpeg` and macOS `say`).

**The screen:** a left sidebar switches pages (Patients, Notes to review, Overdue, Practice tools, Help). Charts and notes open in a panel on the right while the list stays visible. **Pin** any note to keep it on screen as a sticky note.

**Possible emergencies:** clear, written rules (practice "demo red flags") flag a patient on the **Emergencies** page with a red banner and an urgent screen-reader alert that never includes the patient's name. **Any nurse** can check a flag and must write why it is or is not an emergency. The **charge nurse** sees those reasons and makes the final decision, which can be changed later. A flag never moves a patient by itself; a charge nurse's "confirmed emergency" moves the patient to Level 1. Every step is saved with a name, time, and reason.

**Patient chart (Epic-style tabs):** Summary · **Notes & documents** (every note, newest first, with a **search bar** that highlights matches) · **Write note** (type or **dictate**) · Emergencies.

**Voice (🎤 Voice, top right):** commands like "bring up patient A", "take a note for patient B", "search for denies in patient B", "open emergencies". There's a typed command box too, for browsers without speech recognition. **Dictated and typed notes stay on this server and are never sent to Claude.** The safety rule is that only approved practice notes may leave the server. Speech-to-text runs in the browser; some browsers (e.g. Chrome) send the audio to their own servers, so never dictate real patient information in this demo.

**First time? (about 3 minutes)**
1. Sign in as **Riley** (the highlighted button).
2. Follow the blue **"What to do next"** box at the top. It updates as you go (Step 1 of 5 → 5 of 5).
3. At Step 4, open a second browser window to the same address and sign in as **Sam** to approve the change.
4. Use the **A / A+ / A++** buttons (top right) if the words are small, and the **Help & words** page in the sidebar for the glossary.
5. Start over at any time: sidebar → **Practice tools** → **Reset all demo data**.

| Task | Command |
|---|---|
| Live synthetic mode (calls Claude) | `export ANTHROPIC_API_KEY=...` in your shell, then `./scripts/run_demo.sh live` |
| Reset fixtures | `./scripts/run_demo.sh reset`, or "Reset all demo data" in Demo controls |
| All tests | `.venv/bin/python -m unittest discover -s tests -t .` |
| Browser tests only | `.venv/bin/pip install playwright && .venv/bin/python -m playwright install chromium && .venv/bin/python -m unittest tests.test_browser` |
| Baseline vs Claude eval | `.venv/bin/python -m scripts.evaluate --mode replay` (or `--mode live`) |
| Record real replays | `.venv/bin/python -m scripts.record_replays` (live; writes provenance `captured_live`) |

Configuration (environment only; nothing secret in the repo): `TRIAGE_MODE` (`replay`|`live`), `TRIAGE_MODEL` (default `claude-opus-5-5`), `TRIAGE_EFFORT` (`medium`), `TRIAGE_PORT` (8765), `TRIAGE_DEMO_CONTROLS` (1).

More settings: `TRIAGE_DEPLOYMENT` (`demo`|`education`|`clinical`), `TRIAGE_DEMO_LOGIN` (1 = one-click demo sign-in), `TRIAGE_DATA_KEY` (Fernet key; encrypts notes and AI payloads at rest. Make one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`), `TRIAGE_BAA_CONFIRMED` (only with an executed BAA), `TRIAGE_SESSION_IDLE_MIN` (30), `TRIAGE_LOCK_MIN` (3), `TRIAGE_AI_RETENTION_DAYS` (7), `TRIAGE_FHIR_BASE_URL`, `TRIAGE_FHIR_TOKEN`, `TRIAGE_FHIR_ORG`. In `clinical` mode the server refuses to start unless encryption, real accounts, HTTPS cookies and no demo controls are set. The browser never receives the key and never calls Anthropic.

**Latest results (2026-11-17):** 88/88 tests pass (unit, integration, HTTP, privacy canaries, accounts, education scoring, FHIR connector against a fake EHR, headless Chromium). **Live Claude was not called**: no credentials were available while building. Every model result so far is a labeled replay of an authored fixture. See `docs/07-test-report.md`.

## Three ways to use it

| Mode | Who | What it does |
|---|---|---|
| **Education** (ready to pilot) | Students, residents, instructors | **Learning hub** (`/learn.html`): scenario library, private practice sandboxes, automatic scoring with feedback, **student Case Builder** (add your own patients, timed notes and emergencies), instructor review, class results |
| **Demo** (default) | Anyone | The full worklist with synthetic patients and one-click demo sign-in |
| **Clinical** (future, with a hospital partner) | Nurses, charge nurses, providers | Real accounts only, encryption required, read-only **FHIR R4** EHR connector, real notes sent to AI only with a confirmed BAA |

**Sign up:** anyone can create a free learner account (**Create account** on the sign-in page). We email a 6-digit activation code; the account works after the code is entered (codes expire in 15 minutes, 5 tries, new code after 60 seconds, 10 sign-ups per network per hour). After signing in, learners see the **training users** (practice as Riley the staff nurse, Sam the charge nurse, or Dr. Lee the resident, each in a private copy of the unit), the scored scenarios, and **Add a patient / Add a note or emergency** buttons during any practice.

**Email setup:** set `TRIAGE_SMTP_HOST`, `TRIAGE_SMTP_PORT` (587), `TRIAGE_SMTP_USER`, `TRIAGE_SMTP_PASSWORD`, `TRIAGE_SMTP_FROM`. Any provider works (Gmail with an app password, Brevo, Resend, SendGrid, Mailgun). Without them, codes are printed in the server console, which is only useful for local testing. `TRIAGE_SIGNUP=0` turns public sign-up off.

Seeded accounts (password `demo`, sign in by username): **riley** (RN), **sam** (charge RN), **jordan** (RN, other unit), **drlee** (provider), **alex** (admin), **taylor** (nursing student), **avery** (medical student), **morgan** (instructor).

**Practice in 4 steps (students):** sign in as Taylor → **Start practice** on a scenario → work the board, pressing **Next event** to receive new notes → **Finish and see my score**. Build your own case under **My cases**, practice it, then **Submit to class**; an instructor (Morgan) approves it into the class library. The case builder blocks things that look like real patient information, and student cases are sent to the AI only after approval.

**Privacy features:** password accounts with lockout and idle sign-out, **Lock** screen and auto-lock, **Hide names**, per-chart **access log**, **break-the-glass** with a written reason for restricted records, a **Privacy** page showing exactly what was sent to the AI, AI-text retention purge, encryption at rest (`TRIAGE_DATA_KEY`), and patient identifiers scrubbed from anything that could reach the AI.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the path from classroom pilot to hospital pilot, and [docs/OUTREACH.md](docs/OUTREACH.md) for a one-pager and emails to schools and hospitals.

## Deploy (Render, free)

1. Push this repo to GitHub.
2. On [render.com](https://render.com): **New → Blueprint**, then pick this repo. Render reads `render.yaml`.
3. When asked for **`ANTHROPIC_API_KEY`**, paste your Claude API key. It's stored as a secret on Render and **never** goes in the code.
4. Deploy, then open the `https://…onrender.com` link Render gives you.

Notes: the free tier sleeps when idle, so the first visit takes about a minute, and demo data resets on each restart. The hosted app runs in **live** mode, so every practice note calls Claude and uses your credits. Set a monthly spend limit in the [Claude Console](https://console.anthropic.com) under Settings → Limits. Only the approved practice notes can ever be sent to Claude. Anything typed or dictated stays on the server.

## Capability status

Legend: ✅ implemented and tested · 🟡 implemented, untested · 📝 proposed · 🏥 requires facility validation

| Capability | Status |
|---|---|
| Note intake from fixture IDs only (no paste or upload path) | ✅ |
| Excerpt selector (sentence window, header exclusion, abstain on overflow, earlier-note comparison) | ✅ |
| Identifier-shaped token scrubber (not de-identification) | ✅ |
| Server-side fixture allowlist and outbound feature allowlist | ✅ |
| Claude Messages API call (structured outputs, no tools, pinned model) | 🟡 code path exists; **live call never executed** |
| Replay mode with provenance shown on screen | ✅ |
| Validator: schema, verbatim quotes, alias binding, comparison check, full-sentence context warnings | ✅ |
| Stale-response guard (amendment, transfer) | ✅ |
| Official queue ordering rule and FIFO evidence inbox | ✅ |
| Clinician accept / correct / reject with history | ✅ |
| Priority confirmation restricted to an authorized role, with version check | ✅ |
| Immutable deadlines, overdue escalation to backup, accept → reached → complete | ✅ |
| Manual escalation independent of AI | ✅ |
| Transactional outbox, SSE replay, client dedupe | ✅ |
| Focus-preserving live reorder; Enter opens the focused encounter | ✅ (headless Chromium) |
| Generic announcements; grouping; reconnect notice | ✅ live-region text · 🟡 grouping under surge, reconnect |
| Draft preservation during updates | ✅ (Chromium) |
| Access revocation clears the screen | ✅ (Chromium) |
| VoiceOver + Safari behavior, magnifier use | 🏥 not tested |
| Content-free logs and error reports (incl. failed-request path) | ✅ |
| Cross-org authorization (reads, writes, SSE, AI-request panel) | ✅ |
| TLS, real identity provider / MFA, encrypted backups | 📝 |
| Heartbeat-based EHR feed monitoring | 📝 (prototype uses silence detection) |
| Protocol values, confirm roles, watched terms | 🏥 |
| BAA, HIPAA-ready org, vendor checklist | 🏥 |
| FDA CDS determination | 🏥 |
| Clinical performance | 🏥 (see validation plan) |

## Documents

1. [Manager's memo](docs/01-manager-memo.md)
2. [Research, sources, and design decisions](docs/02-research.md)
3. [Privacy impact assessment, data flow, HIPAA matrix](docs/03-privacy-assessment.md)
4. [Vendor eligibility checklist](docs/04-vendor-checklist.md)
5. [Operating model, responsibility map, architecture, failure walkthroughs, approvals](docs/05-operating-model.md)
6. [Risk register](docs/06-risk-register.md)
7. [Test report](docs/07-test-report.md)
8. [Validation plan](docs/08-validation-plan.md)
9. [Two-minute demo script](docs/09-demo-script.md)
10. [If Claude disappears mid-shift: exact path](docs/10-ai-off-path.md)
11. [Roadmap: hackathon → classroom → hospital](docs/ROADMAP.md)
12. [Outreach kit: one-pager and emails](docs/OUTREACH.md)

## Code map

`app/excerpt.py` local minimization · `app/prompt.py` frozen prompt and PHI-free schema · `app/adapter.py` the only outbound path (gates, live/replay) · `app/validate.py` source checks · `app/services.py` workflow, authorization, deadlines · `app/db.py` schema (immutable-deadline trigger, outbox, audit) · `app/server.py` HTTP, SSE, security headers · `static/` accessible client · `fixtures/` synthetic seed, replays, gold labels · `tests/`.
