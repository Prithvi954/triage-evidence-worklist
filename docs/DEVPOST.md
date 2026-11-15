## Inspiration

In urgent care, a patient's situation can change while they wait, and the first sign is often a single new line in a note. At triage: *"Patient denies demo signal A."* An hour later: *"Patient now reports demo signal A."* A nurse tracking several patients and their reassessment deadlines can't reread every chart each time a note is signed.

Then we thought about the nurses who use a **screen reader**. Live worklists are hard for them. Rows jump when the list re-sorts, so pressing **Enter** can open the *wrong patient*. Pop-ups read patient details aloud on a shared workstation. The Coder for Good challenge put it well: *over a billion people live with a disability, and when products are designed with accessibility in mind, they work better for everyone.*

So we designed around one person: **Riley, a nurse who uses a screen reader and a keyboard.** Every feature had to work for her first.

## What it does

**A.I Triage System** (the app is called the *Triage Evidence Worklist*) is a clinician-facing worklist for a *simulated* urgent-care unit (all patients are made up):

- **A note arrives → Claude finds the evidence.** Claude reads a short excerpt and returns the **exact quotes**: who the statement is about, whether it's reported or denied, when it happened, and what changed since the earlier note. Everything is labeled **"AI-assisted, unverified."**
- **A nurse checks it** against the original note, one click away, and can accept, correct any field, or reject.
- **Only an authorized charge nurse can change who is seen first.** The AI can't reorder patients.
- **The list updates live on every screen, and Riley's place never moves.** If the list reorders while she's on Patient A, Enter still opens Patient A.
- **Possible emergencies:** clear, written rules flag a possible emergency. **Any nurse** can check it and **must write why** it is or isn't an emergency. The **charge nurse** reads those reasons and makes the final call, which can be changed later. A confirmed emergency moves the patient to Level 1, and every step is saved with a name, time, and reason.
- **An Epic-style chart:** tabs for Summary, Notes & documents (with a **search bar** that highlights matches), Write note, and Emergencies.
- **Voice:** commands like *"bring up patient A"*, *"take a note for patient B"*, *"search for denies"*, plus **dictation** for notes.
- **Built for everyone:** private screen-reader announcements (*"New evidence awaiting clinical review"*, never a name or symptom), A/A+/A++ text sizes, a one-screen layout with a sidebar, **sticky notes** you can pin, a step-by-step "What to do next" guide, and a 5-minute narrated video tour.
- **It works without AI.** If Claude is down, every note still appears as "manual review required," and no deadline moves.

## How we built it

- **Backend:** Python (standard-library HTTP server) with **SQLite**. A durable job queue for AI calls, a transactional outbox, and **server-sent events** for live updates. A database trigger makes deadlines **immutable**, so an outage, retry, or crash can never reset one.
- **Claude:** the official `anthropic` Python SDK calling the **Messages API** (`claude-opus-5-5`) with **structured outputs** and **no tools**. The model, prompt, schema, and selector versions are saved with every call.
- **Privacy by design:** before anything leaves the server we pick the smallest useful excerpt (the matched sentence plus one neighbor), drop names, record numbers and dates, scrub codes and links, and use random aliases. A server-side **allowlist** means only approved practice notes can ever be sent. Typed and dictated notes **never** go to the AI. The JSON schema contains no patient data.
- **A validator** checks every quote word for word against the stored note, shows the **full sentence** (an exact quote can still be misread, for example when it drops "denies"), flags context like *mother* or *last week*, and throws away a result if the note was amended mid-call.
- **Frontend:** plain HTML, CSS, and JavaScript with native elements, ARIA tabs, one polite status region plus one urgent region reserved for emergencies, and a keyed renderer that **never detaches the focused row** during a reorder.
- **Testing:** **57 automated tests** (unit, integration, privacy, HTTP, and real-browser tests with Playwright and Chromium), including a two-session test proving that Enter opens the right patient after a live reorder.
- **Extras:** a narrated tutorial recorded from the live app, with an offline neural voice (Kokoro) and built-in subtitles, plus research, privacy-assessment, and risk-register docs.

## Challenges we ran into

- **Keeping focus during a live reorder.** Moving the focused DOM node makes the browser drop focus. We rebuilt the list *around* the focused row instead of moving it.
- **"An exact quote" ≠ "the right interpretation."** A model can quote *"demo signal A at rest"* and drop the word *"denies"* before it. The quote passes a word-for-word check, so we added full-sentence context checks and made correction easy.
- **Privacy leaks in unexpected places.** Our planted fake identifiers ("canaries") caught a real leak: a wristband code in a neighboring sentence was being sent to the AI. We added a scrubber and re-tested across 9 destinations, including the error path (0 leaks).
- **Announcements that help without oversharing.** Spoken alerts must be useful but must never read a patient's name aloud on a shared screen.
- **Being honest about limits.** Rule-based emergency alerts may count as a medical device under FDA guidance, browser speech recognition can send audio to the browser company, and real patient data would need a signed business associate agreement. We documented all of it instead of hiding it.

## Accomplishments that we're proud of

- A screen-reader user's Enter key **always opens the patient they were on**, proven in a real browser with two users.
- **0 privacy leaks** across 9 places data could go, and every workflow still works with the AI turned off.
- A clear line of authority: **the AI suggests, people decide**, with reasons recorded at every step.

## What we learned

- Accessibility decisions are also **safety** decisions: a focus jump can mean opening the wrong patient.
- The useful AI job here isn't "decide who's sickest." It's **"show me the exact words that changed."** A simple 2001-style negation rule already handles "denies" well, so AI earns its place on **who** a statement is about, **when** it happened, and **what changed across notes**.
- Privacy needs testing, not just intentions. The canary tests found a leak we didn't expect.

## What's next for A.I Triage System

- Measure Claude live against our keyword baselines on a larger set reviewed by clinicians.
- Test with nurses who use VoiceOver every day.
- Clinical review of the emergency rules, and a regulatory assessment of the emergency alerts.
- A signed business associate agreement and privacy and security sign-off before any real patient data, then a silent pilot alongside normal care.
