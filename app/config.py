"""Runtime configuration. Every value comes from the environment; no secrets live in the repo.

The API key is read by the official SDK from ANTHROPIC_API_KEY on the server only.
It is never sent to the browser, logged, or written into test output.
"""
import os
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class Settings:
    # "replay" uses saved synthetic responses; "live" calls the Claude Messages API.
    mode: str = field(default_factory=lambda: os.environ.get("TRIAGE_MODE", "replay"))
    # Pinned model. Changing it is a reviewed configuration change (see docs/04-vendor-checklist.md).
    model: str = field(default_factory=lambda: os.environ.get("TRIAGE_MODEL", "claude-opus-5-5"))
    effort: str = field(default_factory=lambda: os.environ.get("TRIAGE_EFFORT", "medium"))
    request_timeout_s: float = field(default_factory=lambda: float(os.environ.get("TRIAGE_TIMEOUT_S", "45")))
    db_path: str = field(default_factory=lambda: os.environ.get("TRIAGE_DB", os.path.join(ROOT, "data", "demo.db")))
    log_path: str = field(default_factory=lambda: os.environ.get("TRIAGE_LOG", os.path.join(ROOT, "data", "app.log")))
    error_report_path: str = field(default_factory=lambda: os.environ.get(
        "TRIAGE_ERRORS", os.path.join(ROOT, "data", "error_reports.jsonl")))
    # Hosting platforms (Render, Railway, Fly) pass the port in $PORT.
    port: int = field(default_factory=lambda: int(os.environ.get("TRIAGE_PORT") or os.environ.get("PORT") or "8765"))
    host: str = field(default_factory=lambda: os.environ.get("TRIAGE_HOST", "127.0.0.1"))
    # Add the Secure flag to the session cookie when served over HTTPS (set on the host).
    secure_cookies: bool = field(default_factory=lambda: os.environ.get("TRIAGE_SECURE_COOKIES", "0") == "1")
    # Demo-only controls (EHR simulator, clock, provider fault injection). Off unless set.
    demo_controls: bool = field(default_factory=lambda: os.environ.get("TRIAGE_DEMO_CONTROLS", "1") == "1")
    # Feed is considered stalled if no EHR message arrives within this many minutes (illustrative).
    feed_stall_minutes: int = 15
    max_job_attempts: int = 3
    # "demo" (synthetic, demo sign-in picker), "education" (school accounts, scenarios), or
    # "clinical" (real data: requires an encryption key; AI on real notes only with a confirmed BAA).
    deployment: str = field(default_factory=lambda: os.environ.get("TRIAGE_DEPLOYMENT", "demo"))
    # Show the one-click training-user picker on the sign-in page (never in clinical deployments).
    demo_login: bool = field(default_factory=lambda: os.environ.get("TRIAGE_DEMO_LOGIN", "1") == "1")
    # The facility has an executed BAA with Anthropic and a HIPAA-ready organization for this API key.
    baa_confirmed: bool = field(default_factory=lambda: os.environ.get("TRIAGE_BAA_CONFIRMED", "0") == "1")
    session_idle_minutes: int = field(default_factory=lambda: int(os.environ.get("TRIAGE_SESSION_IDLE_MIN", "30")))
    # Screen locks on the shared workstation after this many idle minutes (client side).
    lock_minutes: int = field(default_factory=lambda: int(os.environ.get("TRIAGE_LOCK_MIN", "3")))
    # Saved AI request/response text is purged after this many days (decisions and audit are kept).
    ai_retention_days: int = field(default_factory=lambda: int(os.environ.get("TRIAGE_AI_RETENTION_DAYS", "7")))
    # Optional FHIR R4 source (e.g. an EHR sandbox). Bearer token comes from the environment only.
    fhir_base_url: str = field(default_factory=lambda: os.environ.get("TRIAGE_FHIR_BASE_URL", ""))
    fhir_token: str = field(default_factory=lambda: os.environ.get("TRIAGE_FHIR_TOKEN", ""))
    fhir_org: str = field(default_factory=lambda: os.environ.get("TRIAGE_FHIR_ORG", "org_n"))

    def ai_may_send_real_notes(self) -> bool:
        """Real (non-synthetic) note text may go to the AI only in a clinical deployment with a BAA."""
        return self.deployment == "clinical" and self.baa_confirmed and self.mode == "live"


# Versions recorded with every model call so changes can be evaluated and rolled back.
PROMPT_VERSION = "extract-prompt-v3"
SCHEMA_VERSION = "evidence-schema-v2"
RULES_VERSION = "ordering-rules-v2"
SELECTOR_VERSION = "excerpt-selector-v2"

# Outbound features this adapter may use. Anything else is rejected before a request is built.
# Every item here was checked against Anthropic's feature eligibility table on 2026-11-13
# (docs/02-research.md). Adding a feature requires updating that review.
APPROVED_OUTBOUND_FEATURES = frozenset({"messages", "structured_outputs", "effort", "adaptive_thinking"})
