"""Event bus: schema validation, filtering, and cross-thread safety."""
import threading

import pytest


def _evt(agent="SPECTRA", **over):
    e = {"agent": agent, "event_type": "deviation", "entity_id": "UPS-02A",
         "entity_type": "Equipment", "severity": "MAJOR",
         "description": "d", "risk_score": 0.7}
    e.update(over)
    return e


def test_publish_stamps_ts_and_persists(env):
    out = env.event_bus.publish(_evt())
    assert out["ts"]
    assert env.event_bus.get_all_events() == [out]


def test_publish_rejects_missing_fields(env):
    with pytest.raises(ValueError, match="missing fields"):
        env.event_bus.publish({"agent": "SPECTRA"})


def test_publish_rejects_unknown_severity(env):
    with pytest.raises(ValueError, match="severity"):
        env.event_bus.publish(_evt(severity="CATASTROPHIC"))


def test_remove_agents_preserves_others(env):
    for a in ("SPECTRA", "CHRONOS", "GUIDE", "FIELD"):
        env.event_bus.publish(_evt(agent=a))
    env.event_bus.remove_agents(["SPECTRA", "CHRONOS", "TRACIS"])
    assert {e["agent"] for e in env.event_bus.get_all_events()} == {"GUIDE", "FIELD"}


def test_remove_matching_all_fields_must_match(env):
    env.event_bus.publish(_evt(agent="FIELD", event_type="ncr_raised", ref="NCR-0001"))
    env.event_bus.publish(_evt(agent="FIELD", event_type="ncr_raised", ref="NCR-0002"))
    removed = env.event_bus.remove_matching(agent="FIELD", event_type="ncr_raised",
                                            ref="NCR-0001")
    assert removed == 1
    assert [e["ref"] for e in env.event_bus.get_all_events()] == ["NCR-0002"]


def test_concurrent_publish_loses_nothing(env):
    """8 writers x 25 events through the filelock - all 200 must land."""
    errors = []

    def worker(i):
        try:
            for j in range(25):
                env.event_bus.publish(_evt(description=f"w{i}e{j}"))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    events = env.event_bus.get_all_events()
    assert len(events) == 200
    assert len({e["description"] for e in events}) == 200


def test_events_endpoint_filters_and_limits(env, client):
    for i in range(5):
        env.event_bus.publish(_evt(agent="SPECTRA" if i % 2 else "TRACIS",
                                   entity_id=f"EQ-{i % 2}"))
    r = client.get("/events", params={"agent": "SPECTRA"})
    assert r.status_code == 200
    assert all(e["agent"] == "SPECTRA" for e in r.json()["events"])
    r = client.get("/events", params={"entity_id": "EQ-1", "limit": 1})
    assert r.json()["count"] == 2 and len(r.json()["events"]) == 1
    assert client.get("/events", params={"limit": 0}).json()["events"] == []
