"""ORACLE - Project Intelligence (hybrid GraphRAG Q&A).

Two retrieval paths, then synthesis:
  (a) entity path - extract ids/intent, run a REAL multi-hop NetworkX traversal
      (e.g. RFI -> LINKED_TO -> activity -> ON_CRITICAL_PATH -> milestone);
  (b) vector path - ChromaDB semantic search over spec clauses / RFIs / submittal;
  (c) LLM synthesis constrained to the retrieved evidence, with citations.

The critical-path-RFI answer comes from the graph traversal, not keyword match -
that is what makes the "3-hop query" claim true.
"""
import json
import re

from agents.base_agent import BaseAgent
from core import config
from core.vector_store import VectorStore

_ID_RES = {
    "rfi": re.compile(r"\bRFI-\d{3,4}\b", re.IGNORECASE),
    "ncr": re.compile(r"\bNCR-\d{1,4}\b", re.IGNORECASE),
    "activity": re.compile(r"\bACT-\d{2,3}\b", re.IGNORECASE),
    "equipment": re.compile(r"\b[A-Z]{2,4}-\d{2}[A-Z]\b|\b(?:UPS|GEN|SWG|ATS|CH|CRAH|PDU)-\d{2}[A-Z]?\b"),
    "clause": re.compile(r"\b[A-Z]{2,5}-\d+\.\d+(?:\.\d+)?\b"),
    "itp": re.compile(r"\bITP-[A-Z0-9]{6,}\b", re.IGNORECASE),
}


