"""Field-level encryption at rest for clinical text (note bodies, chart notes, AI request/response).

Set TRIAGE_DATA_KEY to a Fernet key (generate one with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
and keep it in the host's secret store, never in the repo. Without a key, text is stored as-is
(fine for the synthetic demo; clinical mode refuses to start without one).

Ciphertext carries a prefix so reads work on a mix of old plaintext and new ciphertext rows.
"""
import os

PREFIX = "enc:v1:"
_fernet = None
_loaded = False


def _f():
    global _fernet, _loaded
    if not _loaded:
        _loaded = True
        key = os.environ.get("TRIAGE_DATA_KEY", "").strip()
        if key:
            from cryptography.fernet import Fernet
            _fernet = Fernet(key.encode())
    return _fernet


def reset_for_tests():
    global _fernet, _loaded
    _fernet, _loaded = None, False


def enabled() -> bool:
    return _f() is not None


def enc(text):
    if text is None:
        return None
    f = _f()
    if f is None:
        return text
    return PREFIX + f.encrypt(str(text).encode("utf-8")).decode("ascii")


def dec(value):
    if value is None or not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    f = _f()
    if f is None:
        raise RuntimeError("encrypted data present but TRIAGE_DATA_KEY is not set")
    return f.decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
