"""Bi-temporal knowledge graph: in-memory NetworkX DiGraph, persisted to JSON.

Every node/edge carries two timestamps:
- valid_time:       when the fact was true in the project (from the data)
- transaction_time: when the system learned it (wall-clock at write)

`get_node_as_of(node_id, ts)` answers "what did we know about X at time ts" -
prior states are snapshotted into a per-node history on every mutation.

Node types: Equipment, Specification, ScheduleActivity, Milestone, Vendor,
RFI, TestProcedure, Deviation.
Edge types: SPECIFIED_BY, PROCURED_FROM, REQUIRES, BLOCKS, DEVIATES_FROM,
TESTS, TESTS_AGAINST, ON_CRITICAL_PATH, IMPACTS, LINKED_TO, RESOLVES.
"""
import json
import os
import re
import tempfile
from datetime import datetime, timezone

import networkx as nx
from filelock import FileLock

from core import config

# equipment tags like UPS-02A / GEN-01 / SWG-01 (excludes ACT-0xx, RFI-00xx)
_TAG_RE = re.compile(r"\b([A-Z]{2,4}-\d{2}[A-Z]?)\b")
_TAG_DENYLIST = {"ACT", "RFI", "TIA", "CX"}

# equipment-tag prefix -> keywords that tie a spec clause to that equipment type
_PREFIX_KEYWORDS = {
    "UPS": ["UPS", "UNINTERRUPTIBLE"],
    "GEN": ["GENERATOR", "GENSET", "STANDBY ENGINE"],
    "CH": ["CHILLER", "CHILLED WATER PLANT"],
    "CHWP": ["CHILLED WATER PUMP"],
    "CRAH": ["CRAH", "COMPUTER ROOM AIR"],
    "SWG": ["SWITCHGEAR"],
    "ATS": ["AUTOMATIC TRANSFER SWITCH", "TRANSFER SWITCH"],
    "PDU": ["PDU", "POWER DISTRIBUTION UNIT"],
    "BATT": ["BATTERY"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeGraph:
    def __init__(self):
        self.g = nx.DiGraph()

    # ------------------------------------------------------------------ CRUD

    def add_node(self, node_id: str, node_type: str, attributes: dict | None = None,
                 valid_time: str | None = None) -> dict:
        now = _now()
        if self.g.has_node(node_id):
            self._snapshot(node_id)
            data = self.g.nodes[node_id]
            data["attributes"] = {**data.get("attributes", {}), **(attributes or {})}
            data["valid_time"] = valid_time or data.get("valid_time") or now
            data["transaction_time"] = now
        else:
            self.g.add_node(
                node_id,
                node_type=node_type,
                attributes=attributes or {},
                risk_score=0.0,
                risk_history=[],
                valid_time=valid_time or now,
                transaction_time=now,
                history=[],
            )
        return self.g.nodes[node_id]

    def add_edge(self, src: str, dst: str, relationship: str,
                 attributes: dict | None = None, valid_time: str | None = None) -> None:
        now = _now()
        self.g.add_edge(
            src, dst,
            relationship=relationship,
            attributes=attributes or {},
            valid_time=valid_time or now,
            transaction_time=now,
        )

    def get_node(self, node_id: str) -> dict | None:
        if not self.g.has_node(node_id):
            return None
        return {"node_id": node_id, **self.g.nodes[node_id]}

    def get_neighbors(self, node_id: str, relationship: str | None = None,
                      direction: str = "out") -> list[dict]:
        """Neighbours of node_id, optionally filtered by edge relationship.

        direction: "out" (successors), "in" (predecessors), or "both".
        Returns [{node_id, relationship, direction, node}].
        """
        if not self.g.has_node(node_id):
            return []
        results = []
        pairs = []
        if direction in ("out", "both"):
            pairs += [(nbr, self.g.edges[node_id, nbr], "out") for nbr in self.g.successors(node_id)]
        if direction in ("in", "both"):
            pairs += [(nbr, self.g.edges[nbr, node_id], "in") for nbr in self.g.predecessors(node_id)]
        for nbr, edge, dirn in pairs:
            if relationship and edge.get("relationship") != relationship:
                continue
            results.append({
                "node_id": nbr,
                "relationship": edge.get("relationship"),
                "direction": dirn,
                "edge_attributes": edge.get("attributes", {}),
                "node": self.get_node(nbr),
            })
        return results

    # ------------------------------------------------------- risk & temporal

    def update_node_risk(self, node_id: str, agent: str, score: float, reason: str) -> dict:
        if not self.g.has_node(node_id):
            self.add_node(node_id, "Equipment")
        self._snapshot(node_id)
        data = self.g.nodes[node_id]
        agent_risks = data["attributes"].setdefault("agent_risks", {})
        entry = {"score": round(float(score), 3), "reason": reason, "ts": _now()}
        agent_risks[agent] = entry
        data["risk_score"] = max(r["score"] for r in agent_risks.values())
        data["risk_history"].append({"agent": agent, **entry})
        data["transaction_time"] = entry["ts"]
        return self.get_node(node_id)

    def get_node_as_of(self, node_id: str, ts: str) -> dict | None:
        """Node state as KNOWN at time ts (transaction-time query)."""
        if not self.g.has_node(node_id):
            return None
        data = self.g.nodes[node_id]
        versions = list(data.get("history", [])) + [
            {k: v for k, v in data.items() if k != "history"}
        ]
        known = [v for v in versions if v.get("transaction_time", "") <= ts]
        if not known:
            return None  # the system did not know this node yet at ts
        return {"node_id": node_id, "as_of": ts, **max(known, key=lambda v: v["transaction_time"])}

    def _snapshot(self, node_id: str) -> None:
        data = self.g.nodes[node_id]
        state = {k: v for k, v in data.items() if k != "history"}
        data.setdefault("history", []).append(json.loads(json.dumps(state, default=str)))

    # ----------------------------------------------------------- query helpers

    def get_equipment_risk_nodes(self, threshold: float = 0.5) -> list[dict]:
        return [
            self.get_node(n) for n, d in self.g.nodes(data=True)
            if d.get("node_type") == "Equipment" and d.get("risk_score", 0) > threshold
        ]

    def get_critical_path_activities(self) -> list[dict]:
        out = [
            self.get_node(n) for n, d in self.g.nodes(data=True)
            if d.get("node_type") in ("ScheduleActivity", "Milestone")
            and d.get("attributes", {}).get("is_critical")
        ]
        return sorted(out, key=lambda n: n["attributes"].get("early_start") or "")

    def find_convergence_candidates(self) -> dict[str, list[str]]:
        """Entities flagged by >=2 DISTINCT agents on the event bus."""
        from core import event_bus  # local import: event_bus has no KG dependency

        by_entity: dict[str, set] = {}
        for e in event_bus.get_all_events():
            by_entity.setdefault(e["entity_id"], set()).add(e["agent"])
        return {eid: sorted(agents) for eid, agents in by_entity.items() if len(agents) >= 2}

    # ------------------------------------------------------------ persistence

    def save(self, path=None) -> None:
        path = str(path or config.KG_PATH)
        payload = nx.node_link_data(self.g, edges="links")
        with FileLock(path + ".lock"):
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".kg.tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, default=str)
            os.replace(tmp, path)

    def load(self, path=None) -> "KnowledgeGraph":
        path = str(path or config.KG_PATH)
        with FileLock(path + ".lock"):
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        self.g = nx.node_link_graph(payload, directed=True, edges="links")
        return self

    # ------------------------------------------------------------- population

    def populate_from_cache(self, cache_dir=None, procedures_path=None) -> dict:
        """Build the graph from parsed source facts (data/cache + procedures)."""
        cache_dir = cache_dir or config.CACHE_DIR
        procedures_path = procedures_path or config.PROCEDURES_PATH
        counts = {"nodes": 0, "edges": 0}

        def _load(name):
            p = cache_dir / name
            return json.loads(p.read_text()) if p.exists() else None

        # --- schedule: activities, milestone, dependencies, critical path ---
        schedule = _load("schedule.json")
        finish_milestone = None
        if schedule:
            data_date = schedule.get("data_date")
            for a in schedule["activities"]:
                ntype = "Milestone" if a["is_milestone"] else "ScheduleActivity"
                self.add_node(a["task_code"], ntype, attributes=a, valid_time=data_date)
                if a["is_milestone"] and "commissioning complete" in a["name"].lower():
                    finish_milestone = a["task_code"]
            if finish_milestone is None:  # fall back to the latest milestone
                miles = [a for a in schedule["activities"] if a["is_milestone"]]
                if miles:
                    finish_milestone = max(miles, key=lambda a: a.get("early_end") or "")["task_code"]
            for r in schedule["relationships"]:
                self.add_edge(r["predecessor"], r["successor"], "BLOCKS",
                              attributes={"link": r["link"], "lag_days": r["lag_days"]},
                              valid_time=data_date)
            if finish_milestone:
                for a in schedule["activities"]:
                    if a["is_critical"] and a["task_code"] != finish_milestone:
                        self.add_edge(a["task_code"], finish_milestone, "ON_CRITICAL_PATH",
                                      valid_time=data_date)
            # equipment discovered from activity names -> REQUIRES edges
            for a in schedule["activities"]:
                for tag in _TAG_RE.findall(a["name"]):
                    if tag.split("-")[0] in _TAG_DENYLIST:
                        continue
                    if not self.g.has_node(tag):
                        self.add_node(tag, "Equipment",
                                      attributes={"tag": tag, "discovered_in": []},
                                      valid_time=data_date)
                    disc = self.g.nodes[tag]["attributes"].setdefault("discovered_in", [])
                    if a["task_code"] not in disc:
                        disc.append(a["task_code"])
                    self.add_edge(a["task_code"], tag, "REQUIRES", valid_time=data_date)

        # --- submittal: equipment detail, vendor ---
        submittal = _load("submittal_ups02a.json")
        if submittal:
            tag = submittal["equipment_tag"]
            self.add_node(tag, "Equipment", attributes={
                "tag": tag,
                "vendor": submittal.get("vendor"),
                "model": submittal.get("model"),
                "submitted_parameters": submittal.get("parameters", []),
            }, valid_time=submittal.get("submittal_date"))
            vendor = submittal.get("vendor")
            if vendor:
                self.add_node(vendor, "Vendor", attributes={"name": vendor})
                self.add_edge(tag, vendor, "PROCURED_FROM")

        # --- spec clauses + SPECIFIED_BY links ---
        requirements = _load("spec_requirements.json")
        if requirements:
            equipment_ids = [n for n, d in self.g.nodes(data=True)
                             if d.get("node_type") == "Equipment"]
            for r in requirements:
                param = r.get("parameter") or r.get("canonical_parameter") or "param"
                clause_node = f"SPEC::{r['clause_id']}::{param}"
                self.add_node(clause_node, "Specification", attributes=r)
                blob = f"{r.get('system', '')} {r.get('parameter', '')} {r.get('source_text', '')}".upper()
                for eq in equipment_ids:
                    keywords = _PREFIX_KEYWORDS.get(eq.split("-")[0], [])
                    if eq.upper() in blob or any(k in blob for k in keywords):
                        self.add_edge(eq, clause_node, "SPECIFIED_BY")

        # --- RFIs ---
        rfis = _load("rfi_register.json")
        if rfis:
            for r in rfis:
                self.add_node(r["rfi_id"], "RFI", attributes=r,
                              valid_time=r.get("date_raised"))
                linked = r.get("linked_activity")
                if linked and self.g.has_node(linked):
                    self.add_edge(r["rfi_id"], linked, "LINKED_TO",
                                  valid_time=r.get("date_raised"))

        # --- commissioning procedures ---
        if procedures_path and procedures_path.exists():
            doc = json.loads(procedures_path.read_text())
            for p in (doc if isinstance(doc, list) else doc.get("procedures", [])):
                pid = p.get("procedure_id") or p.get("id")
                self.add_node(pid, "TestProcedure", attributes=p)
                for tag in set(_TAG_RE.findall(json.dumps(p))):
                    if tag.split("-")[0] not in _TAG_DENYLIST and self.g.has_node(tag):
                        self.add_edge(pid, tag, "TESTS")

        counts["nodes"] = self.g.number_of_nodes()
        counts["edges"] = self.g.number_of_edges()
        return counts
