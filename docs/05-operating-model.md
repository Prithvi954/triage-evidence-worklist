# 05. Clinical operating model, architecture, and failure handling

All times, deadlines, and staffing figures are **illustrative** and need clinical lead approval.

## 1. Responsibility map

| Stage | Responsible | Accountable | Backup | What "done" means |
|---|---|---|---|---|
| Arrival / unassessed | Queue owner (charge RN) until assigned | Charge RN | Clinical lead | Initial assessment obligation completed |
| Assessment, priority | Assigned RN proposes; **charge RN (qualified role) confirms** | Charge RN | Provider on duty | `priority_confirmed_by` recorded |
| Evidence review (each new or amended note) | Patient's primary RN (or covering RN if on break) | Charge RN | Charge RN gets overdue escalation | Review decision recorded |
| Action after a priority change (Level 1 bedside review) | Primary RN accepts | Charge RN | Charge RN (overdue escalation) | Accepted → **reached patient** → completed (three separate timestamps) |
| Reassessment | Primary RN | Charge RN | Covering RN / charge RN | Obligation completed |
| Manual escalation | Any RN raises it | Charge RN responds within 5 min | Clinical lead | Response obligation completed |
| Transfer / closure | Provider and RN | Charge RN | n/a | Encounter closed. Open obligations stay visible until closure is confirmed. |
| AI or feed outage | IT on-call restores | Charge RN runs the downtime process | Clinical lead | Every note manually reviewed |
| Privacy event | Reporter tells charge RN | Privacy officer | Security reviewer | Breach assessment documented |

**Breaks and shift change:** an RN on break must name a covering RN (`covered_by`). Owner text then reads "Jordan, on break, covered by Sam." If no cover is named, the text reads "NOT COVERED" and the charge RN owns the work. **Unaccepted handoff:** responsibility stays with the sender until the receiver accepts; the obligation shows "Not accepted." **Receiving an alert ≠ accepting responsibility ≠ reaching the patient ≠ completing the action.** Each has its own field and is shown separately.

**Scarce staffing:** if several patients need immediate care at once, the charge RN uses manual escalation reason `staffing_capacity`. The facility leader's protocol then applies (call in staff, divert, or transfer). The app does not decide who waits. It keeps every obligation and its original deadline visible.

## 2. Separate fields (never merged)

`notes`/`review_items` (note-processing state: `queued → in_progress → findings | no_qualifying_finding | ... | failed_* | stale_discarded`), `findings` (proposed evidence, AI), `decisions` (clinician interpretation), `encounters.priority` (clinician-confirmed priority), `obligations` with `kind ∈ {initial_assessment, reassessment, note_review, bedside_review, escalation_response}`, plus `due_at` (immutable), `owner`, `accepted_by/at`, `reached_at`, `completed_at`, `escalated_to/at`, and `escalations` (manual escalation status).

## 3. What orders each screen

- **Care queue (official order):** `(confirmed priority level; unassessed = 1.5, i.e. after Level 1 and before Level 2) → earliest open deadline → arrival time → encounter ID`. **AI output is not an input.**
- **Evidence inbox:** `note received time (oldest first) → item ID`. Every new or amended note appears, whether AI succeeded, failed, abstained, or found nothing. The AI status is a text label only.
- **Overdue actions:** `due time (oldest first)`.

**Does model output still influence access to care?** Yes, indirectly. The inbox order is model-independent, but a label like "candidate evidence ready" next to "no qualifying finding" can steer which note a nurse opens first. That is a real attention effect, and it can delay review of a note the model misread. Mitigations: FIFO order, a visible unreviewed count, a per-note review deadline that **does not depend on AI output**, overdue escalation to the charge RN, and manual escalation that never waits. A silent pilot should measure this effect (`08-validation-plan.md`).

**Worked example (demo start, 10:00):**

| Pos | Patient | Priority | Earliest deadline | Why here |
|---|---|---|---|---|
| 1 | D | Unassessed | Initial assessment 10:13 | Rank 1.5 |
| 2 | A | Level 2 | Reassessment 10:30 | Level 2 |
| 3 | C | Level 3 | Reassessment **09:55 (overdue)** | Ties with B on Level 3; earlier deadline wins |
| 4 | B | Level 3 | Reassessment 10:50 | |

After Sam confirms B → Level 1: **B, D, A, C**. A's reassessment stays due at 10:30.

## 4. Architecture

Single Python process (stdlib HTTP server and worker thread) with SQLite. The browser talks only to the backend. The backend is the only component with Anthropic credentials. See the data-flow diagram in `03-privacy-assessment.md`.

**Key routes** (all require a session; object-level authz returns 404 for other orgs and unknown IDs alike):

