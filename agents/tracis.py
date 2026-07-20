"""TRACIS - Supply-Chain Visibility & Risk Agent.

Fully deterministic. Per equipment item:
    buffer_days = required_by_date - revised_eta
where revised_eta is the forecast finish of the delivery activity (parsed P6
fact) and required_by is the earliest start of the downstream activity that
needs the equipment. Severity: <0 CRITICAL, <7 HIGH, <14 MEDIUM.
risk_score is computed from buffer + critical-path exposure - never pasted.
"""
import json
import re
from datetime import date, datetime

from agents.base_agent import BaseAgent
from core import config

_DELIVERY_RE = re.compile(r"deliver|receiv|procure|fabricat|ship", re.IGNORECASE)


def _to_date(iso: str | None) -> date | None:
    return datetime.fromisoformat(iso).date() if iso else None


def _severity(buffer_days: int) -> str | None:
    if buffer_days < 0:
        return "CRITICAL"
    if buffer_days < 7:
        return "HIGH"
    if buffer_days < 14:
        return "MEDIUM"
    return None


class TracisAgent(BaseAgent):
    name = "TRACIS"

    def _risk_score(self, buffer_days: int, on_critical_path: bool) -> float:
        # 14+ day buffer -> ~0; negative buffer saturates towards 0.95;
        # critical-path exposure amplifies by 1.2 (documented demo heuristic)
        base = max(0.0, min(1.0, (14 - buffer_days) / 20))
        score = 0.15 + base * 0.65
        if on_critical_path:
            score *= 1.2
        return round(min(score, 0.95), 3)

    def run(self, **_) -> dict:
        sched = json.loads((config.CACHE_DIR / "schedule.json").read_text())
        activities = {a["task_code"]: a for a in sched["activities"]}

        at_risk, on_track, alerts = [], [], []
        equipment_nodes = [n for n, d in self.kg.g.nodes(data=True)
                           if d.get("node_type") == "Equipment"]
        for tag in sorted(equipment_nodes):
            linked = [activities[n["node_id"]]
                      for n in self.kg.get_neighbors(tag, "REQUIRES", direction="in")
                      if n["node_id"] in activities]
            deliveries = [a for a in linked if _DELIVERY_RE.search(a["name"])]
            consumers = [a for a in linked
                         if not _DELIVERY_RE.search(a["name"]) and a["status"] != "TK_Complete"]
            if not deliveries or not consumers:
                continue
            delivery = deliveries[0]
            if delivery["status"] == "TK_Complete":
                revised_eta = _to_date(delivery["actual_end"])
            else:
                revised_eta = _to_date(delivery["early_end"]) or _to_date(delivery["target_end"])
            required_by = min(_to_date(a["early_start"]) or _to_date(a["target_start"])
                              for a in consumers)
            if not revised_eta or not required_by:
                continue
            buffer_days = (required_by - revised_eta).days
            on_cp = any(a["is_critical"] for a in consumers) or delivery["is_critical"]
            severity = _severity(buffer_days)
            vendor = (self.kg.get_node(tag) or {}).get("attributes", {}).get("vendor")
            record = {
                "equipment_tag": tag,
                "vendor": vendor,
                "delivery_activity": delivery["task_code"],
                "revised_eta": revised_eta.isoformat(),
                "required_by": required_by.isoformat(),
                "required_by_activity": min(
                    consumers, key=lambda a: a["early_start"] or a["target_start"] or "")["task_code"],
                "buffer_days": buffer_days,
                "on_critical_path": on_cp,
            }
            if severity:
                risk = self._risk_score(buffer_days, on_cp)
                record.update({"severity": severity, "risk_score": risk})
                at_risk.append(record)
                alerts.append(
                    f"{tag}: ETA {revised_eta} vs required {required_by} "
                    f"({buffer_days:+d} days, {severity})")
                self.kg.update_node_risk(
                    tag, self.name, risk,
                    f"delivery buffer {buffer_days:+d} days "
                    f"({delivery['task_code']} -> {record['required_by_activity']})")
                self.publish_event(
                    "delivery_at_risk", tag, "Equipment", severity,
                    f"Delivery buffer {buffer_days:+d} days: ETA {revised_eta}, "
                    f"required on site by {required_by} for {record['required_by_activity']}"
                    + (" (critical path)" if on_cp else ""),
                    risk)
            else:
                record.update({"severity": "ON_TRACK",
                               "risk_score": self._risk_score(buffer_days, False)})
                on_track.append(record)

        self.kg.save()
        overall = round(max((r["risk_score"] for r in at_risk), default=0.0), 3)
        return {"at_risk": at_risk, "on_track": on_track,
                "alerts": alerts, "overall_risk": overall}
