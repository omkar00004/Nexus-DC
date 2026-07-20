"""Non-Conformance Reports - the human-initiated sibling of GUIDE's auto-RFI.

An NCR is a formal QA record with a lifecycle:

    OPEN -> DISPOSITIONED (use-as-is / rework / reject) -> CLOSED

The record of authority is data/ncrs.json plus an NCR node in the knowledge
graph; the formal PDF is GENERATED from the record for signature - document
as output, never input. Filing publishes a FIELD event to the bus, so an NCR
on UPS-02A becomes one more independent signal to the Convergence Engine,
exactly like an agent's. Closing the NCR withdraws that signal.
"""
import json
from datetime import datetime, timezone

import fitz  # PyMuPDF

from core import config, event_bus
from core.knowledge_graph import KnowledgeGraph

from filelock import FileLock

NCRS_PATH = config.DATA_DIR / "ncrs.json"
_LOCK = FileLock(str(NCRS_PATH) + ".lock")

SEVERITIES = ("CRITICAL", "MAJOR", "MINOR")
SEVERITY_RISK = {"CRITICAL": 0.9, "MAJOR": 0.7, "MINOR": 0.4}
DISPOSITIONS = ("use-as-is", "rework", "reject")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list:
    if NCRS_PATH.exists():
        try:
            return json.loads(NCRS_PATH.read_text())
        except json.JSONDecodeError:
            return []
    return []


def _save(ncrs: list) -> None:
    NCRS_PATH.write_text(json.dumps(ncrs, indent=1, default=str))


def list_all() -> list:
    with _LOCK:
        return _load()


def get(ncr_id: str) -> dict | None:
    return next((n for n in list_all() if n["ncr_id"] == ncr_id), None)


def _field_risk_for(tag: str, ncrs: list) -> float:
    """Equipment FIELD risk = worst still-open NCR against that tag."""
    open_risks = [SEVERITY_RISK[n["severity"]] for n in ncrs
                  if n.get("equipment_tag") == tag and n["status"] != "CLOSED"]
    return max(open_risks, default=0.1)


def sync_open_into_kg(kg) -> int:
    """Re-mirror every still-open NCR into a (freshly rebuilt) knowledge graph:
    the NCR node, its IMPACTS edge to the equipment, and the equipment's FIELD
    risk. Used when the graph is repopulated from cache so field signals are
    not lost. Idempotent; the caller saves. Does not touch the event bus -
    the FIELD events already published there survive a graph rebuild."""
    synced = 0
    for rec in list_all():
        if rec["status"] == "CLOSED":
            continue
        kg.add_node(rec["ncr_id"], "NCR", attributes={k: rec[k] for k in (
            "ncr_id", "title", "description", "equipment_tag", "location",
            "spec_clause", "severity", "status", "raised_by", "date_raised")})
        tag = rec.get("equipment_tag")
        if tag and kg.g.has_node(tag):
            kg.add_edge(rec["ncr_id"], tag, "IMPACTS")
            kg.update_node_risk(tag, "FIELD", SEVERITY_RISK[rec["severity"]],
                                f"{rec['ncr_id']} open: {rec['title']}")
        synced += 1
    return synced


def create(title: str, description: str, equipment_tag: str = "",
           location: str = "", spec_clause: str = "",
           severity: str = "MAJOR", raised_by: str = "Site Engineer",
           evidence_document: str = "") -> dict:
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}")
    if not title.strip() or not description.strip():
        raise ValueError("title and description are required")

    with _LOCK:
        ncrs = _load()
        numbers = [int(n["ncr_id"].split("-")[1]) for n in ncrs]
        ncr_id = f"NCR-{max(numbers, default=0) + 1:04d}"
        record = {
            "ncr_id": ncr_id,
            "title": title.strip(),
            "description": description.strip(),
            "equipment_tag": equipment_tag.strip().upper(),
            "location": location.strip(),
            "spec_clause": spec_clause.strip(),
            "severity": severity,
            "raised_by": raised_by,
            "evidence_document": evidence_document.strip(),
            "date_raised": _now()[:10],
            "status": "OPEN",
            "disposition": None,
            "disposition_note": "",
            "history": [{"ts": _now(), "by": raised_by, "action": "raised"}],
        }
        ncrs.append(record)
        _save(ncrs)

    # record of authority is written; now mirror into the KG + event bus
    kg = KnowledgeGraph()
    if config.KG_PATH.exists():
        kg.load()
    kg.add_node(ncr_id, "NCR", attributes={k: record[k] for k in (
        "ncr_id", "title", "description", "equipment_tag", "location",
        "spec_clause", "severity", "status", "raised_by", "date_raised")})
    tag = record["equipment_tag"]
    if tag and kg.g.has_node(tag):
        kg.add_edge(ncr_id, tag, "IMPACTS")
        kg.update_node_risk(tag, "FIELD", SEVERITY_RISK[severity],
                            f"{ncr_id} raised: {title.strip()}")
    kg.save()

    entity = tag if tag and kg.g.has_node(tag) else ncr_id
    event_bus.publish({
        "agent": "FIELD",
        "event_type": "ncr_raised",
        "entity_id": entity,
        "entity_type": "Equipment" if entity == tag else "NCR",
        "severity": severity,
        "description": f"{ncr_id} ({severity}) raised by {raised_by}: {title.strip()}",
        "risk_score": SEVERITY_RISK[severity],
        "ref": ncr_id,
    })
    return record


