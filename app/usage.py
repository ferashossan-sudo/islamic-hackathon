"""Content-free logging (G11): only allowlisted keys reach stdout, never message text."""
import json
import sys
from datetime import datetime, timedelta, timezone

RIYADH = timezone(timedelta(hours=3))

ALLOWED_KEYS = frozenset({
    "event", "path", "status", "ms", "kind", "degraded",
    "call", "model", "in", "out", "cache_read", "cache_write", "usd", "ok",
})


def log_event(**fields) -> None:
    record = {"ts": datetime.now(RIYADH).isoformat(timespec="seconds")}
    record.update({k: v for k, v in fields.items() if k in ALLOWED_KEYS})
    sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
    sys.stdout.flush()
