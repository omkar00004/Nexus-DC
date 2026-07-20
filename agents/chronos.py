"""CHRONOS - Predictive Schedule Risk Agent.

Derives the delay cascade from the parsed XER: forward-pass CPM over the
relationship network (FS/SS/FF + lag, workday arithmetic), driving-path trace,
then Monte Carlo (n=5000) on remaining durations. The completion date is
COMPUTED - the LLM narrates whatever the numbers say, never a hardcoded date.

Duration model: triangular distribution per QSRA convention (right-skewed:
delays more likely than early finishes; avoids negative durations).

    mode  = planned_remaining (+ derived slip where the forecast carries it)
    left  = max(mode - s, 0.5 * duration)   # short optimistic side, floored
    right = mode + 2 * s                    # longer pessimistic tail

with spread s per activity class: max(0.1 * duration, 3) for PROCUREMENT/
DELIVERY-type activities (logistics tail risk), max(0.1 * duration, 1) for
in-progress site work (remaining effort is observable), 0.1 * duration for
routine site activities - a flat 3-workday floor on every short test step
would overstate path variance ~4x.
"""
import json
import re
from datetime import date

import networkx as nx
import numpy as np

from agents.base_agent import BaseAgent
from core import config

N_SIMULATIONS = 5000
_RNG_SEED = 42  # reproducible demo runs; remove for production use
_LOGISTICS_RE = re.compile(r"deliver|receiv|procure|fabricat|ship|manufactur", re.IGNORECASE)


def _d(iso: str | None):
    return np.datetime64(iso[:10], "D") if iso else None


def _busdays(start, end) -> int:
    return int(np.busday_count(start, end))


def _offset(start, workdays: float):
    return np.busday_offset(start, int(round(max(workdays, 0))), roll="forward")