def _transition(ncr_id: str, action: str, by: str, **updates) -> dict:
    with _LOCK:
        ncrs = _load()
        record = next((n for n in ncrs if n["ncr_id"] == ncr_id), None)
        if record is None:
            raise KeyError(f"no such NCR: {ncr_id}")
        record.update(updates)
        record["history"].append({"ts": _now(), "by": by, "action": action})
        _save(ncrs)
        all_ncrs = ncrs
    # keep the KG node in step with the record
    kg = KnowledgeGraph()
    if config.KG_PATH.exists():
        kg.load()
        if kg.g.has_node(ncr_id):
            kg.add_node(ncr_id, "NCR", attributes={
                k: record[k] for k in ("status", "disposition") if k in record})
        tag = record.get("equipment_tag")
        if record["status"] == "CLOSED" and tag and kg.g.has_node(tag):
            kg.update_node_risk(tag, "FIELD", _field_risk_for(tag, all_ncrs),
                                f"{ncr_id} closed")
        kg.save()
    return record


def disposition(ncr_id: str, disposition: str, by: str, note: str = "") -> dict:
    if disposition not in DISPOSITIONS:
        raise ValueError(f"disposition must be one of {DISPOSITIONS}")
    record = get(ncr_id)
    if record is None:
        raise KeyError(f"no such NCR: {ncr_id}")
    if record["status"] != "OPEN":
        raise ValueError(f"{ncr_id} is {record['status']} - only OPEN NCRs "
                         "can be dispositioned")
    record = _transition(ncr_id, f"dispositioned: {disposition}", by,
                         status="DISPOSITIONED", disposition=disposition,
                         disposition_note=note.strip())
    event_bus.publish({
        "agent": "FIELD", "event_type": "ncr_dispositioned",
        "entity_id": record["equipment_tag"] or ncr_id,
        "entity_type": "Equipment" if record["equipment_tag"] else "NCR",
        "severity": "INFO",
        "description": f"{ncr_id} dispositioned '{disposition}' by {by}"
                       + (f": {note.strip()}" if note.strip() else ""),
        "risk_score": 0.1, "ref": ncr_id,
    })
    return record


def close(ncr_id: str, by: str) -> dict:
    record = get(ncr_id)
    if record is None:
        raise KeyError(f"no such NCR: {ncr_id}")
    if record["status"] != "DISPOSITIONED":
        raise ValueError(f"{ncr_id} is {record['status']} - an NCR must be "
                         "dispositioned before it can be closed")
    record = _transition(ncr_id, "closed", by, status="CLOSED")
    # withdraw the convergence signal: the raised event leaves the bus
    event_bus.remove_matching(agent="FIELD", event_type="ncr_raised", ref=ncr_id)
    event_bus.publish({
        "agent": "FIELD", "event_type": "ncr_closed",
        "entity_id": record["equipment_tag"] or ncr_id,
        "entity_type": "Equipment" if record["equipment_tag"] else "NCR",
        "severity": "INFO",
        "description": f"{ncr_id} closed by {by} "
                       f"(disposition: {record['disposition']})",
        "risk_score": 0.1, "ref": ncr_id,
    })
    return record


# ------------------------------------------------------------------ PDF form

