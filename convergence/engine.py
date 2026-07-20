"""Convergence Engine - the reconciliation layer over the event bus.

An entity converges when >= 2 DISTINCT agents have flagged it. Then:

    convergence_score = sum(max event risk per agent) * criticality_weight

with criticality_weight = 1.5 if the entity sits on the critical path (from
the KG), else 1.0. An alert fires at CONVERGENCE_THRESHOLD (0.65) - a config
constant and an acknowledged demo heuristic, surfaced in every alert payload.

Because every input signal is computed by an agent from source data, the
convergence appears/disappears when the sources change: fix the submittal and
SPECTRA's contribution drops out of the sum on the next run.

Risk Storm: >= 3 entities converging simultaneously = systemic project stress.

Alerts persist to data/convergence_alerts.json (filelock, atomic) so the
dashboard - a separate process - reads them via the API.
"""
import json
import os
import tempfile
from datetime import datetime, timezone

from filelock import FileLock

from agents.base_agent import BaseAgent
from core import config, event_bus

ALERTS_PATH = config.DATA_DIR / "convergence_alerts.json"
_LOCK = FileLock(str(ALERTS_PATH) + ".lock")

_NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "narrative": {"type": "string"},
        "root_cause": {"type": "string"},
        "combined_impact": {"type": "string"},
        "mitigation_options": {"type": "array", "items": {"type": "string"},
                               "minItems": 3, "maxItems": 3},
    },
    "required": ["narrative", "root_cause", "combined_impact", "mitigation_options"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConvergenceEngine(BaseAgent):
    """Subclasses BaseAgent for KG/LLM plumbing; it is a reconciler, not a
    sixth analyst - it emits alerts, never bus events of its own."""
    name = "CONVERGENCE"

    # ------------------------------------------------------------- scoring

    def _agent_signals(self, events: list[dict]) -> dict:
        """Per distinct agent: the strongest signal it raised on this entity."""
        signals: dict[str, dict] = {}
        for e in events:
            if e.get("severity") == "INFO":
                continue  # housekeeping (e.g. an NCR closing) is not a risk signal

            cur = signals.get(e["agent"])
            if cur is None or e["risk_score"] > cur["risk_score"]:
                signals[e["agent"]] = {
                    "risk_score": e["risk_score"],
                    "event_type": e["event_type"],
                    "severity": e["severity"],
                    "description": e["description"],
                    "ts": e["ts"],
                }
        return signals

    def _is_on_critical_path(self, entity_id: str) -> bool:
        node = self.kg.get_node(entity_id)
        if not node:
            return False
        if node["attributes"].get("is_critical"):
            return True
        if node["node_type"] == "Equipment":
            return any(n["node"]["attributes"].get("is_critical")
                       for n in self.kg.get_neighbors(entity_id, "REQUIRES", direction="in"))
        return False

    # ------------------------------------------------------------- context

    def _entity_context(self, entity_id: str) -> dict:
        node = self.kg.get_node(entity_id) or {}
        attrs = dict(node.get("attributes", {}))
        attrs.pop("submitted_parameters", None)  # keep the prompt lean
        context = {"entity": entity_id, "node_type": node.get("node_type"),
                   "attributes": attrs}
        context["deviations"] = [
            self.kg.get_node(n)["attributes"]
            for n, d in self.kg.g.nodes(data=True)
            if d.get("node_type") == "Deviation" and n.startswith(f"DEV::{entity_id}")]
        context["activities"] = [
            {"activity": n["node_id"], "name": n["node"]["attributes"].get("name"),
             "status": n["node"]["attributes"].get("status"),
             "is_critical": n["node"]["attributes"].get("is_critical")}
            for n in self.kg.get_neighbors(entity_id, "REQUIRES", direction="in")]
        context["open_rfis"] = [
            {"rfi": n, "subject": d["attributes"].get("subject")}
            for n, d in self.kg.g.nodes(data=True)
            if d.get("node_type") == "RFI"
            and str(d["attributes"].get("status", "")).lower() == "open"
            and any(x["node_id"] in {a["activity"] for a in context["activities"]}
                    for x in self.kg.get_neighbors(n, "LINKED_TO"))]
        # field-raised NCRs against this entity (the human signal on the bus)
        context["open_ncrs"] = [
            {"ncr": n, "title": d["attributes"].get("title"),
             "severity": d["attributes"].get("severity"),
             "raised_by": d["attributes"].get("raised_by")}
            for n, d in self.kg.g.nodes(data=True)
            if d.get("node_type") == "NCR"
            and str(d["attributes"].get("status", "")).upper() != "CLOSED"
            and any(x["node_id"] == entity_id
                    for x in self.kg.get_neighbors(n, "IMPACTS"))]
        # milestone MC results (CHRONOS writes these) drive the SLA exposure
        for n, d in self.kg.g.nodes(data=True):
            if d.get("node_type") == "Milestone" and "expected_delay_days" in d.get("attributes", {}):
                context["milestone"] = {"id": n, **{k: d["attributes"][k] for k in
                                        ("mc_p50", "mc_p90", "baseline_finish",
                                         "expected_delay_days", "sla_breach_risk")}}
                break
        return context

    def _sla_exposure(self, context: dict) -> dict:
        delay_days = context.get("milestone", {}).get("expected_delay_days", 0)
        exposure = delay_days * config.SLA_PENALTY_PER_DAY_USD
        return {
            "delay_days": delay_days,
            "penalty_per_day_usd": config.SLA_PENALTY_PER_DAY_USD,
            "exposure_usd": exposure,
            "assumption": (f"ASSUMPTION: liquidated damages of "
                           f"${config.SLA_PENALTY_PER_DAY_USD:,.0f}/day, a demo figure - "
                           f"actual LD rates come from the contract."),
        }

    # ------------------------------------------------------------ narrative

    def _narrate(self, entity_id: str, signals: dict, score: float,
                 context: dict, exposure: dict) -> dict:
        try:
            return self.call_llm_structured(
                "Multiple independent agents flagged the SAME entity on a data-centre "
                "EPC project. Reconcile their computed signals into ONE causal story.\n\n"
                f"Entity: {entity_id}\nConvergence score: {score} "
                f"(threshold {config.CONVERGENCE_THRESHOLD})\n"
                f"Agent signals (each independently computed):\n{json.dumps(signals, default=str)}\n\n"
                f"Knowledge-graph context:\n{json.dumps(context, default=str)}\n\n"
                f"SLA exposure: {json.dumps(exposure)}\n\n"
                "Return strict JSON: narrative (one causal chain uniting all agent "
                "angles, quoting the computed figures), root_cause (the single most "
                "upstream cause), combined_impact (schedule + commercial, citing the "
                "SLA exposure WITH its stated assumption), mitigation_options "
                "(exactly 3, concrete, each naming which agent signals it addresses).",
                schema=_NARRATIVE_SCHEMA, role="pro",
                temperature=config.TEMP_NARRATIVE,
                max_tokens=config.MAX_TOKENS_NARRATIVE,
                system="You are the convergence analyst of NEXUS-DC. You never invent "
                       "numbers; every figure you cite must appear in the inputs.",
            )
        except Exception as exc:
            return {
                "narrative": (f"{entity_id} flagged independently by "
                              f"{', '.join(signals)} (score {score}). LLM narrative "
                              f"unavailable: {exc}"),
                "root_cause": "see agent signals",
                "combined_impact": exposure["assumption"],
                "mitigation_options": [s["description"][:120] for s in
                                       list(signals.values())[:3]] or ["review signals"],
            }

    # ------------------------------------------------------------------ run

    def run(self, **_) -> dict:
        events = event_bus.get_all_events()
        by_entity: dict[str, list] = {}
        for e in events:
            by_entity.setdefault(e["entity_id"], []).append(e)

        alerts, converged_entities = [], []
        for entity_id, entity_events in by_entity.items():
            signals = self._agent_signals(entity_events)
            if len(signals) < 2:  # one agent alone never converges
                continue
            on_cp = self._is_on_critical_path(entity_id)
            weight = config.CRITICAL_PATH_WEIGHT if on_cp else 1.0
            score = round(sum(s["risk_score"] for s in signals.values()) * weight, 3)
            if score < config.CONVERGENCE_THRESHOLD:
                continue
            context = self._entity_context(entity_id)
            exposure = self._sla_exposure(context)
            narrative = self._narrate(entity_id, signals, score, context, exposure)
            converged_entities.append(entity_id)
            alerts.append({
                "alert_id": f"CONV-{entity_id}-{_now()[:19]}",
                "entity_id": entity_id,
                "entity_type": (self.kg.get_node(entity_id) or {}).get("node_type", "Unknown"),
                "agents": sorted(signals),
                "agent_signals": signals,
                "convergence_score": score,
                "criticality_weight": weight,
                "on_critical_path": on_cp,
                "threshold": config.CONVERGENCE_THRESHOLD,
                "threshold_note": "0.65 is a configured demo heuristic, not a derived constant",
                "sla_exposure": exposure,
                **narrative,
                "created_at": _now(),
            })

        risk_storm = len(converged_entities) >= config.RISK_STORM_MIN_ENTITIES
        result = {
            "alerts": sorted(alerts, key=lambda a: -a["convergence_score"]),
            "risk_storm": risk_storm,
            "risk_storm_note": (f"RISK STORM: {len(converged_entities)} entities converging "
                                f"simultaneously - systemic project stress"
                                if risk_storm else None),
            "converged_entities": converged_entities,
            "entities_considered": len(by_entity),
            "threshold": config.CONVERGENCE_THRESHOLD,
            "generated_at": _now(),
        }
        with _LOCK:
            fd, tmp = tempfile.mkstemp(dir=str(config.DATA_DIR), suffix=".alerts.tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=1, default=str)
            os.replace(tmp, ALERTS_PATH)
        return result


def load_alerts() -> dict:
    if not ALERTS_PATH.exists():
        return {"alerts": [], "risk_storm": False, "converged_entities": [],
                "entities_considered": 0, "threshold": config.CONVERGENCE_THRESHOLD,
                "generated_at": None}
    with _LOCK:
        return json.loads(ALERTS_PATH.read_text())
