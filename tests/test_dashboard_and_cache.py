"""Fresh-install dashboard state and the cold-start cache self-heal."""
import json

from core.knowledge_graph import KnowledgeGraph


def _minimal_kg(env):
    kg = KnowledgeGraph()
    kg.add_node("UPS-02A", "Equipment", attributes={"vendor": "PowerVolt"})
    kg.update_node_risk("UPS-02A", "SPECTRA", 0.8, "derived deviation")
    kg.add_node("CRAH-11", "Equipment", attributes={"vendor": "CoolFlow"})
    kg.update_node_risk("CRAH-11", "TRACIS", 0.5, "boundary: not at risk")
    kg.add_node("MS-100", "Milestone", attributes={
        "name": "Rated-3 Certification", "mc_p90": "2026-12-22",
        "mc_p50": "2026-12-01", "mc_p80": "2026-12-14",
        "baseline_finish": "2026-11-30", "expected_delay_days": 22,
        "sla_breach_risk": 0.7})
    kg.add_node("RFI-0007", "RFI", attributes={"status": "Open"})
    kg.add_node("RFI-0008", "RFI", attributes={"status": "Closed"})
    kg.add_node("DEV-1", "Deviation", attributes={"parameter": "rated_output"})
    kg.save()
    return kg


def test_summary_fresh_install_is_empty(env, client):
    r = client.get("/dashboard/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["cache_present"] is False
    m = body["metrics"]
    assert (m["equipment_at_risk"], m["open_deviations"], m["open_rfis"]) == (0, 0, 0)
    assert m["hours_saved"] == 0.0
    assert body["equipment"] == [] and body["deviations"] == []
    assert body["milestone"] is None
    assert body["convergence"]["alerts"] == []


def test_summary_fresh_install_still_counts_open_ncrs(env, client):
    env.ncr.create("Cable tray unsupported", "Span exceeds 1.5 m",
                   equipment_tag="", severity="MINOR")
    r = client.get("/dashboard/summary")
    assert r.json()["metrics"]["open_ncrs"] == 1


def test_summary_with_cache_reports_graph_state(env, client):
    (env.cache / "schedule.json").write_text("{}")
    _minimal_kg(env)
    r = client.get("/dashboard/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["cache_present"] is True        # uniform contract with empty shape
    m = body["metrics"]
    assert m["equipment_at_risk"] == 1          # 0.8 counts, 0.5 is NOT > 0.5
    assert m["open_deviations"] == 1
    assert m["open_rfis"] == 1                  # status compared case-insensitively
    # hours saved: no submittal cache, 2 RFI nodes, 0 ITPs
    assert m["hours_saved"] == round(2 * env.config.HOURS_PER_RFI, 1)
    assert [e["tag"] for e in body["equipment"]] == ["UPS-02A", "CRAH-11"]  # sorted desc
    assert body["milestone"]["mc_p90"] == "2026-12-22"


def test_summary_survives_corrupt_guide_sessions(env, client):
    (env.cache / "schedule.json").write_text("{}")
    _minimal_kg(env)
    (env.data / "guide_sessions.json").write_text("{not json")
    r = client.get("/dashboard/summary")
    assert r.status_code == 200
    assert r.json()["metrics"]["hours_saved_basis"]["itps"]["n"] == 0


# ------------------------------------------------------- cold-start self-heal

def test_ensure_cache_noop_when_cache_present(env, monkeypatch):
    (env.cache / "schedule.json").write_text("{}")

    def boom(*a, **k):
        raise AssertionError("subprocess must not run when cache exists")
    monkeypatch.setattr(env.api_main.subprocess, "run", boom)
    assert env.api_main._ensure_cache_and_graph() is False


def test_ensure_cache_cold_path_rebuilds_and_resets_oracle(env, monkeypatch):
    calls = {}

    def fake_run(cmd, check, cwd):
        calls["cmd"], calls["check"], calls["cwd"] = cmd, check, cwd
        (env.cache / "schedule.json").write_text("{}")   # simulate the build
        return None
    monkeypatch.setattr(env.api_main.subprocess, "run", fake_run)

    class StubKG:
        instances = []

        def __init__(self):
            self.calls = []
            StubKG.instances.append(self)

        def populate_from_cache(self):
            self.calls.append("populate")

        def save(self):
            self.calls.append("save")
    monkeypatch.setattr(env.api_main, "KnowledgeGraph", StubKG)
    synced = {}
    monkeypatch.setattr(env.api_main.ncr, "sync_open_into_kg",
                        lambda kg: synced.setdefault("kg", kg))
    monkeypatch.setattr(env.api_main, "_oracle", object())

    assert env.api_main._ensure_cache_and_graph() is True
    assert calls["check"] is True
    assert "build_cache.py" in str(calls["cmd"][-1])
    assert StubKG.instances[0].calls == ["populate", "save"]
    assert synced["kg"] is StubKG.instances[0]           # NCRs re-mirrored
    assert env.api_main._oracle is None                  # forced clean reload


def test_ensure_cache_concurrent_cold_start_rebuilds_once(env, monkeypatch):
    """Two racing cold-start requests must trigger exactly ONE rebuild."""
    import threading

    calls = []
    gate = threading.Barrier(2)

    def fake_run(cmd, check, cwd):
        calls.append(cmd)
        (env.cache / "schedule.json").write_text("{}")
    monkeypatch.setattr(env.api_main.subprocess, "run", fake_run)

    class StubKG:
        def populate_from_cache(self):
            pass

        def save(self):
            pass
    monkeypatch.setattr(env.api_main, "KnowledgeGraph", StubKG)

    results = []

    def racer():
        gate.wait()
        results.append(env.api_main._ensure_cache_and_graph())

    threads = [threading.Thread(target=racer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1                       # one subprocess, not two
    assert sorted(results) == [False, True]      # loser sees the winner's cache


def test_ensure_cache_build_failure_returns_clear_500(env, client, monkeypatch):
    import subprocess as sp

    def boom(cmd, check, cwd):
        raise sp.CalledProcessError(2, cmd)
    monkeypatch.setattr(env.api_main.subprocess, "run", boom)
    r = client.post("/agents/all/run")
    assert r.status_code == 500
    assert "cache rebuild failed" in r.json()["detail"]


def test_run_all_reports_cache_flag_and_prunes_stale_events(env, client, monkeypatch):
    (env.cache / "schedule.json").write_text("{}")       # warm path: no rebuild
    for agent in ("SPECTRA", "CHRONOS", "TRACIS", "GUIDE"):
        env.event_bus.publish({
            "agent": agent, "event_type": "stale", "entity_id": "UPS-02A",
            "entity_type": "Equipment", "severity": "INFO",
            "description": "old signal", "risk_score": 0.2})

    for name in ("SpectraAgent", "ChronosAgent", "TracisAgent", "ConvergenceEngine"):
        class Stub:  # noqa: B903
            def run(self, refresh=None):
                return {"stub": True}
        monkeypatch.setattr(env.api_main, name, Stub)

    r = client.post("/agents/all/run")
    assert r.status_code == 200
    body = r.json()
    assert body["cache_rebuilt"] is False
    assert "cache_build" not in body["timings"]
    assert set(body["timings"]) == {"spectra", "chronos", "tracis",
                                    "convergence", "total"}
    agents_left = {e["agent"] for e in env.event_bus.get_all_events()}
    assert agents_left == {"GUIDE"}                      # GUIDE history survives
