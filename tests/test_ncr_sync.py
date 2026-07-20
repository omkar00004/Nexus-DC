"""ncr.sync_open_into_kg: re-mirroring open field records into a rebuilt KG."""
import json

import pytest

from core.knowledge_graph import KnowledgeGraph


def _write_ncrs(env, records):
    (env.data / "ncrs.json").write_text(json.dumps(records))


def _rec(ncr_id, status="OPEN", severity="MAJOR", tag="UPS-02A"):
    return {
        "ncr_id": ncr_id, "title": f"Issue {ncr_id}", "description": "d",
        "equipment_tag": tag, "location": "Hall A", "spec_clause": "ELEC-4.2.1",
        "severity": severity, "status": status, "raised_by": "Site Engineer",
        "date_raised": "2026-07-01", "disposition": None,
        "disposition_note": "", "history": [],
    }


def test_sync_mirrors_open_and_skips_closed(env):
    _write_ncrs(env, [_rec("NCR-0001"), _rec("NCR-0002", status="CLOSED")])
    kg = KnowledgeGraph()
    kg.add_node("UPS-02A", "Equipment")
    assert env.ncr.sync_open_into_kg(kg) == 1
    assert kg.g.has_node("NCR-0001") and not kg.g.has_node("NCR-0002")
    assert kg.g.edges["NCR-0001", "UPS-02A"]["relationship"] == "IMPACTS"
    node = kg.get_node("UPS-02A")
    assert node["attributes"]["agent_risks"]["FIELD"]["score"] == 0.7  # MAJOR
    assert node["risk_score"] == 0.7


def test_sync_severity_maps_to_field_risk(env):
    _write_ncrs(env, [_rec("NCR-0001", severity="CRITICAL")])
    kg = KnowledgeGraph()
    kg.add_node("UPS-02A", "Equipment")
    env.ncr.sync_open_into_kg(kg)
    assert kg.get_node("UPS-02A")["attributes"]["agent_risks"]["FIELD"]["score"] == 0.9


def test_sync_without_matching_equipment_adds_node_only(env):
    _write_ncrs(env, [_rec("NCR-0001", tag="PDU-99Z")])
    kg = KnowledgeGraph()                      # empty graph - tag unknown
    assert env.ncr.sync_open_into_kg(kg) == 1
    assert kg.g.has_node("NCR-0001")
    assert kg.g.number_of_edges() == 0         # no dangling IMPACTS edge


def test_sync_blank_tag_no_edge(env):
    _write_ncrs(env, [_rec("NCR-0001", tag="")])
    kg = KnowledgeGraph()
    assert env.ncr.sync_open_into_kg(kg) == 1
    assert kg.g.number_of_edges() == 0


def test_sync_empty_store_returns_zero(env):
    kg = KnowledgeGraph()
    assert env.ncr.sync_open_into_kg(kg) == 0
    assert kg.g.number_of_nodes() == 0


def test_sync_corrupt_store_returns_zero(env):
    (env.data / "ncrs.json").write_text("{corrupt json")
    kg = KnowledgeGraph()
    assert env.ncr.sync_open_into_kg(kg) == 0


def test_sync_is_idempotent(env):
    _write_ncrs(env, [_rec("NCR-0001")])
    kg = KnowledgeGraph()
    kg.add_node("UPS-02A", "Equipment")
    env.ncr.sync_open_into_kg(kg)
    nodes, edges = kg.g.number_of_nodes(), kg.g.number_of_edges()
    env.ncr.sync_open_into_kg(kg)
    assert (kg.g.number_of_nodes(), kg.g.number_of_edges()) == (nodes, edges)


def test_sync_dispositioned_still_counts_as_open(env):
    """Lifecycle is OPEN -> DISPOSITIONED -> CLOSED; only CLOSED withdraws."""
    _write_ncrs(env, [_rec("NCR-0001", status="DISPOSITIONED")])
    kg = KnowledgeGraph()
    assert env.ncr.sync_open_into_kg(kg) == 1


# --------------------------------------------------------- lifecycle via API

def test_ncr_lifecycle_roundtrip(env, client):
    r = client.post("/ncr", json={"title": "THD above spec",
                                  "description": "Measured 4.5% vs <= 3%",
                                  "equipment_tag": "ups-02a",
                                  "severity": "MAJOR"})
    assert r.status_code == 200
    ncr_id = r.json()["ncr_id"]
    assert r.json()["equipment_tag"] == "UPS-02A"        # normalised upper

    assert client.post(f"/ncr/{ncr_id}/close",
                       json={"by": "QA"}).status_code == 400   # must disposition first
    assert client.post(f"/ncr/{ncr_id}/disposition",
                       json={"disposition": "bad-value"}).status_code == 400
    assert client.post("/ncr/NCR-9999/disposition",
                       json={"disposition": "rework"}).status_code == 404

    r = client.post(f"/ncr/{ncr_id}/disposition",
                    json={"disposition": "rework", "by": "QA", "note": "fix filter"})
    assert r.status_code == 200 and r.json()["status"] == "DISPOSITIONED"
    r = client.post(f"/ncr/{ncr_id}/close", json={"by": "QA"})
    assert r.status_code == 200 and r.json()["status"] == "CLOSED"

    # the raised FIELD signal is withdrawn from the bus on close
    raised = [e for e in env.event_bus.get_all_events()
              if e["event_type"] == "ncr_raised"]
    assert raised == []


def test_ncr_create_validation(client):
    assert client.post("/ncr", json={"title": " ", "description": "x"}).status_code == 400
    assert client.post("/ncr", json={"title": "x", "description": "y",
                                     "severity": "APOCALYPTIC"}).status_code == 400


def test_ncr_pdf_generated_from_record(env, client):
    r = client.post("/ncr", json={"title": "Paint damage", "description": "Scratch"})
    ncr_id = r.json()["ncr_id"]
    r = client.get(f"/ncr/{ncr_id}/pdf")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")
    assert client.get("/ncr/NCR-9999/pdf").status_code == 404
