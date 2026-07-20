"""Thin wrapper over `xerparser` - never a hand-rolled XER parser.

Maps P6 objects to plain dicts the rest of the system consumes. Facts only:
dates, durations, float, relationships. Slips/cascades are CHRONOS's job.
"""
from xerparser import Xer


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def parse_xer(path: str) -> dict:
    with open(path, encoding=Xer.CODEC, errors="ignore") as f:
        xer = Xer(f.read())

    # single-project exports are the norm; take the first project
    proj = list(xer.projects.values())[0]

    activities = []
    for t in proj.tasks:
        activities.append({
            "task_code": t.task_code,
            "uid": t.uid,
            "name": t.name,
            "type": t.type.name,                      # TT_Task / TT_FinMile / ...
            "is_milestone": "MILE" in t.type.name.upper(),
            "status": t.status.name,                  # TK_NotStart / TK_Active / TK_Complete
            "is_critical": bool(t.is_critical),       # P6's own float-based flag (parsed fact)
            "total_float": float(t.total_float) if t.total_float is not None else None,
            "original_duration_days": float(t.original_duration) if t.original_duration is not None else None,
            "remaining_duration_days": float(t.duration) if t.duration is not None else None,
            "target_start": _iso(t.target_start_date),
            "target_end": _iso(t.target_end_date),
            "actual_start": _iso(t.act_start_date),
            "actual_end": _iso(t.act_end_date),
            "early_start": _iso(t.early_start_date),
            "early_end": _iso(t.early_end_date),
            "late_start": _iso(t.late_start_date),
            "late_end": _iso(t.late_end_date),
        })

    relationships = []
    for r in proj.relationships:
        relationships.append({
            "predecessor": r.predecessor.task_code,
            "successor": r.successor.task_code,
            "link": r.link,                            # FS / SS / FF / SF
            "lag_days": float(r.lag) if r.lag is not None else 0.0,
        })

    return {
        "project_name": proj.name,
        "data_date": _iso(proj.data_date),
        "activities": activities,
        "relationships": relationships,
    }
