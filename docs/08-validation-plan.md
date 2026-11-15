# 08. Validation plan for any future clinical use

Nothing in this prototype is validated for clinical use. The plan has phases, and each phase needs a go/no-go decision from the clinical lead and the facility leader.

## 1. Independent clinical adjudication (offline, retrospective)
- Sample notes from real encounters **inside a BAA-covered environment** after privacy officer approval. Stratify by note type, author role, amendments, and copied-forward text.
- Two clinicians independently label each watched term (subject, assertion, time, change vs earlier note). A third resolves disagreements. Report inter-rater agreement.
- Compare Claude (pinned model, prompt, schema, selector versions) with the phrase-match and NegEx baselines on the same notes. Report per field: sensitivity for "patient, reported, current," false-positive rate, and **exact-quote-but-wrong-interpretation rate**.
- Pre-register acceptance thresholds before looking at results.

## 2. Silent pilot alongside existing care (prospective, no clinician sees AI output)
- Run extraction in shadow mode on live notes. Clinicians work as usual. Adjudicators review a sample daily.
- Measure: **missed findings** (adjudicated change with no AI candidate), **false alerts** (candidate rejected by the adjudicator), latency from note signing to AI result, failure and abstention rates, and stale discards.
- **Documentation delay:** time from observed change (bedside or vitals) to note signing. This is the window the tool cannot see (risk R3).

## 3. Supervised pilot (clinicians see AI evidence)
- Start with one unit and limited hours, with an extra charge nurse as safety net.
- Outcomes: time from note signing to review decision; time from confirmed priority change to patient reached; reassessment on-time rate; manual escalation counts; **clinician workload** (reviews per hour, NASA-TLX or similar); override and correction rates; **attention bias** (time-to-review by AI label vs the FIFO position).
- Patient outcomes are secondary and underpowered at pilot scale. Avoid claiming them.

## 4. Subgroup performance (Section 1557 "reasonable efforts" duty, status to be confirmed by counsel)
- Report extraction errors and time-to-review by age band, sex, race and ethnicity (where recorded), preferred language and interpreter use, disability, and note author role. Check whether notes written with interpreters or in non-standard English are misread more often.

## 5. Suspension authority and restart
- **Who can suspend:** the clinical lead, privacy officer, or security reviewer, each acting alone. Suspension is setting `TRIAGE_MODE=replay` (or AI off). Every workflow continues without AI.
- **Automatic suspension triggers** (illustrative thresholds, set before the pilot): missed-finding rate above threshold over a rolling window; any confirmed privacy incident on the AI path; any vendor configuration change not reviewed; validator rejection rate spike; repeated stale or failed states.
- **Restart criteria:** root cause documented; fix verified on the adjudicated set; privacy and security sign-off; staff briefed; restart with a new silent-pilot period.

## 6. Accessibility testing with intended users
- Recruit nurses who use **VoiceOver on macOS (Safari)**, plus at least one JAWS or NVDA user if the facility uses Windows, and one magnifier user (200–400% zoom, macOS Zoom).
- Tasks: the two-stage signature scenario; finding unreviewed notes, failed AI calls, overdue actions, and owners; manual escalation; correcting a finding; returning to the previous patient; recovering after a disconnect.
- Record what was actually spoken (screen-reader speech viewer or recording), task completion, errors, and time. **The accessibility specialist signs off.**

## 7. Change control
Any change to model, prompt, schema, selector, ordering rules, feature list, endpoint, or organization repeats §1 on the adjudicated set before release. Version identifiers are stored on every model call for comparison and rollback.
