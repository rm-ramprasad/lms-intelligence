"""Append-only JSONL audit log of chat turns. Never stores result rows. Long digit runs in the
question text (possible account/phone numbers) are masked before writing."""
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

LONG_DIGITS = re.compile(r"\d{6,}")
_lock = threading.Lock()


def redact(text: str) -> str:
    return LONG_DIGITS.sub(lambda m: "#" * len(m.group()), text)[:500]


def log_turn(path: Path, *, session: str, question: str, outcome: str, spec: dict | None = None,
             status: str | None = None, query_id: str | None = None, ms: int | None = None,
             bytes_scanned: int | None = None, unrecognised: list[str] | None = None,
             cached: bool | None = None, **extra) -> None:
    """extra: interpreter source/mode and AI meta (model, tokens, cost, prompt hash). Never result rows."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": session,
        "question": redact(question),
        "outcome": outcome,             # interpreted | clarify | refuse | builder | example
        "spec": spec,
        "status": status,
        "query_id": query_id,
        "ms": ms,
        "bytes": bytes_scanned,
        "cached": cached,
        "unrecognised": [redact(w) for w in (unrecognised or [])],
        **{k: v for k, v in extra.items() if k not in ("rows", "result")},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
