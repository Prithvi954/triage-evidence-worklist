"""Controllable clock. All deadlines and timestamps use this, so tests and demos are repeatable."""
import threading
from datetime import datetime, timedelta, timezone

# Demo day starts at an illustrative 10:00 (stored as UTC, labelled illustrative in the UI).
DEMO_START = datetime(2026, 11, 14, 10, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start: datetime = DEMO_START, frozen: bool = True):
        self._lock = threading.Lock()
        self._now = start
        self._frozen = frozen
        self._real_anchor = datetime.now(timezone.utc)

    def now(self) -> datetime:
        with self._lock:
            if self._frozen:
                return self._now
            return self._now + (datetime.now(timezone.utc) - self._real_anchor)

    def advance(self, minutes: float = 0, seconds: float = 0) -> datetime:
        with self._lock:
            if not self._frozen:
                self._now = self._now + (datetime.now(timezone.utc) - self._real_anchor)
                self._real_anchor = datetime.now(timezone.utc)
            self._now = self._now + timedelta(minutes=minutes, seconds=seconds)
            return self._now


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