| Route | Purpose | Authorization |
|---|---|---|
| `GET /api/state` | Queue, inbox, overdue, feed, mode | Clinical role; own org |
| `GET /api/encounters/{id}` | Encounter detail and notes | Own org |
| `GET /api/review-items/{id}` | Evidence and original note | Own org |
| `GET /api/review-items/{id}/ai-request` | "What Claude received" (approved fixtures only) | Own org; fixture-hash present |
| `POST /api/review-items/{id}/decision` | Accept / correct / reject | Clinical role; item version check |
| `POST /api/encounters/{id}/priority` | **Only path that changes care order** | `charge_rn`; encounter version check |
| `POST /api/encounters/{id}/escalate` | Manual escalation | Clinical role |
| `POST /api/obligations/{id}/{accept,reached,complete}` | Task lifecycle | Clinical role; atomic conditional update |
| `GET /api/events` | SSE: IDs and generic text; replay via `Last-Event-ID` | Re-checks session on every wake |
| `/api/sim/*` | Demo-only EHR feed, clock, faults | `TRIAGE_DEMO_CONTROLS=1` |

**Transaction boundaries:** intake (note + item + note-review obligation + job + audit + outbox) is one transaction. Model-result commit (stale re-check + findings + item state + audit + outbox) is one transaction. A priority change (conditional version update + change record + new obligation + audit + outbox) is one transaction. Notifications use a **transactional outbox** and are dispatched after commit.

## 5. Failure walkthroughs

| Failure | Durable state | Staff see / hear | Responder | Retry | Reconciliation |
|---|---|---|---|---|---|
| **Model succeeds after the note was amended or the patient transferred** | Call stored as `stale_discarded`; no findings attached; v1 item `superseded`; both note versions kept | v2 item in the inbox. The open v1 panel shows "This note was ... amended. Your draft is kept." | Primary RN reviews v2 | v2 gets its own job | `test_amended_while_in_flight...`, `test_transferred_patient_result_discarded` |
| **Worker crashes after a priority commit, before notification** | Priority, obligation, and audit committed; outbox row `delivered_at IS NULL` | On restart the dispatcher sends it. Clients reconnect with `Last-Event-ID` and refetch | IT on-call if repeated | Outbox redelivers; client dedupes by `seq` | `test_crash_after_priority_commit_before_notification` |
| **Two clinicians accept the same task / stale downgrade** | Atomic `UPDATE ... WHERE accepted_by IS NULL`; version-checked priority | Second person: "Already accepted by another clinician" / "Priority changed since you loaded this patient" | n/a | No auto-retry; the user reloads | `test_two_clinicians_accept_same_task`, `test_stale_downgrade_rejected` |
| **Authorization lost with a panel or stream open** | Sessions deleted; user inactive | Stream sends `access_revoked`; client clears all patient content; "Your access has ended" is announced | Admin | n/a | `test_revocation_ends_session`; headless Chromium `test_access_revoked_clears_patient_content` |
| **EHR feed stops while the dashboard loads normally** | `feed.last_message_at` stops advancing | After 15 min (illustrative): "EHR feed may be stalled. Data may be out of date" in the status bar and announcement | IT on-call; charge RN starts downtime checks | n/a | `test_feed_stall_detected`. *Limit:* silence detection can't tell a quiet hour from a dead feed. A production feed needs heartbeats. |
| **Anthropic unavailable or rate-limiting while urgent patients wait** | Job retries with backoff (30 s, 60 s), then `failed_*`; deadlines untouched | Inbox row: "AI service unavailable; manual review required." Inbox banner: "N note(s) need manual review" | Primary RN reviews manually; charge RN watches note-review deadlines | 3 attempts | `test_provider_failure_preserves_care_and_manual_paths` |
| **Nurse accepts a task but never reaches the patient** | `accepted_at` set, `reached_at` null; `due_at` unchanged | At due time: escalated to the charge RN, shown in Overdue with "Accepted by Riley ... Not recorded" | Charge RN | n/a | `test_accepted_but_never_reached...` |

**Invariant:** no model outage, duplicate event, or worker restart clears an obligation or resets its deadline. A DB trigger rejects any `due_at` update (`test_deadline_cannot_be_reset`). A duplicate EHR event is a no-op (`test_duplicate_ehr_event_idempotent`).

## 6. Decisions that need a real person's approval

| Decision | Approver |
|---|---|
| Watched terms; which priority levels exist; Level 1 bedside window; note-review window; where unassessed patients sort | **Clinician / clinical lead** |
| Who may confirm a priority change (role list), and whether a nurse's AI-informed escalation needs provider co-sign | **Clinical lead + facility leader** |
| Whether AI evidence is part of the designated record set; retention of excerpts and responses; earlier-note window | **Privacy officer** (with HIM) |
| Patient-facing disclosure that AI assisted (Usage Policy requires it for High-risk AI Recommendations; may also be state law) | **Privacy officer + counsel + facility leader** |
| Announcement wording, verbosity, and focus behavior; VoiceOver and magnification sign-off | **Accessibility specialist** (with nurse users who use assistive technology) |
| Vendor route, BAA, key management, egress, logging, threat model | **Security reviewer** |
| FDA device determination; labeling and intended-use statement | **Regulatory specialist** |
| Go-live, suspension authority, staffing for downtime | **Facility leader** |