def generate_pdf(ncr_id: str) -> bytes:
    """Render the formal NCR form FROM the record - the document is an output
    artifact for signature, never the source of truth."""
    record = get(ncr_id)
    if record is None:
        raise KeyError(f"no such NCR: {ncr_id}")

    BLACK, GRAY, LGRAY = (0, 0, 0), (0.45, 0.45, 0.45), (0.85, 0.85, 0.85)
    W, H, M = 612, 792, 46
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)

    def text(x, y, s, size=10, color=BLACK, bold=False):
        page.insert_text((x, y), s, fontsize=size, color=color,
                         fontname="hebo" if bold else "helv")

    y = 54
    text(M, y, "NEXUS-DC · Meridian Data Centre · Phase 1", size=9, color=GRAY)
    text(W - M - 120, y, f"Doc: {ncr_id} · Rev 0", size=9, color=GRAY)
    y += 26
    text(M, y, "NON-CONFORMANCE REPORT", size=19, bold=True)
    y += 12
    page.draw_line((M, y), (W - M, y), color=BLACK, width=1.4)
    y += 24

    rows = [
        ("NCR No.", record["ncr_id"], "Date raised", record["date_raised"]),
        ("Equipment tag", record["equipment_tag"] or "-",
         "Location", record["location"] or "-"),
        ("Spec clause", record["spec_clause"] or "-",
         "Severity", record["severity"]),
        ("Raised by", record["raised_by"], "Status", record["status"]),
    ]
    for l1, v1, l2, v2 in rows:
        text(M, y, l1.upper(), size=7.5, color=GRAY, bold=True)
        text(M, y + 13, str(v1), size=10.5)
        text(W / 2, y, l2.upper(), size=7.5, color=GRAY, bold=True)
        text(W / 2, y + 13, str(v2), size=10.5)
        y += 30
    y += 4
    page.draw_line((M, y), (W - M, y), color=LGRAY, width=0.75)
    y += 20

    text(M, y, "DESCRIPTION OF NON-CONFORMANCE", size=8.5, color=GRAY, bold=True)
    y += 8
    box = fitz.Rect(M, y, W - M, y + 120)
    page.draw_rect(box, color=LGRAY, width=0.75)
    page.insert_textbox(fitz.Rect(M + 8, y + 6, W - M - 8, y + 114),
                        record["description"], fontsize=10, fontname="helv")
    y += 136
    if record.get("evidence_document"):
        text(M, y, f"Evidence: {record['evidence_document']} (document register)",
             size=8.5, color=GRAY)
        y += 18

    text(M, y, "DISPOSITION", size=8.5, color=GRAY, bold=True)
    y += 14
    x = M
    for opt in DISPOSITIONS:
        chosen = record.get("disposition") == opt
        page.draw_rect(fitz.Rect(x, y - 9, x + 11, y + 2), color=BLACK,
                       width=1, fill=BLACK if chosen else None)
        text(x + 17, y, opt.upper(), size=9.5, bold=chosen)
        x += 130
    y += 16
    if record.get("disposition_note"):
        page.insert_textbox(fitz.Rect(M, y, W - M, y + 40),
                            f"Note: {record['disposition_note']}",
                            fontsize=9, fontname="helv", color=GRAY)
        y += 44
    else:
        y += 8
    page.draw_line((M, y), (W - M, y), color=LGRAY, width=0.75)
    y += 20

    text(M, y, "HISTORY", size=8.5, color=GRAY, bold=True)
    y += 14
    for h in record["history"]:
        text(M, y, f"{h['ts'][:16].replace('T', ' ')}  ·  {h['action']}  ·  {h['by']}",
             size=8.5)
        y += 13
    y = max(y + 20, 578)

    # the originator line is pre-filled from the record (the system knows who
    # raised it and when); QA/PE lines stay blank for wet-ink signature
    for label, name, date in (
            ("Raised by (Originator)", record["raised_by"], record["date_raised"]),
            ("Reviewed by (QA Engineer)", "", ""),
            ("Approved by (Project Engineer)", "", "")):
        if name:
            text(M, y + 22, name[:34], size=9)
        if date:
            text(M + 200, y + 22, date, size=9)
        page.draw_line((M, y + 26), (M + 150, y + 26), color=BLACK, width=0.75)
        text(M, y + 38, label.upper(), size=7, color=GRAY, bold=True)
        text(M + 200, y + 38, "DATE", size=7, color=GRAY, bold=True)
        page.draw_line((M + 200, y + 26), (M + 290, y + 26), color=BLACK, width=0.75)
        y += 52

    text(M, H - 40, "Generated by NEXUS-DC from the NCR record - the form is "
                    "an output of the system of record, not its input.",
         size=7, color=GRAY)
    data = doc.tobytes()
    doc.close()
    return data
