"""Versioned briefing snapshots on local disk (prototype; S3 later). Approved snapshots are immutable."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

from .models import Briefing

ROOT = Path(__file__).resolve().parents[1] / "briefings"


class StoreError(RuntimeError):
    pass


def _root(root: Path | None) -> Path:
    return root if root is not None else ROOT


def path_for(as_of: date, root: Path | None = None) -> Path:
    return _root(root) / as_of.isoformat() / "briefing.json"


def save(b: Briefing, root: Path | None = None, overwrite_draft: bool = True) -> Path:
    p = path_for(b.as_of, root)
    if p.exists():
        existing = load(b.as_of, root)
        if existing.status == "APPROVED":
            raise StoreError(f"briefing {b.as_of} is APPROVED and immutable")
        if not overwrite_draft:
            raise StoreError(f"briefing {b.as_of} exists")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(b.model_dump_json(indent=1), encoding="utf-8")
    return p


def load(as_of: date, root: Path | None = None) -> Briefing:
    p = path_for(as_of, root)
    if not p.exists():
        raise StoreError(f"no briefing for {as_of}")
    return Briefing.model_validate_json(p.read_text(encoding="utf-8"))


def list_briefings(root: Path | None = None) -> list[tuple[date, str]]:
    out = []
    root = _root(root)
    if root.exists():
        for d in sorted(root.iterdir(), reverse=True):
            f = d / "briefing.json"
            if f.exists():
                status = json.loads(f.read_text(encoding="utf-8")).get("status", "DRAFT")
                out.append((date.fromisoformat(d.name), status))
    return out


def approve(as_of: date, by: str, root: Path | None = None) -> Briefing:
    if not by.strip():
        raise StoreError("approver name required")
    b = load(as_of, root)
    if b.status == "APPROVED":
        raise StoreError(f"briefing {as_of} already approved by {b.approved_by}")
    b.status, b.approved_by = "APPROVED", by.strip()
    b.approved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path_for(as_of, root).write_text(b.model_dump_json(indent=1), encoding="utf-8")
    return b
