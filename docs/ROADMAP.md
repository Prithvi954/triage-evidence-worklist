# Roadmap: from hackathon to real use

Status key: ✅ built and tested · 🟡 built, needs real-world validation · 📝 planned · 🤝 needs a partner (school, hospital, vendor)

## Stage 1: Education pilot (now, no real patient data)

| Item | Status |
|---|---|
| Learning hub: scenario library, private practice sandboxes, Next event / Finish | ✅ |
| Automatic scoring with feedback (note review, AI mistakes caught, emergency calls + reasons, acuity, escalation, restricted records) | ✅ |
| Student case builder with real-patient-information guard; submit → instructor review → class library | ✅ |
| Instructor results, learner accounts | ✅ |
| Two built-in scenarios | ✅; more scenarios written with faculty 🤝 |
| Accessibility testing with screen-reader users (VoiceOver, JAWS, NVDA) | 🤝 |
| Hosting with a persistent database (Render free tier resets on restart) | 📝 Postgres or a paid persistent disk |
| Faculty-written rubric weights per scenario | 📝 |
| Export of results for a gradebook (CSV) | 📝 |

**Exit criteria:** one course or simulation session uses it; learners and faculty give structured feedback; accessibility
issues found by real users are fixed.

## Stage 2: Hospital readiness (engineering)

| Item | Status |
|---|---|
| Accounts, roles, password sign-in, throttling, idle session expiry | ✅ (replace with hospital SSO + MFA 📝) |
| Encryption of note text and AI payloads at rest | ✅ (TRIAGE_DATA_KEY; key management in a vault 📝) |
| Access log, break-the-glass, privacy dashboard, auto-lock, hide names | ✅ |
| AI-text retention purge; AI-disclosure record | ✅ |
| Patient identifiers scrubbed from any text before it could reach the AI | ✅ (add a trained name detector 📝) |
| Clinical mode: refuses to start without encryption, disables demo sign-in and simulator, requires HTTPS cookies | ✅ |
| Read-only FHIR R4 connector (Encounter, Patient, DocumentReference) | ✅ against a fake server; 🟡 test against Epic/Cerner/HAPI sandboxes |
| SMART on FHIR backend-services auth (JWT client credentials) | 📝 (today: bearer token from env) |
| Postgres, backups, monitoring, rate limiting at the edge | 📝 |
| Independent security review / penetration test | 🤝 |

## Stage 3: Clinical pilot (with a hospital partner)

| Item | Owner |
|---|---|
| Executed business associate agreement with Anthropic; HIPAA-ready organization; set `TRIAGE_BAA_CONFIRMED=1` only after | Hospital privacy + legal 🤝 |
| HIPAA risk analysis covering this data flow; privacy officer sign-off | Hospital 🤝 |
| FDA clinical-decision-support assessment (emergency alerts may be a device function) | Regulatory consultant 🤝 |
| Clinical lead writes the real emergency rules, acuity protocol and who may confirm priority | Clinical lead 🤝 |
| **Silent pilot**: AI runs in the background, clinicians don't see it; adjudicators compare with real care (IRB if research) | Hospital + research team 🤝 |
| Measure missed findings, false alerts, subgroup performance, workload, time-to-review | Research team 🤝 |
| Accessibility sign-off with nurses who use assistive technology | Accessibility specialist 🤝 |
| Suspension authority and restart criteria agreed in writing | Facility leader 🤝 |

See `docs/04-vendor-checklist.md`, `docs/06-risk-register.md` and `docs/08-validation-plan.md` for detail.

## Configuration for each stage

| Setting | Demo (hackathon) | Education pilot | Clinical |
|---|---|---|---|
| `TRIAGE_DEPLOYMENT` | `demo` | `education` | `clinical` |
| `TRIAGE_DEMO_LOGIN` | `1` | `0` (real accounts) | `0` (required) |
| `TRIAGE_DEMO_CONTROLS` | `1` | `0` | `0` (required) |
| `TRIAGE_DATA_KEY` | optional | recommended | required |
| `TRIAGE_SECURE_COOKIES` | `1` on HTTPS | `1` | `1` (required) |
| `TRIAGE_MODE` | `replay` or `live` | `live` | `live` |
| `TRIAGE_BAA_CONFIRMED` | `0` | `0` | `1` only with an executed BAA |
| `TRIAGE_FHIR_BASE_URL` / `_TOKEN` | — | — | EHR FHIR endpoint |