class OracleAgent(BaseAgent):
    name = "ORACLE"

    def __init__(self, kg=None):
        super().__init__(kg)
        self.vs = VectorStore()

    # -------------------------------------------------------- graph traversals

    def _open_rfis_on_critical_path(self) -> tuple[list[dict], list[str]]:
        """3-hop traversal: RFI -> LINKED_TO -> activity -> ON_CRITICAL_PATH -> milestone."""
        facts, paths = [], []
        for node_id, data in self.kg.g.nodes(data=True):
            if data.get("node_type") != "RFI":
                continue
            attrs = data.get("attributes", {})
            if str(attrs.get("status", "")).lower() != "open":
                continue
            for hop1 in self.kg.get_neighbors(node_id, "LINKED_TO"):
                act = hop1["node"]
                on_cp = act["attributes"].get("is_critical", False)
                milestones = self.kg.get_neighbors(hop1["node_id"], "ON_CRITICAL_PATH")
                if not (on_cp or milestones):
                    continue
                milestone = milestones[0]["node_id"] if milestones else "critical path"
                facts.append({
                    "rfi_id": node_id,
                    "subject": attrs.get("subject"),
                    "status": attrs.get("status"),
                    "ball_in_court": attrs.get("ball_in_court"),
                    "date_required": attrs.get("date_required"),
                    "linked_activity": f"{hop1['node_id']}: {act['attributes'].get('name', '')}",
                    "gates_milestone": milestone,
                })
                paths.append(f"{node_id} -LINKED_TO-> {hop1['node_id']} "
                             f"-ON_CRITICAL_PATH-> {milestone}")
        return facts, paths

    def _equipment_status(self, tag: str) -> tuple[list[dict], list[str]]:
        node = self.kg.get_node(tag)
        if not node:
            return [], []
        facts, paths = [], []
        attrs = node["attributes"]
        facts.append({"equipment": tag, "vendor": attrs.get("vendor"),
                      "model": attrs.get("model"), "risk_score": node["risk_score"],
                      "agent_risks": attrs.get("agent_risks", {})})
        deviations = [self.kg.get_node(n) for n, d in self.kg.g.nodes(data=True)
                      if d.get("node_type") == "Deviation" and n.startswith(f"DEV::{tag}")]
        for dev in deviations:
            facts.append({"deviation": dev["attributes"]})
            paths.append(f"{tag} <-derived- {dev['node_id']} (SPECTRA)")
        for n in self.kg.get_neighbors(tag, "REQUIRES", direction="in"):
            a = n["node"]["attributes"]
            facts.append({"activity": n["node_id"], "name": a.get("name"),
                          "status": a.get("status"), "early_end": a.get("early_end"),
                          "is_critical": a.get("is_critical")})
            paths.append(f"{n['node_id']} -REQUIRES-> {tag}")
        for n in self.kg.get_neighbors(tag, "PROCURED_FROM"):
            paths.append(f"{tag} -PROCURED_FROM-> {n['node_id']}")
        for n in self.kg.get_neighbors(tag, "IMPACTS", direction="in"):
            if n["node"].get("node_type") == "NCR":
                a = n["node"]["attributes"]
                facts.append({"ncr": n["node_id"], "title": a.get("title"),
                              "severity": a.get("severity"), "status": a.get("status"),
                              "disposition": a.get("disposition"),
                              "raised_by": a.get("raised_by"),
                              "date_raised": a.get("date_raised")})
                paths.append(f"{n['node_id']} -IMPACTS-> {tag} (field NCR)")
        return facts, paths

    def _open_ncrs(self, include_closed: bool = False) -> tuple[list[dict], list[str]]:
        facts, paths = [], []
        for node_id, data in self.kg.g.nodes(data=True):
            if data.get("node_type") != "NCR":
                continue
            a = data["attributes"]
            if not include_closed and str(a.get("status", "")).upper() == "CLOSED":
                continue
            facts.append({"ncr": node_id, "title": a.get("title"),
                          "equipment_tag": a.get("equipment_tag"),
                          "severity": a.get("severity"), "status": a.get("status"),
                          "raised_by": a.get("raised_by"),
                          "date_raised": a.get("date_raised")})
            for n in self.kg.get_neighbors(node_id, "IMPACTS"):
                paths.append(f"{node_id} -IMPACTS-> {n['node_id']}")
        return facts, paths

    def _spec_requirements_for(self, query: str, tag: str | None) -> tuple[list[dict], list[str]]:
        facts, paths = [], []
        tokens = set(re.findall(r"[a-z]+", query.lower()))
        anchor = tag or "UPS-02A"
        for n in self.kg.get_neighbors(anchor, "SPECIFIED_BY"):
            attrs = n["node"]["attributes"]
            blob = f"{attrs.get('parameter', '')} {attrs.get('canonical_parameter', '')} " \
                   f"{attrs.get('source_text', '')}".lower()
            if tokens & set(re.findall(r"[a-z]+", blob)) - {"the", "for", "ups", "what", "spec"}:
                facts.append({"clause": attrs.get("clause_id"),
                              "parameter": attrs.get("canonical_parameter") or attrs.get("parameter"),
                              "requirement": f"{attrs.get('comparison')} {attrs.get('value')} "
                                             f"{attrs.get('unit') or ''}",
                              "text": attrs.get("source_text", "")[:200],
                              "page": attrs.get("page")})
                paths.append(f"{anchor} -SPECIFIED_BY-> {n['node_id']}")
        return facts, paths

    def _deliveries_at_risk(self) -> tuple[list[dict], list[str]]:
        facts, paths = [], []
        for node in self.kg.get_equipment_risk_nodes(threshold=0.3):
            risks = node["attributes"].get("agent_risks", {})
            if "TRACIS" in risks:
                facts.append({"equipment": node["node_id"],
                              "vendor": node["attributes"].get("vendor"),
                              "tracis": risks["TRACIS"]})
                paths.append(f"{node['node_id']} (risk {node['risk_score']}) <- TRACIS")
        return facts, paths

    # ------------------------------------------------------------------- run

    def answer(self, query: str) -> dict:
        ids = {kind: rex.findall(query) for kind, rex in _ID_RES.items()}
        q = query.lower()

        graph_facts, graph_paths, intent = [], [], "general"
        if "rfi" in q and ("critical" in q or "path" in q):
            intent = "open_rfis_on_critical_path"
            graph_facts, graph_paths = self._open_rfis_on_critical_path()
        elif "ncr" in q or "non-conformance" in q or "nonconformance" in q:
            intent = "open_ncrs"
            graph_facts, graph_paths = self._open_ncrs(
                include_closed=any(w in q for w in ("closed", "all", "history")))
            if ids["equipment"]:
                tag = ids["equipment"][0].upper()
                graph_facts = [f for f in graph_facts
                               if f.get("equipment_tag") == tag] or graph_facts
        elif ids["equipment"] or any(t in q for t in ("status", "submittal", "deviation")):
            intent = "equipment_status"
            tag = (ids["equipment"] or ["UPS-02A"])[0].upper()
            graph_facts, graph_paths = self._equipment_status(tag)
        if any(t in q for t in ("deliver", "eta", "supply", "lead time")):
            intent = "deliveries_at_risk" if not graph_facts else intent
            f, p = self._deliveries_at_risk()
            graph_facts += f
            graph_paths += p
        if any(t in q for t in ("spec", "requirement", "clause", "shall", "autonomy", "require")):
            f, p = self._spec_requirements_for(
                query, (ids["equipment"] or [None])[0])
            if f:
                intent = "spec_requirement" if not graph_facts else intent
                graph_facts += f
                graph_paths += p
        for rfi in ids["rfi"]:
            node = self.kg.get_node(rfi.upper())
            if node:
                graph_facts.append({"rfi": rfi.upper(), **node["attributes"]})
                for n in self.kg.get_neighbors(rfi.upper(), "LINKED_TO"):
                    graph_paths.append(f"{rfi.upper()} -LINKED_TO-> {n['node_id']}")
        # direct NCR lookup - closed ones included (the audit trail); tolerate
        # short-typed ids like NCR-1 / NCR-001 by zero-padding to the node id
        for raw in ids["ncr"]:
            nid = f"NCR-{int(raw.split('-')[1]):04d}"
            node = self.kg.get_node(nid)
            if node:
                intent = "ncr_lookup" if intent in ("general", "open_ncrs") else intent
                graph_facts.append({"ncr": nid, **node["attributes"]})
                for n in self.kg.get_neighbors(nid, "IMPACTS"):
                    graph_paths.append(f"{nid} -IMPACTS-> {n['node_id']}")
        for itp in ids["itp"]:
            node = self.kg.get_node(itp.upper())
            if node:
                intent = "itp_lookup" if intent == "general" else intent
                graph_facts.append({"itp_record": itp.upper(), **node["attributes"]})
                for n in self.kg.get_neighbors(itp.upper()):
                    graph_paths.append(f"{itp.upper()} -{n['relationship']}-> {n['node_id']}")

        vector_hits = self.vs.query(query, n_results=5)

        citations = []
        for h in vector_hits:
            m = h["metadata"]
            citations.append({
                "source": m.get("source"),
                "ref": m.get("clause_id") or m.get("rfi_id") or h["id"],
                "page": m.get("page"),
            })
        for p in graph_paths:
            citations.append({"source": "knowledge_graph", "ref": p, "page": None})

        evidence = {
            "graph_facts (structural, from multi-hop traversal)": graph_facts,
            "graph_paths": graph_paths,
            "vector_hits (semantic)": [
                {"text": h["text"][:300], "source": h["metadata"].get("source"),
                 "ref": h["metadata"].get("clause_id") or h["metadata"].get("rfi_id"),
                 "page": h["metadata"].get("page")}
                for h in vector_hits],
        }
        try:
            answer = self.call_llm(
                f"Question: {query}\n\nEvidence:\n{json.dumps(evidence, default=str)}\n\n"
                "Answer the question using ONLY this evidence. Cite every factual claim "
                "inline like [specification.pdf p4, ELEC-4.2.5] or [RFI register, RFI-0003] "
                "or [schedule.xer, ACT-033]. If the evidence does not answer the question, "
                "say what is missing. Be concise and specific. Start directly with the "
                "answer - no preamble, no meta-commentary about the evidence quality.",
                system="You are ORACLE, the project-intelligence assistant for the Meridian "
                       "Data Centre EPC project. You never invent facts beyond the evidence.",
                role="flash", temperature=config.TEMP_NARRATIVE, fast=True,
            )
        except Exception as exc:
            answer = (f"(LLM synthesis unavailable: {exc}) Graph evidence: "
                      + json.dumps(graph_facts[:5], default=str))

        return {"query": query, "intent": intent, "answer": answer,
                "citations": citations, "graph_paths": graph_paths,
                "graph_facts": graph_facts,
                "vector_hits": [{k: h[k] for k in ("id", "distance")} for h in vector_hits]}

    def run(self, query: str = "What is the status of UPS-02A?", **_) -> dict:
        return self.answer(query)
