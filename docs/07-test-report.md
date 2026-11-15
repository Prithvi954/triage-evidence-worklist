# 07. Test report (what actually happened)

Run on 2026-11-14 04:23 UTC, macOS (Darwin 24.6), Python 3.12.2, `anthropic` SDK 2.8.0, Playwright 1.65.2 with headless Chromium. Command: `.venv/bin/python -m unittest discover -s tests -t .`. **Result (latest run, 2026-11-14): 57 tests, all passed.** (The original 47 plus emergency flags, chart notes, layout, guide, search, and typed voice commands.)

**What these tests can and cannot show.** Unit and integration tests exercise our code against a controllable clock and SQLite. Headless browser tests observe the DOM and the **accessibility tree**, not speech. **No test here establishes what VoiceOver speaks.** All model outputs came from **replay** (authored fixtures). **The live Claude API was not called**: the build environment had no `ANTHROPIC_API_KEY` and no `ant` CLI profile. A synthetic demo cannot establish clinical performance or HIPAA compliance.

| Test (brief §9) | Kind | Expected | Actual | Evidence | Unresolved limitation |
|---|---|---|---|---|---|
| Claude contributes something useful | Eval script | Baselines vs Claude on the same notes | Baselines measured: phrase match assertion 6/9, NegEx-lite 9/9; both 3/9 on change, 7/9 on time. **Claude not measured** (no credentials). | `scripts/evaluate.py`, `data/eval_replay.json` | Run `--mode live`. Even then: 9 synthetic findings, so this shows differences, not performance. |
| One correct contextual interpretation and one failure | Unit | Family-member subject handled; dropped "denies" flagged | Family member grouped `not_about_patient`. A verbatim quote missing "denies" **passes** quote checks and is caught only by the full-sentence warning. | `test_family_member_kept_out_of_patient_current`, `test_exact_quote_is_not_correct_interpretation_dropped_denies` | Warnings cover listed cue words only |
| Data minimization works | Integration | No header, other-patient, or other-clinic canary in the outbound request | Outbound contained none of the 4 canaries (the passage canary was scrubbed as identifier-shaped) | `test_canary_matrix`, `test_other_patient_canary_never_in_b_request` | Scrubber is regex; not de-identification |
| Model output cannot alter care order | Integration | Valid, malformed, no-finding, and injection outputs leave order and deadlines unchanged | Unchanged in every case. Requests carry no tools. | `test_valid_malformed_and_malicious_outputs_never_change_order`, `test_malformed_and_malicious_outputs` | n/a |
| Clinician disagrees | Integration | Correction stored; source kept; not reopened | Correction `{assertion: denied}` stored; second decision → 409; warnings retained | `test_clinician_rejects_and_item_not_reopened` | n/a |
| Live reorder during keyboard use | **Headless browser**, 2 sessions | Focus stays on A's node; Enter opens A; B reachable | Same DOM node focused after reorder (B moved from 4th to 1st). Enter opened "Patient A, encounter", "Encounter ID enc_A", "Reassessment: due 10:30". Back returned focus to A; Tab from the heading reached B. | `test_signature_two_stage_flow`, `data/browser_queue_aria.txt` | Chromium only; Safari and VoiceOver not run |
| Spoken output protects privacy | Browser (live region text) + unit | Generic text; no patient label or clinical words | Live region: "New evidence awaiting clinical review. N unreviewed in evidence inbox." No "Patient B", no "demo signal", no canary. | `test_signature_two_stage_flow`, `test_announcements_are_generic` | **Speech not verified** |
| Source changes during inference | Integration | Stale result not active | `stale_discarded`; no findings on v1; v2 queued; both versions kept | `test_amended_while_in_flight_old_response_not_current`, `test_transferred_patient_result_discarded` | n/a |
| Provider call fails | Integration | Pending manual review visible; deadlines and escalation continue | Timeout, rate limit, and outage each → `failed_*` after 3 attempts, still unreviewed, `ai_needs_manual ≥ 1`. Deadlines unchanged; escalation and manual review succeeded. | `test_provider_failure_preserves_care_and_manual_paths` | Live SDK exception mapping is not exercised against the real API |
| Task accepted but care incomplete | Integration | Deadline expires; backup finds patient | Escalated to Sam; overdue row shows Patient B, "Accepted by Riley", not reached; original due time; one overdue event despite two ticks | `test_accepted_but_never_reached_escalates_to_backup_with_original_deadline` | n/a |
| One clinic requests another's record | Integration + HTTP | No leak | 404/403 on 9 service paths and 4 HTTP paths; other-clinic state contains no North IDs or labels; SSE events org-filtered | `test_cross_org_access_denied_everywhere`, `test_two_sessions_live_reorder_and_org_isolation` | Export: none exists to test |
| Sensitive canary enters an error path | Integration | Error report holds class only | Error report: `RuntimeError` + item ID. No canary or note text in error reports or the app log. Item → `retrying`. | `test_failed_request_path_does_not_leak` | Destinations **not inspected**: Anthropic-side logs, OS/terminal scrollback, browser devtools, macOS crash reports, any proxy |

