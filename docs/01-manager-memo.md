# Memo: AI-assisted evidence review for the urgent-care worklist

**To:** Facility leadership, clinical lead, privacy officer, security reviewer, accessibility specialist
**From:** Healthcare operations (prototype owner)
**Date:** 2026-11-14
**Status:** Synthetic prototype. Not approved for patient data or clinical use.

**The patient-care problem.** Waiting patients change. The change often appears first in a new or amended note: "Patient *now* reports..." after an earlier denial. A nurse tracking several reassessments can't reread every chart each time a note is signed, so a reversal can sit unread while the official queue still reflects the old picture. The risk is a patient waiting at the wrong priority because evidence nobody had time to read.

**The accessibility barrier.** Live worklists usually move rows and shift focus when data changes. For a nurse using VoiceOver, that is dangerous: pressing Enter on "Patient A" can open whichever patient slid into that position. Pop-ups that read a patient's details aloud also broadcast private information on a shared workstation. In our build, incoming data never moves focus, the focused patient stays the same element, and automatic speech is limited to "New evidence awaiting clinical review. N unreviewed." Headless browser tests confirm the focus behavior. **Real VoiceOver testing with nurses is still owed.**

**What Claude contributes.** One narrow job: read a short excerpt of the new note (plus a bounded excerpt of the earlier note), quote the exact supporting words, and state who the statement is about, whether it is reported or denied, when it applies, and what changed. Everything it returns is labeled "AI-assisted, unverified." Claude cannot change priority, choose which chart is updated, send messages, or reach any system. A charge nurse confirms any priority change. Our honest finding so far: a 2001-style negation rule already handles "denies" well. Claude's possible value is in **subject, timing, and changes across notes**, where both baselines failed (for example, the mother's history read as the patient's symptom). We have **not yet measured Claude live**: the build environment had no credentials, so the demo runs labeled replays. That measurement is the first next step. It also matters that exact quotes are not enough: a quote that drops "denies" is still verbatim. That is why the full sentence and a correction control sit next to every finding.

**The largest privacy risk.** Not the vendor call itself, which we minimized: no names, MRNs, dates, or other patients, and identifier-shaped tokens are scrubbed. Tests confirm the synthetic canaries don't leak. The largest risk is **copies outside the approved path**: an API key from the wrong organization, SDK debug logs, an error-reporting tool, or a staff member pasting a note into a chat tool. Some of those are human behaviors that software can't fully block. On the vendor side, published terms leave open questions that only an executed BAA can settle: retention (30 days vs "not retained by default," which conflict in Anthropic's own pages), human access, and whether our exact model and features are covered. Anthropic's Usage Policy (effective Nov 12, 2026) treats "setting triage or waitlist priority" as high-risk. That requires a qualified human reviewer, and AI disclosure to the patient.

**What technical controls cannot fix.** A patient who deteriorates without anyone writing it down generates no evidence at all. This tool can't see that patient, and that miss may stay invisible. Bedside reassessment deadlines, which run independently of notes and of AI, remain the safeguard.

**The next decision for the facility:** whether to fund a **silent pilot** (AI runs out of sight next to normal care, adjudicated by two clinicians) under an executed BAA, *after* five sign-offs:
1. The privacy officer approves the excerpt window and the vendor checklist.
2. The security reviewer approves the route, keys, and logging.
3. A regulatory specialist gives the FDA clinical-decision-support assessment for this time-sensitive use.
4. The clinical lead sets the protocol values and who may confirm priority.
5. The accessibility specialist completes VoiceOver testing with nurses.

If any of these isn't ready, the right decision is to keep the AI path off. Every workflow in the app runs without it.
