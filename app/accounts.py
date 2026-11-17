"""Accounts: password hashing, login throttling, idle session expiry, and user administration.

Passwords: PBKDF2-HMAC-SHA256, 310,000 iterations, 16-byte random salt (Python stdlib).
Sessions expire after TRIAGE_SESSION_IDLE_MIN minutes without a request (wall-clock time).
Five failed logins for a username within 5 minutes lock that username for 5 minutes.
In production, replace this with the facility's single sign-on (SAML/OIDC) and MFA.
"""
import hashlib
import hmac
import re
import secrets
import threading
import time

ITERATIONS = 310_000
ROLES = {
    "rn": "Registered nurse",
    "charge_rn": "Charge nurse",
    "provider": "Provider (resident / attending)",
    "student": "Student (nursing / medical)",
    "instructor": "Instructor",
    "admin": "Administrator",
}
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,40}$")
EMAIL_RE = re.compile(r"^[a-z0-9._%+-]{1,64}@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")
PROGRAMS = {"nursing": "Nursing student", "medical": "Medical student", "resident": "Resident",
            "nurse": "Nurse (practicing)", "other": "Learner"}
CODE_MINUTES = 15
CODE_ATTEMPTS = 5
RESEND_SECONDS = 60


def code_hash(email: str, code: str) -> str:
    return hashlib.sha256(f"{email}:{code}".encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def password_problems(password: str):
    problems = []
    if len(password) < 10:
        problems.append("at least 10 characters")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        problems.append("letters and numbers")
    return problems


class Throttle:
    """In-memory failed-login counter (per username)."""

    def __init__(self, limit=5, window_s=300):
        self.limit, self.window = limit, window_s
        self.fails = {}
        self.lock = threading.Lock()

    def blocked(self, key):
        now = time.time()
        with self.lock:
            recent = [t for t in self.fails.get(key, []) if now - t < self.window]
            self.fails[key] = recent
            return len(recent) >= self.limit

    def fail(self, key):
        with self.lock:
            self.fails.setdefault(key, []).append(time.time())

    def clear(self, key):
        with self.lock:
            self.fails.pop(key, None)