**Additional tests:** CSRF header required (403); body > 16 KB → 413; path traversal and other-org IDs → 404; no paste/upload/extract endpoints exist (404); unknown fixture → 404; modified fixture → `blocked_not_approved_fixture` with **zero** outbound requests; unapproved features (`tools: web_fetch`, `mcp_servers`, `thread`, `fallbacks`) → `BoundaryViolation`; deadline update → DB trigger abort; duplicate EHR event → no new item, no deadline change; crash between priority commit and notification → outbox redelivered by a new process; two clinicians accept → 409; stale downgrade → 409; feed silence 16 min → stale; revocation clears the screen and moves focus to the "Your access has ended" heading (browser); draft text survives an incoming update (browser).

## Later features (emergencies, chart, voice)

| Test | Kind | Result |
|---|---|---|
| Rules fire only on affirmed, current, patient statements (not denied / family / past) | Unit | ✅ `test_rule_fires_only_on_affirmed_current_patient_statements` |
| Rule flag creates check + decision tasks, moves nobody | Integration | ✅ |
| Nurse check requires a written reason; any nurse may check; stale version → 409 | Integration | ✅ |
| Only charge nurse decides; "confirmed" → Level 1 + bedside task; can be re-decided; later "not emergency" never silently downgrades | Integration | ✅ |
| Other clinic can't see or act on flags | Integration | ✅ |
| Nurse-written / dictated notes are saved, run emergency rules, and **never reach the AI adapter** | Integration | ✅ |
| Full flow in two browsers: urgent alert with no name, nurse check with reason, charge decision, B→Level 1 on nurse's screen | Headless Chromium | ✅ |
| Chart tabs (arrow keys), note search with highlight, typed voice commands, typed chart note | Headless Chromium | ✅ |
| **Spoken dictation / voice recognition** | — | ❌ Not tested: headless browser has no microphone. Needs a manual check in Chrome and Safari. |

## Canary matrix (observed)

| Destination | Header (B MRN) | Other patient (C) | In B passage | Other clinic | Expected? |
|---|---|---|---|---|---|
| Outbound request to Claude | absent | absent | **absent** (scrubbed) | absent | Yes |
| App log | absent | absent | absent | absent | Yes |
| Error reports (incl. failed call) | absent | absent | absent | absent | Yes |
| Audit events | absent | absent | absent | absent | Yes |
| SSE / outbox events | absent | absent | absent | absent | Yes |
| Browser: queue/inbox state | absent | absent | absent | absent | Yes |
| Browser: evidence detail (authorized reviewer) | absent | absent | **PRESENT** | absent | Yes: the reviewer sees the original note |
| Browser: other clinic | absent | absent | absent | absent | Yes |
| `model_calls` table (demo request/response store) | absent | absent | absent | absent | Yes after scrubbing. In production, don't persist excerpts. |

The header canary is never shown anywhere, because the UI does not display MRNs. A real deployment would display some identifiers to authorized users for patient identification (SAFER Patient Identification guide).

## Not executed

- **Live Claude calls** (no credentials). The mode banner says "Replay: saved synthetic responses, no live Claude call," and every call is recorded with provenance `replay:authored_fixture`.
- **VoiceOver + Safari on macOS**, keyboard-only use in Safari, and 200–400% zoom/magnifier review. Manual protocol: `08-validation-plan.md` §6.
- **Clinical review** of the fixtures, wording, and protocol values.
- Load and surge testing beyond the announcement grouping logic.
