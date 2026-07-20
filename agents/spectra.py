"""SPECTRA - Specification Compliance Agent.

Derives deviations by DETERMINISTIC comparison of submitted parameters against
spec requirements (greater_equal / less_equal / equal / range + unit
conversion + tolerance). The LLM is used only for qualitative/certification
checks and the report narrative. Nothing in the inputs says "deviation" -
if the submittal changes, the findings change.
"""
import json
import re
import time

from agents.base_agent import BaseAgent
from core import config

# convertible units: (from, to) -> multiplier. Only identical physical
# dimensions convert; MVA vs MW are different quantities and never conflated.
_UNIT_FACTORS = {
    ("ms", "s"): 0.001, ("s", "ms"): 1000.0,
    ("kva", "mva"): 0.001, ("mva", "kva"): 1000.0,
    ("kw", "mw"): 0.001, ("mw", "kw"): 1000.0,
    ("kvar", "mvar"): 0.001, ("mvar", "kvar"): 1000.0,
    ("min", "h"): 1 / 60, ("h", "min"): 60.0,
    ("s", "min"): 1 / 60, ("min", "s"): 60.0,
    ("kv", "v"): 1000.0, ("v", "kv"): 0.001,
}
_SEVERITY_RISK = {"CRITICAL": 0.9, "MAJOR": 0.6, "MINOR": 0.3}


_UNIT_ALIASES = {
    "percent": "%", "pct": "%",
    "milliseconds": "ms", "millisecond": "ms",
    "seconds": "s", "second": "s", "sec": "s",
    "minutes": "min", "minute": "min", "mins": "min",
    "hours": "h", "hour": "h", "hrs": "h", "hr": "h",
    "volts": "v", "volt": "v", "kilovolts": "kv", "kilovolt": "kv",
    "hertz": "hz",
}


def _norm_unit(u) -> str:
    u = (u or "").strip().lower()
    return _UNIT_ALIASES.get(u, u)


def _to_float(value):
    if isinstance(value, (int, float)):
        return float(value)
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(value))
    return float(m.group(1)) if m else None


def _convert(value: float, from_unit: str, to_unit: str) -> float | None:
    fu, tu = _norm_unit(from_unit), _norm_unit(to_unit)
    if fu == tu:
        return value
    factor = _UNIT_FACTORS.get((fu, tu))
    return value * factor if factor is not None else None


def _parse_tolerance(tol) -> tuple[str, float] | None:
    """'±10%' -> ('rel', 0.10); '±2' -> ('abs', 2.0)."""
    if not tol:
        return None
    m = re.search(r"±?\s*(\d+(?:\.\d+)?)\s*(%?)", str(tol))
    if not m:
        return None
    return ("rel", float(m.group(1)) / 100) if m.group(2) else ("abs", float(m.group(1)))


