"""Shared fixtures: every file-backed store is redirected to a throwaway
tmp data tree, so tests never touch the real data/ directory and never
need the LLM providers or ChromaDB."""
import fitz
import pytest
from filelock import FileLock


def make_pdf(text: str) -> bytes:
    """A minimal real PDF whose text layer contains `text`."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated data tree + monkeypatched module-level paths/locks."""
    data = tmp_path / "data"
    sources = data / "sources"
    cache = data / "cache"
    assets = tmp_path / "assets"
    for d in (data, sources, cache, assets):
        d.mkdir(parents=True)

    from api import main as api_main
    from convergence import engine
    from core import config, event_bus, ncr

    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "SOURCES_DIR", sources)
    monkeypatch.setattr(config, "CACHE_DIR", cache)
    monkeypatch.setattr(config, "KG_PATH", data / "knowledge_graph.json")
    monkeypatch.setattr(config, "EVENTS_PATH", data / "events.json")
    monkeypatch.setattr(config, "SUBMITTAL_PDF", sources / "ups_submittal.pdf")
    monkeypatch.setattr(event_bus, "_LOCK", FileLock(str(data / "events.json.lock")))
    monkeypatch.setattr(ncr, "NCRS_PATH", data / "ncrs.json")
    monkeypatch.setattr(ncr, "_LOCK", FileLock(str(data / "ncrs.json.lock")))
    monkeypatch.setattr(engine, "ALERTS_PATH", data / "convergence_alerts.json")
    monkeypatch.setattr(engine, "_LOCK", FileLock(str(data / "alerts.lock")))
    monkeypatch.setattr(api_main, "_DOCLOG_PATH", data / "document_log.json")
    monkeypatch.setattr(api_main, "_DOCLOG_LOCK",
                        FileLock(str(data / "document_log.json.lock")))
    monkeypatch.setattr(api_main, "_VERSIONS_DIR", data / "submittal_versions")
    monkeypatch.setattr(api_main, "_ASSETS_DIR", assets)

    class Env:
        pass

    e = Env()
    e.data, e.sources, e.cache, e.assets = data, sources, cache, assets
    e.config, e.api_main, e.ncr, e.event_bus, e.engine = (
        config, api_main, ncr, event_bus, engine)
    return e


@pytest.fixture()
def client(env):
    """TestClient WITHOUT lifespan - no model resolution, no ORACLE pre-warm."""
    from fastapi.testclient import TestClient

    from api.main import app
    return TestClient(app)
