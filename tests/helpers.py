import os
import tempfile

from app import config
from app.clock import Clock
from app.server import App

CANARY_HEADER = "CANARY-HDR-7Q2X"      # Patient B's MRN: unnecessary header field
CANARY_OTHER_PATIENT = "CANARY-XPT-4K9M"  # inside Patient C's note
CANARY_PASSAGE = "CANARY-PSG-8V3N"     # inside B's selected passage (legitimately displayed)
CANARY_ORG2 = "CANARY-ORG2-6T1R"       # other clinic's record


def make_app(mode="replay"):
    d = tempfile.mkdtemp(prefix="triage-test-")
    s = config.Settings()
    s.mode = mode
    s.demo_login = True    # tests use the one-click training-user picker
    s.db_path = os.path.join(d, "t.db")
    s.log_path = os.path.join(d, "app.log")
    s.error_report_path = os.path.join(d, "errors.jsonl")
    app = App(settings=s, clock=Clock())
    return app


def user(app, uid):
    return app.store.one("SELECT * FROM users WHERE id=?", (uid,))


def read(path):
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""
