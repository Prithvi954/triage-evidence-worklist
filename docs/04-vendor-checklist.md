# 04. Vendor eligibility checklist (before any real patient information)

Status key: **PUB** = confirmed from published vendor docs (checked 2026-11-13) · **CONTRACT** = only verifiable from the executed agreement, org settings, or procurement · **INTERNAL** = facility work. Nothing marked CONTRACT or INTERNAL has been verified.

| # | Item | Status | Evidence required |
|---|---|---|---|
| 1 | Route is the direct Claude API (`api.anthropic.com`), not chat, Console, Bedrock, or Vertex | PUB (route chosen) | Deployment config; egress allowlist |
| 2 | BAA executed by the facility, and the version recorded | CONTRACT | Signed BAA; name of the signer |
| 3 | HIPAA readiness enabled on **this** organization (permanent once on) | CONTRACT | Console settings screenshot; org ID |
| 4 | API key belongs to that org and workspace; no other keys in use | CONTRACT/INTERNAL | Key inventory; secret manager audit |
| 5 | Model `claude-opus-5-5` is eligible under the executed BAA (not a Covered Model per published list) | PUB (not Covered) / CONTRACT (eligible) | BAA Eligible Services / Implementation Guide |
| 6 | Features used: Messages, structured outputs, effort, adaptive thinking are all HIPAA-eligible | PUB | Feature eligibility table (link in 02) |
| 7 | No ineligible features: Batch, Files, Skills, code execution, MCP, threads, web fetch, compaction, fallbacks | INTERNAL (enforced) | `APPROVED_OUTBOUND_FEATURES`; `test_unapproved_outbound_feature_rejected` |
| 8 | Structured-output schema contains no PHI | INTERNAL (done) | `app/prompt.py` review |
| 9 | Retention arrangement (30-day vs not-retained conflict) | CONTRACT | Written answer from the account team |
| 10 | Flagged-content retention (up to 2 years) accepted by the privacy officer | PUB / INTERNAL | Risk acceptance |
| 11 | Conditions and logging for human access to content | CONTRACT | BAA / Implementation Guide |
| 12 | Subprocessor list and processing geography | CONTRACT | Trust Center docs; DPA |
| 13 | Breach notification timeline and incident contact | CONTRACT | BAA clause; contact on file |
| 14 | Security attestation (e.g., SOC 2 Type II) reviewed | CONTRACT | Trust Center under NDA |
| 15 | Feedback disabled for staff (5-year retention, may train) | INTERNAL | Policy; no feedback UI |
| 16 | Usage Policy high-risk requirements met (qualified reviewer, disclosure) | INTERNAL | Clinical protocol; patient disclosure decision |
| 17 | Re-review triggered by any change to model, endpoint, feature, org, or prompt | INTERNAL | Change-control record; `PROMPT_VERSION` etc. in every model call |

**Why server-side refusal fallbacks are off:** a fallback silently re-runs the request on a different model. That model would need its own eligibility review, and the feature is not in the eligibility table. A refusal is shown as "AI declined this request; manual review required."
