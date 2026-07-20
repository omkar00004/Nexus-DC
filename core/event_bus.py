"""File-backed append-only event bus.

The React frontend and FastAPI are separate OS processes (and agents may run in
their own workers), so the bus is `data/events.json` guarded by a filelock -
never an in-memory singleton.

Event schema:
    {agent, event_type, entity_id, entity_type, severity, description,
     risk_score, ts}
"""
import json
import os
import tempfile
from datetime import datetime, timezone

from filelock import FileLock

from core import config

REQUIRED_FIELDS = (
    "agent", "event_type", "entity_id", "entity_type",
    "severity", "description", "risk_score",
)
SEVERITIES = ("CRITICAL", "HIGH", "MAJOR", "MEDIUM", "MINOR", "LOW", "INFO")

_LOCK = FileLock(str(config.EVENTS_PATH) + ".lock")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_unlocked() -> list:
    if not config.EVENTS_PATH.exists():
        return []
    try:
        with open(config.EVENTS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return []


def _write_unlocked(events: list) -> None:
    # atomic replace so a concurrent reader never sees a half-written file
    fd, tmp = tempfile.mkstemp(dir=str(config.DATA_DIR), suffix=".events.tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=1, default=str)
    os.replace(tmp, config.EVENTS_PATH)


def publish(event: dict) -> dict:
    missing = [k for k in REQUIRED_FIELDS if k not in event]
    if missing:
        raise ValueError(f"event missing fields {missing}: {event}")
    if event["severity"] not in SEVERITIES:
        raise ValueError(f"unknown severity {event['severity']!r}")
    event = {**event, "ts": event.get("ts") or _now()}
    with _LOCK:
        events = _read_unlocked()
        events.append(event)
        _write_unlocked(events)
    return event


def publish_many(events: list) -> list:
    return [publish(e) for e in events]


def get_all_events() -> list:
    with _LOCK:
        return _read_unlocked()


def get_events_by_entity(entity_id: str) -> list:
    return [e for e in get_all_events() if e["entity_id"] == entity_id]


def get_events_by_agent(agent: str) -> list:
    return [e for e in get_all_events() if e["agent"] == agent]


def clear() -> None:
    with _LOCK:
        _write_unlocked([])


def remove_agents(agents: list[str]) -> None:
    """Drop events from the given agents only - used by a full re-analysis so
    stale SPECTRA/CHRONOS/TRACIS signals don't linger, while GUIDE's
    operational history (test failures, raised RFIs) is preserved."""
    with _LOCK:
        _write_unlocked([e for e in _read_unlocked() if e["agent"] not in agents])


def remove_matching(**fields) -> int:
    """Drop events matching ALL given key/values (e.g. agent="FIELD",
    ref="NCR-0001") - used when a field record is closed so its signal stops
    feeding convergence. Returns the number of events removed."""
    with _LOCK:
        events = _read_unlocked()
        keep = [e for e in events
                if not all(e.get(k) == v for k, v in fields.items())]
        _write_unlocked(keep)
        return len(events) - len(keep)
