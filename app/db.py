"""SQLite store. Durable state for queue, obligations, evidence, decisions, outbox, and audit.

Design rules:
- An obligation's due_at is written once and never updated (no retry, restart, or model
  outcome can reset a deadline). Only completion fields change.
- Clinical text lives only in `notes` (and in model_calls.request_json/response_json for the
  approved synthetic fixtures). The audit table and outbox never carry clinical text.
"""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS orgs (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, org TEXT NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL, title TEXT,
  status TEXT NOT NULL, covered_by TEXT, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS encounters (
  id TEXT PRIMARY KEY, org TEXT NOT NULL, label TEXT NOT NULL, arrived_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', priority INTEGER, priority_confirmed_by TEXT,
  priority_confirmed_at TEXT, primary_user TEXT, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS encounter_headers (encounter_id TEXT PRIMARY KEY, header_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes (
  note_id TEXT NOT NULL, version INTEGER NOT NULL, encounter_id TEXT NOT NULL, org TEXT NOT NULL,
  author_employee_id TEXT, signed_at TEXT NOT NULL, received_at TEXT NOT NULL, body TEXT NOT NULL,
  fixture_sha256 TEXT, PRIMARY KEY (note_id, version));
CREATE TABLE IF NOT EXISTS review_items (
  id TEXT PRIMARY KEY, org TEXT NOT NULL, encounter_id TEXT NOT NULL, note_id TEXT NOT NULL,
  note_version INTEGER NOT NULL, received_at TEXT NOT NULL, state TEXT NOT NULL,
  ai_state TEXT NOT NULL, reviewed_by TEXT, reviewed_at TEXT, version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (note_id, note_version));
CREATE TABLE IF NOT EXISTS model_calls (
  id TEXT PRIMARY KEY, review_item_id TEXT NOT NULL, org TEXT NOT NULL, encounter_id TEXT NOT NULL,
  note_id TEXT NOT NULL, note_version INTEGER NOT NULL, mode TEXT NOT NULL, provenance TEXT,
  model TEXT, prompt_version TEXT, schema_version TEXT, selector_version TEXT, rules_version TEXT,
  started_at TEXT NOT NULL, finished_at TEXT, outcome TEXT, provider_request_id TEXT,
  request_json TEXT, response_json TEXT, validation_json TEXT);
CREATE TABLE IF NOT EXISTS findings (
  id TEXT PRIMARY KEY, review_item_id TEXT NOT NULL, model_call_id TEXT NOT NULL, idx INTEGER NOT NULL,
  finding_json TEXT NOT NULL, checks_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY, review_item_id TEXT NOT NULL, encounter_id TEXT NOT NULL, user_id TEXT NOT NULL,
  decided_at TEXT NOT NULL, decision TEXT NOT NULL, corrections_json TEXT, comment TEXT);
CREATE TABLE IF NOT EXISTS priority_changes (
  id TEXT PRIMARY KEY, encounter_id TEXT NOT NULL, from_priority INTEGER, to_priority INTEGER NOT NULL,
  user_id TEXT NOT NULL, at TEXT NOT NULL, review_item_id TEXT, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS obligations (
  id TEXT PRIMARY KEY, org TEXT NOT NULL, encounter_id TEXT NOT NULL, kind TEXT NOT NULL,
  due_at TEXT NOT NULL, created_at TEXT NOT NULL, owner TEXT, accepted_by TEXT, accepted_at TEXT,
  reached_at TEXT, completed_at TEXT, completed_by TEXT, status TEXT NOT NULL DEFAULT 'open',
  source TEXT, source_ref TEXT, escalated_to TEXT, escalated_at TEXT);
CREATE TRIGGER IF NOT EXISTS obligations_due_immutable BEFORE UPDATE OF due_at ON obligations
  WHEN NEW.due_at <> OLD.due_at BEGIN SELECT RAISE(ABORT, 'due_at is immutable'); END;
CREATE TABLE IF NOT EXISTS escalations (
  id TEXT PRIMARY KEY, org TEXT NOT NULL, encounter_id TEXT NOT NULL, raised_by TEXT NOT NULL,
  raised_at TEXT NOT NULL, reason_code TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
  ack_by TEXT, ack_at TEXT);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, review_item_id TEXT NOT NULL, note_id TEXT NOT NULL, note_version INTEGER NOT NULL,
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL,
  last_error_class TEXT, idempotency_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS outbox (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, org TEXT NOT NULL, created_at TEXT NOT NULL,
  type TEXT NOT NULL, payload_json TEXT NOT NULL, delivered_at TEXT);
CREATE TABLE IF NOT EXISTS audit (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, org TEXT NOT NULL, actor TEXT NOT NULL,
  action TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL, details_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feed (org TEXT PRIMARY KEY, last_message_at TEXT, stalled_sim INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sim (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chart_notes (
  id TEXT PRIMARY KEY, org TEXT NOT NULL, encounter_id TEXT NOT NULL, author_user TEXT NOT NULL,
  note_type TEXT NOT NULL, created_at TEXT NOT NULL, body TEXT NOT NULL, input_method TEXT NOT NULL DEFAULT 'typed');
CREATE TABLE IF NOT EXISTS emergency_flags (
  id TEXT PRIMARY KEY, org TEXT NOT NULL, encounter_id TEXT NOT NULL, source TEXT NOT NULL, rule_id TEXT,
  factor_label TEXT NOT NULL, evidence TEXT, note_ref TEXT, raised_by TEXT NOT NULL, raised_at TEXT NOT NULL,
  status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  nurse_assessment TEXT, nurse_by TEXT, nurse_at TEXT, final_determination TEXT, final_by TEXT, final_at TEXT);
CREATE TABLE IF NOT EXISTS emergency_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, flag_id TEXT NOT NULL, org TEXT NOT NULL, at TEXT NOT NULL,
  user_id TEXT NOT NULL, action TEXT NOT NULL, decision TEXT, reason TEXT);
-- privacy: who opened which chart, and why (break-the-glass)
CREATE TABLE IF NOT EXISTS chart_access (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, org TEXT NOT NULL, user_id TEXT NOT NULL,
  encounter_id TEXT NOT NULL, action TEXT NOT NULL, reason TEXT, break_glass INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS ai_disclosures (
  review_item_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, at TEXT NOT NULL);
-- texts an instructor (or the built-in library) approved as synthetic, so they may be sent to the AI
CREATE TABLE IF NOT EXISTS approved_texts (sha TEXT PRIMARY KEY, approved_by TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS note_replay (
  note_id TEXT NOT NULL, version INTEGER NOT NULL, replay_note_id TEXT NOT NULL, replay_version INTEGER NOT NULL,
  PRIMARY KEY (note_id, version));
-- education
CREATE TABLE IF NOT EXISTS scenarios (
  id TEXT PRIMARY KEY, org TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, goals TEXT NOT NULL,
  role TEXT NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  spec_json TEXT NOT NULL, review_comment TEXT);
CREATE TABLE IF NOT EXISTS training_sessions (
  id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL, user_id TEXT NOT NULL, home_org TEXT NOT NULL, sandbox_org TEXT NOT NULL,
  shadow_user TEXT NOT NULL, parent_token TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
  event_index INTEGER NOT NULL DEFAULT 0, events_total INTEGER NOT NULL DEFAULT 0, score_json TEXT);
-- integration: last sync per FHIR source
CREATE TABLE IF NOT EXISTS fhir_sync (source TEXT PRIMARY KEY, org TEXT NOT NULL, last_sync_at TEXT, last_error TEXT,
  encounters INTEGER NOT NULL DEFAULT 0, notes INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS training_roles (session_id TEXT PRIMARY KEY, role TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS signups (email TEXT PRIMARY KEY, name TEXT NOT NULL, program TEXT NOT NULL,
  password_hash TEXT NOT NULL, code_hash TEXT NOT NULL, expires_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  last_sent_at REAL NOT NULL, created_at REAL NOT NULL);
"""

# Columns added after the first release. Applied idempotently to existing databases.
MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN username TEXT",
    "ALTER TABLE users ADD COLUMN password_hash TEXT",
    "ALTER TABLE sessions ADD COLUMN last_seen REAL",
    "ALTER TABLE encounters ADD COLUMN restricted INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE encounters ADD COLUMN display_json TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS users_username ON users(username)",
]


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                self.conn.execute(sql)
            except sqlite3.OperationalError:
                pass   # already applied
        from . import crypto
        self.conn.create_function("dec", 1, crypto.dec, deterministic=True)

    @contextmanager
    def tx(self):
        """One atomic transaction. Callers group every related write inside a single tx."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def reset(self):
        with self.lock:
            tables = [r["name"] for r in self.q("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for t in tables:
                self.conn.execute(f"DELETE FROM {t}")
            self.conn.execute("DELETE FROM sqlite_sequence") if self.q(
                "SELECT name FROM sqlite_master WHERE name='sqlite_sequence'") else None


def audit(c, ts, org, actor, action, target_type, target_id, **details):
    """Append-only audit. `details` must be IDs / enums / versions only (checked)."""
    for k, v in details.items():
        if isinstance(v, str) and (len(v) > 80 or " " in v.strip() and k not in ("reason_code",)):
            raise ValueError(f"audit detail {k} looks like free text; audit stores codes only")
    c.execute("INSERT INTO audit (ts, org, actor, action, target_type, target_id, details_json) VALUES (?,?,?,?,?,?,?)",
              (ts, org, actor, action, target_type, target_id, json.dumps(details, sort_keys=True)))


def emit(c, ts, org, type_, **payload):
    """Transactional outbox: the event commits with the state change, delivery happens after."""
    c.execute("INSERT INTO outbox (org, created_at, type, payload_json) VALUES (?,?,?,?)",
              (org, ts, type_, json.dumps(payload, sort_keys=True)))