class SpectraAgent(BaseAgent):
    name = "SPECTRA"

    # ------------------------------------------------------------ data access

    def _load_inputs(self, refresh: bool | None = None) -> tuple[list, dict]:
        """Cache is the fast path; re-parse + re-extract when the source PDF is
        newer than the cache (this is what makes the demo falsifiable live)."""
        sub_cache = config.CACHE_DIR / "submittal_ups02a.json"
        if refresh is None:
            refresh = (not sub_cache.exists()
                       or config.SUBMITTAL_PDF.stat().st_mtime > sub_cache.stat().st_mtime)
        if refresh:
            from core.document_parser import parse_pdf
            from core.entity_extractor import extract_submittal_parameters
            parsed = parse_pdf(config.SUBMITTAL_PDF)
            submittal = extract_submittal_parameters(parsed)
            sub_cache.write_text(json.dumps(submittal, indent=1, default=str))
            (config.CACHE_DIR / "submittal_document.json").write_text(
                json.dumps(parsed, indent=1, default=str))
        else:
            submittal = json.loads(sub_cache.read_text())
        requirements = json.loads((config.CACHE_DIR / "spec_requirements.json").read_text())
        return requirements, submittal

    # ------------------------------------------------------------- comparison

    def _match_parameter(self, requirement: dict, parameters: list[dict]) -> dict | None:
        """Find the submitted parameter answering a requirement.

        Canonical-name candidates are ranked by qualifier-token overlap between
        the two parameter NAMES (so 'Eco-Mode Efficiency' pairs with
        'Efficiency (economy / eco mode)', not the double-conversion figure),
        with unit convertibility as tiebreaker. Fallback: token overlap >= 2.
        """
        def tokens(name) -> set:
            return set(re.findall(r"[a-z]+", str(name or "").lower()))

        req_unit = requirement.get("unit")
        req_tokens = tokens(requirement.get("parameter"))

        def convertible(p) -> bool:
            v = _to_float(p.get("value"))
            return v is not None and _convert(v, p.get("unit"), req_unit) is not None

        canon = requirement.get("canonical_parameter")
        candidates = [p for p in parameters if canon and p.get("canonical_parameter") == canon]
        if candidates:
            return max(candidates,
                       key=lambda p: (len(req_tokens & tokens(p.get("parameter"))),
                                      convertible(p)))
        best, best_score = None, 0
        for p in parameters:
            score = len(req_tokens & tokens(p.get("parameter")))
            if score > best_score or (score == best_score and best is not None
                                      and convertible(p) and not convertible(best)):
                best, best_score = p, score
        return best if best_score >= 2 else None

    def _compare(self, requirement: dict, param: dict) -> dict | None:
        """Deterministic pass/fail. Returns a comparison record or None when the
        pair is not numerically comparable (routed to the qualitative check)."""
        submitted = _to_float(param.get("value"))
        required = _to_float(requirement.get("value"))
        if submitted is None or required is None:
            return None
        converted = _convert(submitted, param.get("unit"), requirement.get("unit"))
        if converted is None:
            return None
        op = requirement.get("comparison")
        tol = _parse_tolerance(requirement.get("tolerance"))
        if op == "greater_equal":
            ok = converted >= required
        elif op == "less_equal":
            ok = converted <= required
        elif op == "equal":
            margin = (tol[1] * required if tol and tol[0] == "rel"
                      else tol[1] if tol else 1e-9)
            ok = abs(converted - required) <= margin
        elif op == "range":
            upper = _to_float(requirement.get("value_max"))
            ok = required <= converted <= (upper if upper is not None else required)
        else:
            return None
        margin_pct = (abs(converted - required) / abs(required) * 100) if required else 0.0
        return {
            "compliant": ok,
            "submitted_value": submitted,
            "submitted_unit": param.get("unit"),
            "compared_value": round(converted, 6),
            "required_value": required,
            "required_unit": requirement.get("unit"),
            "comparison": op,
            "margin_pct": round(margin_pct, 2),
        }

    # ------------------------------------------------------------ LLM (nuance)

    def _qualitative_review(self, requirements: list, parameters: list) -> list[dict]:
        """Certification/boolean requirements the numeric engine can't judge."""
        quals = [r for r in requirements
                 if r.get("comparison") == "boolean" or _to_float(r.get("value")) is None]
        if not quals:
            return []
        cert_params = [p for p in parameters if _to_float(p.get("value")) is None]
        schema = {
            "type": "object",
            "properties": {"verdicts": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "clause_id": {"type": "string"},
                    "satisfied": {"type": "boolean"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "rationale": {"type": "string"},
                },
                "required": ["clause_id", "satisfied", "confidence", "rationale"],
            }}},
            "required": ["verdicts"],
        }
        try:
            result = self.call_llm_structured(
                "Spec requirements (qualitative/certification):\n"
                + json.dumps(quals, default=str)
                + "\n\nSubmitted non-numeric parameters/certifications:\n"
                + json.dumps(cert_params, default=str)
                + "\n\nFor each requirement, judge whether the submittal satisfies it "
                  "based ONLY on the listed submissions. If the submittal is silent on "
                  "a requirement, satisfied=false with confidence=low.",
                schema=schema, role="pro", temperature=config.TEMP_EXTRACTION,
                system="You are a senior electrical engineer reviewing a vendor "
                       "submittal against specification clauses. Be strict and factual.",
            )
            return result.get("verdicts", [])
        except Exception:
            return []  # qualitative layer is additive; never sinks the run

    def _report_narrative(self, equipment_tag: str, deviations: list) -> str:
        if not deviations:
            return (f"Submittal for {equipment_tag} reviewed: all comparable parameters "
                    f"comply with specification requirements.")
        try:
            return self.call_llm(
                "Write a concise submittal-review finding (max 180 words) for a data-centre "
                "EPC project. These deviations were COMPUTED by deterministic comparison of "
                "the vendor submittal against the specification:\n"
                + json.dumps(deviations, default=str)
                + "\nState each deviation with clause, required vs submitted, and why it "
                  "matters for a Rated-3 (TIA-942) / Tier III (Uptime) facility. Do not "
                  "invent values.",
                role="pro", temperature=config.TEMP_NARRATIVE,
            )
        except Exception:
            lines = [f"{d['clause_id']}: submitted {d['submitted']} vs required "
                     f"{d['required']} ({d['severity']})" for d in deviations]
            return "Deviations found: " + "; ".join(lines)

    # -------------------------------------------------------------------- run

    def run(self, refresh: bool | None = None, **_) -> dict:
        t0 = time.time()
        requirements, submittal = self._load_inputs(refresh)
        tag = submittal.get("equipment_tag", "UPS-02A")
        parameters = submittal.get("parameters", [])

        # requirements that govern this equipment: linked in KG, or UPS-system clauses
        specified = {n["node_id"] for n in self.kg.get_neighbors(tag, "SPECIFIED_BY")}
        relevant = [r for r in requirements
                    if f"SPEC::{r['clause_id']}::{r.get('parameter') or r.get('canonical_parameter') or 'param'}" in specified
                    or tag.upper() in str(r.get("source_text", "")).upper()]
        if not relevant:
            relevant = requirements

        deviations, compliant_checks = [], []
        for req in relevant:
            param = self._match_parameter(req, parameters)
            if param is None:
                continue
            cmp = self._compare(req, param)
            if cmp is None:
                continue
            record = {
                "clause_id": req["clause_id"],
                "parameter": req.get("canonical_parameter") or req.get("parameter"),
                "required": f"{req.get('comparison')} {req.get('value')} {req.get('unit') or ''}".strip(),
                "submitted": f"{cmp['submitted_value']} {cmp['submitted_unit'] or ''}".strip(),
                "margin_pct": cmp["margin_pct"],
                "source_page": param.get("page"),
                "spec_text": req.get("source_text", "")[:200],
            }
            if cmp["compliant"]:
                compliant_checks.append(record)
            else:
                severity = req.get("criticality", "MAJOR")
                risk = min(1.0, _SEVERITY_RISK.get(severity, 0.6) + min(cmp["margin_pct"], 50) / 500)
                deviations.append({**record, "severity": severity, "risk_score": round(risk, 3)})

        qualitative = self._qualitative_review(relevant, parameters)
        for v in qualitative:
            if not v.get("satisfied") and v.get("confidence") in ("high", "medium"):
                deviations.append({
                    "clause_id": v["clause_id"], "parameter": "qualitative",
                    "required": "see clause", "submitted": v["rationale"][:120],
                    "severity": "MINOR", "risk_score": 0.3, "margin_pct": 0.0,
                    "source_page": None, "spec_text": "",
                })

        overall = 0.0
        if deviations:
            overall = min(1.0, max(d["risk_score"] for d in deviations)
                          + 0.02 * (len(deviations) - 1))

        # --- write derived facts to KG + event bus ---
        # re-derivation REPLACES the previous derivation: prune stale deviation
        # nodes so a corrected submittal visibly clears its old findings
        stale = [n for n, data in self.kg.g.nodes(data=True)
                 if data.get("node_type") == "Deviation" and n.startswith(f"DEV::{tag}")]
        for n in stale:
            self.kg.g.remove_node(n)
        for d in deviations:
            dev_id = f"DEV::{tag}::{d['clause_id']}::{d['parameter']}"
            self.kg.add_node(dev_id, "Deviation", attributes=d)
            param_key = d["parameter"]
            for n in self.kg.get_neighbors(tag, "SPECIFIED_BY"):
                if d["clause_id"] in n["node_id"]:
                    self.kg.add_edge(dev_id, n["node_id"], "DEVIATES_FROM")
            for n in self.kg.get_neighbors(tag, "REQUIRES", direction="in"):
                self.kg.add_edge(dev_id, n["node_id"], "IMPACTS")
        reason = (f"{len(deviations)} spec deviation(s): "
                  + ", ".join(d["clause_id"] for d in deviations)) if deviations \
                 else "submittal compliant"
        self.kg.update_node_risk(tag, self.name, overall, reason)
        self.kg.save()

        for d in deviations:
            if d["severity"] in ("CRITICAL", "MAJOR"):
                self.publish_event(
                    "deviation_detected", tag, "Equipment", d["severity"],
                    f"{d['clause_id']} {d['parameter']}: submitted {d['submitted']}, "
                    f"required {d['required']}",
                    d["risk_score"],
                )

        report = self._report_narrative(tag, deviations)
        return {
            "equipment_tag": tag,
            "vendor": submittal.get("vendor"),
            "deviations": deviations,
            "compliant_checks": compliant_checks,
            "qualitative_verdicts": qualitative,
            "overall_risk_score": round(overall, 3),
            "report_text": report,
            "detection_time_seconds": round(time.time() - t0, 1),
        }
