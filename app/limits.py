"""Rate limits and the daily model-call budget, in memory (one worker; a restart resets them).

Limits run after distress detection, so a person in distress always gets the support card.
The visitor's address is never stored: it is reduced to an HMAC with a random salt that lives only
in memory and changes every day.
"""
import hashlib
import hmac
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

RIYADH = timezone(timedelta(hours=3))


def _parse(spec: str, default: tuple[int, int]) -> tuple[int, int]:
    """'20/600' -> (20 requests, 600 seconds)."""
    try:
        count, seconds = spec.split("/")
        return int(count), int(seconds)
    except (ValueError, AttributeError):
        return default


SESSION = _parse(os.environ.get("RATE_SESSION", "20/600"), (20, 600))
ADDRESS = _parse(os.environ.get("RATE_IP", "60/600"), (60, 600))
GLOBAL_PER_MIN = int(os.environ.get("RATE_GLOBAL_PER_MIN", "60"))
DAILY_LLM_CALLS = int(os.environ.get("DAILY_LLM_CALLS", "1000"))  # about two 500-request free quotas


class Limiter:
    def __init__(self) -> None:
        self.windows: dict[str, deque] = defaultdict(deque)
        self.salt_day = ""
        self.salt = b""
        self.llm_day = ""
        self.llm_calls = 0

    def _today(self) -> str:
        return datetime.now(RIYADH).strftime("%Y-%m-%d")

    def bucket(self, address: str) -> str:
        day = self._today()
        if day != self.salt_day:
            self.salt_day, self.salt = day, os.urandom(16)
        return hmac.new(self.salt, (address or "").encode("utf-8"), hashlib.sha256).hexdigest()[:16]

    def _hit(self, key: str, limit: int, seconds: int, now: float) -> float:
        """Record a request; return 0 if allowed, else seconds to wait."""
        window = self.windows[key]
        while window and now - window[0] >= seconds:
            window.popleft()
        if len(window) >= limit:
            return seconds - (now - window[0])
        window.append(now)
        return 0.0

    def check(self, sid: str, address: str, now: float | None = None) -> int:
        """0 if the message may proceed, else whole seconds to wait."""
        now = time.monotonic() if now is None else now
        waits = [self._hit("global", GLOBAL_PER_MIN, 60, now),
                 self._hit("ip:" + self.bucket(address), *ADDRESS, now)]
        if sid:
            waits.append(self._hit("sid:" + sid, *SESSION, now))
        wait = max(waits)
        return int(wait) + 1 if wait > 0 else 0

    def llm_allowed(self) -> bool:
        """The daily budget of model calls protects the free-tier quota; over it, degraded mode."""
        day = self._today()
        if day != self.llm_day:
            self.llm_day, self.llm_calls = day, 0
        return self.llm_calls < DAILY_LLM_CALLS

    def count_llm_call(self) -> None:
        self.llm_allowed()
        self.llm_calls += 1


LIMITER = Limiter()
