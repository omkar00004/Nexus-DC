"""GUIDE - Commissioning Copilot.

Deterministic step engine over data/tia942_procedures.json. Acceptance criteria
are structured data authored in that file - never LLM-generated - so pass/fail
cannot be hallucinated. The LLM writes only the closing summary paragraph.

On a critical-hold FAIL: sign-off is blocked and an RFI node is auto-created in
the knowledge graph, linked to the procedure's activity.

Sessions persist to data/guide_sessions.json (filelock) so the React frontend
and the API - separate processes - see the same state.
"""
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone

from filelock import FileLock

from agents.base_agent import BaseAgent
from core import config

_SESSIONS_PATH = config.DATA_DIR / "guide_sessions.json"
_LOCK = FileLock(str(_SESSIONS_PATH) + ".lock")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_sessions() -> dict:
    if not _SESSIONS_PATH.exists():
        return {}
    try:
        return json.loads(_SESSIONS_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def _save_sessions(sessions: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(config.DATA_DIR), suffix=".sessions.tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(sessions, f, indent=1, default=str)
    os.replace(tmp, _SESSIONS_PATH)


def evaluate_criteria(criteria: dict, reading) -> tuple[bool, str]:
    """Deterministic comparison of a reading against structured criteria."""
    op = criteria["operator"]
    if op == "boolean":
        expected = criteria["expected"]
        value = str(reading).strip().lower() in ("true", "1", "yes", "y") \
            if not isinstance(reading, bool) else reading
        return value == expected, f"expected {expected}, recorded {value}"
    value = float(reading)
    unit = criteria.get("unit", "")
    if op == "less_equal":
        return value <= criteria["limit"], f"{value} {unit} vs limit <= {criteria['limit']} {unit}"
    if op == "greater_equal":
        return value >= criteria["limit"], f"{value} {unit} vs limit >= {criteria['limit']} {unit}"
    if op == "equal":
        tol = criteria.get("tolerance", 0)
        return abs(value - criteria["limit"]) <= tol, \
            f"{value} {unit} vs {criteria['limit']} ± {tol} {unit}"
    if op == "range":
        return criteria["min"] <= value <= criteria["max"], \
            f"{value} {unit} vs range {criteria['min']}-{criteria['max']} {unit}"
    raise ValueError(f"unknown operator {op!r}")


class GuideAgent(BaseAgent):
    name = "GUIDE"

    def __init__(self, kg=None):
        super().__init__(kg)
        doc = json.loads(config.PROCEDURES_PATH.read_text())
        self.procedures = {p["procedure_id"]: p for p in doc["procedures"]}
        self.meta = doc.get("meta", {})

    # ------------------------------------------------------------ public API

    def list_procedures(self) -> list[dict]:
        return [{"procedure_id": pid, "title": p["title"], "system": p["system"],
                 "equipment_tag": p.get("equipment_tag"),
                 "steps": len(p["steps"]), "tier_target": p.get("tier_target")}
                for pid, p in self.procedures.items()]

    def get_procedure(self, procedure_id: str) -> dict:
        return self.procedures[procedure_id]

    def start_session(self, procedure_id: str, operator: str = "Commissioning Engineer") -> dict:
        proc = self.procedures[procedure_id]
        session = {
            "session_id": uuid.uuid4().hex[:12],
            "procedure_id": procedure_id,
            "procedure_title": proc["title"],
            "equipment_tag": proc.get("equipment_tag"),
            "operator": operator,
            "started_at": _now(),
            "current_step": 1,
            "total_steps": len(proc["steps"]),
            "readings": [],
            "status": "in_progress",
            "sign_off_blocked": False,
            "rfi_id": None,
        }
        with _LOCK:
            sessions = _load_sessions()
            sessions[session["session_id"]] = session
            _save_sessions(sessions)
        return session

    def submit_step_reading(self, session_id: str, reading) -> dict:
        with _LOCK:
            sessions = _load_sessions()
            session = sessions[session_id]
            if session["status"] != "in_progress":
                raise ValueError(f"session is {session['status']}, not accepting readings")
            proc = self.procedures[session["procedure_id"]]
            step = proc["steps"][session["current_step"] - 1]
            passed, detail = evaluate_criteria(step["acceptance_criteria"], reading)
            record = {
                "step_no": step["step_no"],
                "instruction": step["instruction"],
                "measurement": step["measurement"],
                "reading": reading,
                "unit": step.get("unit"),
                "result": "PASS" if passed else "FAIL",
                "detail": detail,
                "hold_point": step.get("hold_point", False),
                "critical_hold": step.get("critical_hold", False),
                "ts": _now(),
            }
            session["readings"].append(record)
            if not passed:
                # a failure blocks SIGN-OFF, not the test: the engineer completes
                # the remaining steps so the ITP records the full picture
                session["sign_off_blocked"] = True
                session.setdefault("failed_steps", []).append(step["step_no"])
                session["failed_step"] = session["failed_steps"][0]
                rfi_id = self._raise_rfi(proc, step, record)
                session.setdefault("rfi_ids", []).append(rfi_id)
                session["rfi_id"] = session["rfi_ids"][0]
                record["rfi_id"] = rfi_id
            if session["current_step"] < session["total_steps"]:
                session["current_step"] += 1
            else:
                session["status"] = "steps_complete"
            sessions[session_id] = session
            _save_sessions(sessions)
        if not passed:
            self.publish_event(
                "test_failed", proc.get("equipment_tag") or session["procedure_id"],
                "Equipment", "CRITICAL" if record["critical_hold"] else "HIGH",
                f"{proc['procedure_id']} step {step['step_no']} FAIL: {detail} "
                f"- sign-off blocked, {record['rfi_id']} raised",
                0.85 if record["critical_hold"] else 0.6)
        return {"session": session, "step_result": record}

    def complete_session(self, session_id: str) -> dict:
        with _LOCK:
            sessions = _load_sessions()
            session = sessions[session_id]
            proc = self.procedures[session["procedure_id"]]
            all_pass = (session["status"] == "steps_complete"
                        and all(r["result"] == "PASS" for r in session["readings"]))
            session["status"] = "passed" if all_pass else "failed"
            session["completed_at"] = _now()
            itp = {
                "itp_no": f"ITP-{session['session_id'].upper()}",
                "procedure_id": proc["procedure_id"],
                "title": proc["title"],
                "equipment_tag": proc.get("equipment_tag"),
                "tier_target": proc.get("tier_target"),
                "standard_ref": proc.get("standard_ref"),
                "operator": session["operator"],
                "started_at": session["started_at"],
                "completed_at": session["completed_at"],
                "result": "PASS" if all_pass else "FAIL",
                "sign_off": "SIGNED" if all_pass else "BLOCKED - RFI raised",
                "rfi_id": session.get("rfi_id"),
                "rfi_ids": session.get("rfi_ids", []),
                "failed_steps": session.get("failed_steps", []),
                "readings": session["readings"],
            }
            itp["summary"] = self._summary(itp)
            session["itp"] = itp
            sessions[session_id] = session
            _save_sessions(sessions)
        # the ITP record itself becomes a graph node, so ORACLE can answer
        # "what happened in ITP-XXXX?" by id lookup + traversal
        self.kg.add_node(itp["itp_no"], "ITPRecord", attributes={
            k: itp[k] for k in ("itp_no", "procedure_id", "title", "equipment_tag",
                                "operator", "completed_at", "result", "sign_off",
                                "rfi_ids", "failed_steps", "summary")})
        if self.kg.g.has_node(proc["procedure_id"]):
            self.kg.add_edge(itp["itp_no"], proc["procedure_id"], "RECORDS")
        if proc.get("equipment_tag") and self.kg.g.has_node(proc["equipment_tag"]):
            self.kg.add_edge(itp["itp_no"], proc["equipment_tag"], "CERTIFIES")
        for rfi in itp["rfi_ids"]:
            if self.kg.g.has_node(rfi):
                self.kg.add_edge(itp["itp_no"], rfi, "RESOLVES")
        if all_pass and proc.get("equipment_tag"):
            self.kg.update_node_risk(
                proc["equipment_tag"], self.name, 0.05,
                f"{proc['procedure_id']} passed all {len(session['readings'])} steps")
        self.kg.save()
        return itp

    def get_session(self, session_id: str) -> dict:
        return _load_sessions()[session_id]

    # --------------------------------------------------------------- internals

    def _raise_rfi(self, proc: dict, step: dict, record: dict) -> str:
        existing = [n for n, d in self.kg.g.nodes(data=True) if d.get("node_type") == "RFI"]
        numbers = [int(m.group(1)) for n in existing
                   if (m := __import__("re").match(r"RFI-(\d+)", n))]
        rfi_id = f"RFI-{max(numbers, default=0) + 1:04d}"
        self.kg.add_node(rfi_id, "RFI", attributes={
            "rfi_id": rfi_id,
            "subject": f"{proc['title']} - step {step['step_no']} acceptance failure",
            "discipline": proc.get("system", "Commissioning"),
            "status": "Open",
            "priority": "High" if record["critical_hold"] else "Medium",
            "raised_by": "GUIDE (auto)",
            "ball_in_court": "Vendor",
            "date_raised": _now()[:10],
            "linked_activity": proc.get("linked_activity"),
            "question": (f"Reading {record['reading']} {record['unit'] or ''} failed "
                         f"acceptance ({record['detail']}) during {proc['procedure_id']}. "
                         f"Vendor to advise rectification and retest window."),
        })
        if proc.get("linked_activity") and self.kg.g.has_node(proc["linked_activity"]):
            self.kg.add_edge(rfi_id, proc["linked_activity"], "LINKED_TO")
        if proc.get("equipment_tag") and self.kg.g.has_node(proc["equipment_tag"]):
            self.kg.add_edge(rfi_id, proc["equipment_tag"], "IMPACTS")
            self.kg.update_node_risk(
                proc["equipment_tag"], self.name, 0.85 if record["critical_hold"] else 0.6,
                f"{proc['procedure_id']} step {step['step_no']} FAILED: {record['detail']}")
        self.kg.save()
        return rfi_id

    def _summary(self, itp: dict) -> str:
        try:
            return self.call_llm(
                "Write a 3-4 sentence ITP close-out summary for this commissioning "
                "record. Pass/fail was determined deterministically against authored "
                "acceptance criteria - report it factually, no embellishment:\n"
                + json.dumps({k: itp[k] for k in
                              ("procedure_id", "title", "result", "sign_off", "rfi_id")},
                             default=str)
                + "\nFailed/pass steps:\n"
                + json.dumps([{k: r[k] for k in ("step_no", "measurement", "reading",
                                                 "result", "detail")}
                              for r in itp["readings"]], default=str),
                role="flash", temperature=config.TEMP_NARRATIVE, max_tokens=1200,
                fast=True)
        except Exception:
            fails = [r for r in itp["readings"] if r["result"] == "FAIL"]
            if fails:
                f = fails[0]
                return (f"{itp['procedure_id']} {itp['result']}: step {f['step_no']} "
                        f"({f['measurement']}) recorded {f['reading']} {f['unit'] or ''} - "
                        f"{f['detail']}. Sign-off blocked; {itp['rfi_id']} raised.")
            return f"{itp['procedure_id']} {itp['result']}: all steps within acceptance."

    def run(self, procedure_id: str | None = None, **_) -> dict:
        """Default run = list procedures (sessions are driven interactively)."""
        return {"procedures": self.list_procedures(),
                "note": "use start_session/submit_step_reading/complete_session"}