class ChronosAgent(BaseAgent):
    name = "CHRONOS"

    # ------------------------------------------------------------- CPM engine

    def _load_schedule(self) -> dict:
        return json.loads((config.CACHE_DIR / "schedule.json").read_text())

    def _forward_pass(self, activities: dict, topo: list, preds: dict,
                      data_date, durations: dict) -> tuple[dict, dict, dict]:
        """Workday-space forward pass. Returns (start, finish, driver) keyed by
        task_code, where driver[t] is the predecessor that set t's early start."""
        start, finish, driver = {}, {}, {}
        for code in topo:
            a = activities[code]
            dur = durations[code]
            if a["status"] == "TK_Complete":
                s = _busdays(data_date, _d(a["actual_start"]) or data_date)
                f = _busdays(data_date, _d(a["actual_end"]) or data_date)
                start[code], finish[code], driver[code] = s, f, None
                continue
            if a["status"] == "TK_Active":
                s = _busdays(data_date, _d(a["actual_start"]) or data_date)
                start[code], finish[code], driver[code] = s, max(s, 0) + dur, None
                # remaining work runs from the data date
                finish[code] = max(0, s) + dur if s < 0 else s + dur
                finish[code] = max(finish[code], 0 + dur)
                continue
            best_s, who = 0, None  # unconstrained work can start at the data date
            planned = _d(a["target_start"])
            if planned is not None:
                best_s = max(best_s, _busdays(data_date, planned))
            for p, link, lag in preds.get(code, []):
                if link == "SS":
                    cand = start[p] + lag
                elif link == "FF":
                    cand = finish[p] + lag - dur
                else:  # FS (and SF treated as FS - not present in P6 exports here)
                    cand = finish[p] + lag
                if cand > best_s:
                    best_s, who = cand, p
            start[code], finish[code], driver[code] = best_s, best_s + dur, who
        return start, finish, driver

    def _remaining_durations(self, activities: dict, data_date) -> dict:
        """Workday durations for the CURRENT forecast. For in-progress tasks the
        P6 forecast finish (early_end) is a parsed fact - the remaining duration
        derived from it carries the slip; everything downstream is OUR derivation."""
        durations = {}
        for code, a in activities.items():
            if a["status"] == "TK_Complete":
                durations[code] = 0
            elif a["status"] == "TK_Active":
                fc = _d(a["early_end"]) or _d(a["target_end"]) or data_date
                durations[code] = max(_busdays(data_date, max(fc, data_date)), 0)
            else:
                durations[code] = int(round(a["original_duration_days"] or 0))
        return durations

    def _trace_driving_path(self, driver: dict, milestone: str) -> list[str]:
        path, node = [], milestone
        while node is not None:
            path.append(node)
            node = driver.get(node)
        return list(reversed(path))

    # ------------------------------------------------------------ Monte Carlo

    def _monte_carlo(self, activities: dict, topo: list, preds: dict,
                     durations: dict, data_date, milestone: str) -> dict:
        rng = np.random.default_rng(_RNG_SEED)
        n = N_SIMULATIONS
        start_v, finish_v = {}, {}
        for code in topo:
            a = activities[code]
            dur = durations[code]
            if a["status"] == "TK_Complete":
                f = _busdays(data_date, _d(a["actual_end"]) or data_date)
                start_v[code] = np.full(n, float(min(f, 0)))
                finish_v[code] = np.full(n, float(f))
                continue
            if dur > 0:
                if _LOGISTICS_RE.search(a["name"]):
                    s = max(0.1 * dur, 3.0)        # logistics tail risk
                elif a["status"] == "TK_Active":
                    s = max(0.1 * dur, 1.0)        # remaining site work is observable
                else:
                    s = 0.1 * dur
                # right-skewed triangular (QSRA convention): left side short and
                # floored above zero, pessimistic tail twice as long
                mode = float(dur)
                left = max(mode - s, 0.5 * dur)
                right = mode + 2 * s
                sampled = rng.triangular(left, mode, right, n)
            else:
                sampled = np.zeros(n)  # zero-duration milestones stay at 0
            base = np.zeros(n)
            planned = _d(a["target_start"])
            if a["status"] == "TK_NotStart" and planned is not None:
                base = np.maximum(base, float(_busdays(data_date, planned)))
            for p, link, lag in preds.get(code, []):
                if link == "SS":
                    cand = start_v[p] + lag
                elif link == "FF":
                    cand = finish_v[p] + lag - sampled
                else:
                    cand = finish_v[p] + lag
                base = np.maximum(base, cand)
            start_v[code] = base
            finish_v[code] = base + sampled
        return finish_v[milestone]

    # -------------------------------------------------------------- narrative

    def _narrate(self, facts: dict) -> dict:
        schema = {
            "type": "object",
            "properties": {
                "causal_narrative": {"type": "string"},
                "mitigation_options": {"type": "array", "items": {"type": "string"},
                                       "minItems": 3, "maxItems": 3},
            },
            "required": ["causal_narrative", "mitigation_options"],
        }
        try:
            return self.call_llm_structured(
                "You are the schedule-risk analyst on a data-centre EPC project. "
                "These figures were COMPUTED by CPM + Monte Carlo from the live "
                "P6 programme - use them exactly, do not invent dates:\n"
                + json.dumps(facts, default=str)
                + "\n\nWrite (1) causal_narrative: a crisp cause-and-effect account "
                  "of how the origin slip propagates down the driving path to the "
                  "Rated-3 (TIA-942) / Tier III (Uptime) milestone, quoting the "
                  "computed P50/P80/P90 dates and probabilities; (2) exactly 3 "
                  "mitigation_options with expected schedule effect.",
                schema=schema, role="pro", temperature=config.TEMP_NARRATIVE,
                max_tokens=config.MAX_TOKENS_NARRATIVE,
            )
        except Exception:
            return {
                "causal_narrative": (
                    f"Driving path {' -> '.join(facts['driving_path_codes'])}: origin slip "
                    f"{facts['origin_slip_workdays']} workdays at {facts['origin_activity']}; "
                    f"P50 {facts['p50']}, P80 {facts['p80']}, P90 {facts['p90']}; "
                    f"P(miss SLA {facts['sla_date']}) = {facts['sla_breach_risk']}"),
                "mitigation_options": [
                    "Expedite the slipped delivery (air freight / vendor premium).",
                    "Resequence downstream commissioning to overlap non-dependent tests.",
                    "Negotiate interim milestone relief with the owner before breach."],
            }

    # -------------------------------------------------------------------- run

    def run(self, **_) -> dict:
        sched = self._load_schedule()
        data_date = _d(sched["data_date"]) or np.datetime64(date.today(), "D")
        activities = {a["task_code"]: a for a in sched["activities"]}

        g = nx.DiGraph()
        g.add_nodes_from(activities)
        preds: dict[str, list] = {}
        for r in sched["relationships"]:
            if r["predecessor"] in activities and r["successor"] in activities:
                g.add_edge(r["predecessor"], r["successor"])
                preds.setdefault(r["successor"], []).append(
                    (r["predecessor"], r["link"], r["lag_days"] or 0))
        topo = list(nx.topological_sort(g))

        milestone = next((c for c, a in activities.items()
                          if a["is_milestone"] and "commissioning complete" in a["name"].lower()),
                         topo[-1])

        durations = self._remaining_durations(activities, data_date)
        start, finish, driver = self._forward_pass(activities, topo, preds, data_date, durations)

        # derived slip vs the P6 baseline (target dates)
        slips = {}
        for code, a in activities.items():
            if a["target_end"] and a["status"] != "TK_Complete":
                slips[code] = finish[code] - _busdays(data_date, _d(a["target_end"]))
        driving_path = self._trace_driving_path(driver, milestone)
        origin = next((c for c in driving_path if slips.get(c, 0) > 0), driving_path[0])

        baseline_finish = _d(activities[milestone]["target_end"])
        current_finish = _offset(data_date, finish[milestone])

        mc_finish = self._monte_carlo(activities, topo, preds, durations, data_date, milestone)
        p50, p80, p90 = (str(_offset(data_date, float(np.percentile(mc_finish, q))))
                         for q in (50, 80, 90))
        sla_wd = _busdays(data_date, baseline_finish)
        sla_breach_risk = round(float(np.mean(mc_finish > sla_wd)), 3)
        expected_delay_days = int((np.datetime64(p50) - baseline_finish).astype(int))

        at_risk = sorted(
            [{"task_code": c, "name": activities[c]["name"],
              "derived_slip_workdays": s,
              "is_critical": activities[c]["is_critical"]}
             for c, s in slips.items() if s > 0 and not activities[c]["is_milestone"]],
            key=lambda x: -x["derived_slip_workdays"])
        critical_path = [
            {"task_code": c, "name": activities[c]["name"],
             "forecast_finish": str(_offset(data_date, finish[c])),
             "on_driving_path": c in driving_path}
            for c in topo if activities[c]["is_critical"]]

        facts = {
            "project": sched["project_name"],
            "data_date": sched["data_date"][:10],
            "milestone": f"{milestone} {activities[milestone]['name']}",
            "baseline_finish": str(baseline_finish),
            "deterministic_forecast_finish": str(current_finish),
            "p50": p50, "p80": p80, "p90": p90,
            "sla_date": str(baseline_finish),
            "sla_breach_risk": sla_breach_risk,
            "expected_delay_days": expected_delay_days,
            "origin_activity": f"{origin} {activities[origin]['name']}",
            "origin_slip_workdays": slips.get(origin, 0),
            "driving_path_codes": driving_path,
            "driving_path": [f"{c}: {activities[c]['name']}" for c in driving_path],
        }
        narrative = self._narrate(facts)

        # --- KG + events: schedule risk lands on the milestone AND on the
        # equipment whose activities drive the slip (that's what converges) ---
        milestone_risk = min(1.0, 0.4 + sla_breach_risk * 0.6)
        # structured MC results on the milestone node - the Convergence Engine
        # reads delay_days from here for the SLA-exposure computation
        self.kg.add_node(milestone, "Milestone", attributes={
            "mc_p50": p50, "mc_p80": p80, "mc_p90": p90,
            "baseline_finish": str(baseline_finish),
            "expected_delay_days": expected_delay_days,
            "sla_breach_risk": sla_breach_risk,
        })
        self.kg.update_node_risk(
            milestone, self.name, milestone_risk,
            f"P90 {p90} vs SLA {baseline_finish}; breach risk {sla_breach_risk}")
        self.publish_event(
            "milestone_at_risk", milestone, "Milestone",
            "CRITICAL" if sla_breach_risk > 0.5 else "HIGH",
            f"{activities[milestone]['name']}: P90 {p90} vs baseline {baseline_finish} "
            f"(breach probability {sla_breach_risk:.0%})",
            milestone_risk)

        origin_slip = slips.get(origin, 0)
        for n in self.kg.get_neighbors(origin, "REQUIRES"):
            if n["node"]["node_type"] == "Equipment":
                eq_risk = min(1.0, 0.3 + origin_slip / 30 * 0.5)
                self.kg.update_node_risk(
                    n["node_id"], self.name, eq_risk,
                    f"driving-path slip: {origin} +{origin_slip} workdays -> "
                    f"milestone P90 {p90}")
                self.publish_event(
                    "schedule_slip", n["node_id"], "Equipment",
                    "HIGH" if origin_slip >= 10 else "MEDIUM",
                    f"{origin} ({activities[origin]['name']}) slipped +{origin_slip} "
                    f"workdays on the driving path to {milestone}",
                    eq_risk)
        self.kg.save()

        return {
            "critical_path_activities": critical_path,
            "at_risk_activities": at_risk,
            "monte_carlo": {
                "n_simulations": N_SIMULATIONS,
                "p50_completion": p50, "p80_completion": p80, "p90_completion": p90,
                "baseline_finish": str(baseline_finish),
                "deterministic_forecast": str(current_finish),
                "delay_probability": sla_breach_risk,
                "expected_delay_days": expected_delay_days,
            },
            "causal_narrative": narrative["causal_narrative"],
            "mitigation_options": narrative["mitigation_options"],
            "p90_completion_date": p90,
            "sla_breach_risk": sla_breach_risk,
        }
