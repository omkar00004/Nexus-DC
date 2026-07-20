"""NEXUS-DC API - the single source of truth (uvicorn :8000).

All state persists under data/ (knowledge_graph.json, events.json,
convergence_alerts.json, guide_sessions.json, ncrs.json, document_log.json,
submittal_versions/), so the React frontend (a separate process) stays
consistent by talking HTTP to this API and never importing agent modules.

Run: .venv/bin/uvicorn api.main:app --port 8000
"""
import hashlib
import json
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from filelock import FileLock
from pydantic import BaseModel

from agents.chronos import ChronosAgent
from agents.guide import GuideAgent
from agents.oracle import OracleAgent
from agents.spectra import SpectraAgent
from agents.tracis import TracisAgent
from convergence.engine import ConvergenceEngine, load_alerts
from core import config, event_bus, ncr
from core.knowledge_graph import KnowledgeGraph

# module-level singletons for the heavyweight constructions (ChromaDB +
# sentence-transformers in ORACLE); their KG is re-loaded from disk per call
_oracle: OracleAgent | None = None
_guide: GuideAgent | None = None


def _get_oracle() -> OracleAgent:
    global _oracle
    if _oracle is None:
        _oracle = OracleAgent()
    else:
        _oracle.kg.load()
    return _oracle


def _get_guide() -> GuideAgent:
    global _guide
    if _guide is None:
        _guide = GuideAgent()
    else:
        _guide.kg.load()
    return _guide


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.resolve_models()
    if not (config.CACHE_DIR / "schedule.json").exists():
        print("[api] WARNING: data/cache is empty - run scripts/build_cache.py first")
    if not config.KG_PATH.exists():
        kg = KnowledgeGraph()
        kg.populate_from_cache()
        kg.save()
        print("[api] knowledge graph initialised from cache")
    # pre-warm ORACLE (ChromaDB + embedding model) so the first chat query
    # doesn't pay the ~15 s cold-start
    import threading
    threading.Thread(target=_get_oracle, daemon=True).start()
    yield


app = FastAPI(title="NEXUS-DC", version="1.0", lifespan=lifespan)


class OracleQuery(BaseModel):
    query: str


class SessionStart(BaseModel):
    procedure_id: str
    operator: str = "Commissioning Engineer"


class StepSubmit(BaseModel):
    session_id: str
    reading: bool | float | str


class SessionRef(BaseModel):
    session_id: str


@app.post("/demo/reset")
def demo_reset():
    """Wipe DERIVED state (events, alerts, sessions, risk scores) and rebuild
    the KG from the parsed cache. Source documents and cache stay untouched -
    the dashboard returns to a clean 'nothing analysed yet' state."""
    import os as _os

    event_bus.clear()
    for f in ("guide_sessions.json", "convergence_alerts.json", "ncrs.json"):
        p = config.DATA_DIR / f
        if p.exists():
            _os.remove(p)
    kg = KnowledgeGraph()
    kg.populate_from_cache()
    kg.save()
    return {"status": "reset", "note": "derived state cleared; sources and cache intact"}


@app.get("/health")
def health():
    return {"status": "ok", "models": {
        "pro": config.MODEL_REASONING_PRO,
        "flash": config.MODEL_REASONING_FLASH,
        "extraction": config.MODEL_EXTRACTION,
    }}


# ------------------------------------------------------------------- agents

@app.post("/agents/spectra/run")
def run_spectra(refresh: bool | None = None):
    return SpectraAgent().run(refresh=refresh)


@app.post("/agents/chronos/run")
def run_chronos():
    return ChronosAgent().run()


@app.post("/agents/tracis/run")
def run_tracis():
    return TracisAgent().run()


def _ensure_cache_and_graph() -> bool:
    """Self-heal the parsed cache. If data/cache/ is missing (fresh install,
    or the user deleted it to test the cold path), rebuild it from
    data/sources/ - parse every document, re-index the vector store, and
    repopulate the knowledge graph - so 'Run All Agents' works from scratch
    with no separate build step. Open NCRs are re-mirrored into the fresh
    graph so field signals survive. No-op once the cache exists.

    A file lock serialises the rebuild: two concurrent 'Run All Agents'
    must not spawn two build_cache.py processes writing the same files."""
    global _oracle
    if (config.CACHE_DIR / "schedule.json").exists():
        return False
    with FileLock(str(config.DATA_DIR / "cache_build.lock")):
        if (config.CACHE_DIR / "schedule.json").exists():
            return False               # another request rebuilt while we waited
        try:
            subprocess.run(
                [sys.executable, str(config.PROJECT_ROOT / "scripts" / "build_cache.py")],
                check=True, cwd=str(config.PROJECT_ROOT))
        except subprocess.CalledProcessError as exc:
            raise HTTPException(
                500, "cache rebuild failed - scripts/build_cache.py exited "
                     f"{exc.returncode}; check data/sources/ and server logs")
        kg = KnowledgeGraph()
        kg.populate_from_cache()
        ncr.sync_open_into_kg(kg)
        kg.save()
        # force a clean ORACLE reload against the rebuilt cache/index (GUIDE
        # needs no reset: it re-loads the KG from disk on every call and
        # holds no vector index)
        _oracle = None
    return True


@app.post("/agents/all/run")
def run_all(refresh: bool | None = None):
    """The demo button: a FULL re-analysis. If the parsed cache is missing it
    is rebuilt first (cold-start self-heal). Stale signals from the three
    analysis agents are dropped, then agents run, then the Convergence Engine.
    Per-stage timings are returned so the UI can show what the analysis cost."""
    import time as _time

    results, timings = {}, {}
    t0 = _time.time()
    cache_rebuilt = _ensure_cache_and_graph()
    if cache_rebuilt:
        timings["cache_build"] = round(_time.time() - t0, 1)

    event_bus.remove_agents(["SPECTRA", "CHRONOS", "TRACIS"])
    stages = (
        ("spectra", lambda: SpectraAgent().run(refresh=refresh)),
        ("chronos", lambda: ChronosAgent().run()),
        ("tracis", lambda: TracisAgent().run()),
        ("convergence", lambda: ConvergenceEngine().run()),
    )
    for name, fn in stages:
        s0 = _time.time()
        results[name] = fn()
        timings[name] = round(_time.time() - s0, 1)
    timings["total"] = round(_time.time() - t0, 1)   # true elapsed, not Σ(stages)
    results["cache_rebuilt"] = cache_rebuilt
    results["timings"] = timings
    return results


# ------------------------------------------------------------------- oracle

@app.post("/oracle/query")
def oracle_query(body: OracleQuery):
    return _get_oracle().answer(body.query)


# -------------------------------------------------------------- convergence

@app.get("/convergence/alerts")
def convergence_alerts():
    return load_alerts()


# ------------------------------------------------------------------- events

@app.get("/events")
def events(entity_id: str | None = None, agent: str | None = None, limit: int = 100):
    evts = event_bus.get_all_events()
    if entity_id:
        evts = [e for e in evts if e["entity_id"] == entity_id]
    if agent:
        evts = [e for e in evts if e["agent"] == agent]
    return {"count": len(evts), "events": list(reversed(evts))[:limit]}


# ---------------------------------------------------------------- dashboard

def _empty_summary() -> dict:
    """Fresh-install state: no parsed cache means nothing has been analysed
    yet, so the dashboard shows a clean slate (not stale prior results).
    'Run All Agents' rebuilds the cache and repopulates everything."""
    return {
        "project": "Meridian Data Centre - Phase 1 (Nashik, 24 MW, Rated-3/Tier III)",
        "cache_present": False,
        "metrics": {
            "equipment_at_risk": 0, "open_deviations": 0, "open_rfis": 0,
            "open_ncrs": sum(1 for n in ncr.list_all() if n["status"] != "CLOSED"),
            "convergence_alerts": 0, "risk_storm": False, "events_total": 0,
            "hours_saved": 0.0,
            "hours_saved_basis": {
                "submittals": {"n": 0, "hours_each": config.HOURS_PER_SUBMITTAL},
                "rfis": {"n": 0, "hours_each": config.HOURS_PER_RFI},
                "itps": {"n": 0, "hours_each": config.HOURS_PER_ITP},
            },
        },
        "equipment": [], "deviations": [], "milestone": None,
        "critical_path": [],
        "convergence": {"alerts": [], "risk_storm": False,
                        "converged_entities": [], "entities_considered": 0,
                        "threshold": config.CONVERGENCE_THRESHOLD, "generated_at": None},
        "recent_events": [],
    }


@app.get("/dashboard/summary")
def dashboard_summary():
    # no parsed cache => fresh install; don't surface stale KG/alerts as if live
    if not (config.CACHE_DIR / "schedule.json").exists():
        return _empty_summary()

    kg = KnowledgeGraph()
    if config.KG_PATH.exists():
        kg.load()
    else:
        kg.populate_from_cache()

    equipment = [
        {"tag": n, "vendor": d["attributes"].get("vendor"),
         "risk_score": d.get("risk_score", 0.0),
         "agent_risks": {a: r["score"] for a, r in
                         d["attributes"].get("agent_risks", {}).items()}}
        for n, d in kg.g.nodes(data=True) if d.get("node_type") == "Equipment"]

    milestone = next(
        ({"id": n, **{k: d["attributes"][k] for k in
          ("mc_p50", "mc_p80", "mc_p90", "baseline_finish",
           "expected_delay_days", "sla_breach_risk") if k in d["attributes"]},
          "name": d["attributes"].get("name")}
         for n, d in kg.g.nodes(data=True)
         if d.get("node_type") == "Milestone" and "mc_p90" in d.get("attributes", {})),
        None)

    critical = kg.get_critical_path_activities()
    schedule = [
        {"task_code": a["node_id"], "name": a["attributes"].get("name"),
         "status": a["attributes"].get("status"),
         "target_start": a["attributes"].get("target_start"),
         "target_end": a["attributes"].get("target_end"),
         "early_start": a["attributes"].get("early_start"),
         "early_end": a["attributes"].get("early_end"),
         "is_critical": a["attributes"].get("is_critical"),
         "risk_score": a.get("risk_score", 0.0)}
        for a in critical]

    rfi_nodes = [(n, d) for n, d in kg.g.nodes(data=True) if d.get("node_type") == "RFI"]
    open_rfis = [n for n, d in rfi_nodes
                 if str(d["attributes"].get("status", "")).lower() == "open"]
    deviations = [d["attributes"] for n, d in kg.g.nodes(data=True)
                  if d.get("node_type") == "Deviation"]

    sessions = {}
    sessions_path = config.DATA_DIR / "guide_sessions.json"
    if sessions_path.exists():
        try:
            sessions = json.loads(sessions_path.read_text())
        except json.JSONDecodeError:
            sessions = {}
    itps_completed = sum(1 for s in sessions.values() if s.get("itp"))

    # ONE consistent hours-saved story: live per-session numbers, same figures
    # the pitch quotes (submittals x 5.5h + RFIs x 0.75h + ITPs x 2.5h)
    submittals_processed = 1 if (config.CACHE_DIR / "submittal_ups02a.json").exists() else 0
    hours_saved = round(submittals_processed * config.HOURS_PER_SUBMITTAL
                        + len(rfi_nodes) * config.HOURS_PER_RFI
                        + itps_completed * config.HOURS_PER_ITP, 1)

    alerts = load_alerts()
    all_events = event_bus.get_all_events()
    return {
        "project": "Meridian Data Centre - Phase 1 (Nashik, 24 MW, Rated-3/Tier III)",
        "cache_present": True,
        "metrics": {
            "equipment_at_risk": sum(1 for e in equipment if e["risk_score"] > 0.5),
            "open_deviations": len(deviations),
            "open_rfis": len(open_rfis),
            "open_ncrs": sum(1 for n in ncr.list_all() if n["status"] != "CLOSED"),
            "convergence_alerts": len(alerts["alerts"]),
            "risk_storm": alerts["risk_storm"],
            "events_total": len(all_events),
            "hours_saved": hours_saved,
            "hours_saved_basis": {
                "submittals": {"n": submittals_processed, "hours_each": config.HOURS_PER_SUBMITTAL},
                "rfis": {"n": len(rfi_nodes), "hours_each": config.HOURS_PER_RFI},
                "itps": {"n": itps_completed, "hours_each": config.HOURS_PER_ITP},
            },
        },
        "equipment": sorted(equipment, key=lambda e: -e["risk_score"]),
        "deviations": deviations,
        "milestone": milestone,
        "critical_path": schedule,
        "convergence": alerts,
        "recent_events": list(reversed(all_events))[:25],
    }


# --------------------------------------------------------------------- ncrs
# The record of authority is data/ncrs.json + the NCR node in the KG; the
# formal PDF is generated FROM the record (document as output, never input).

class NcrCreate(BaseModel):
    title: str
    description: str
    equipment_tag: str = ""
    location: str = ""
    spec_clause: str = ""
    severity: str = "MAJOR"
    raised_by: str = "Site Engineer"
    evidence_document: str = ""


class NcrDraftBody(BaseModel):
    description: str


class NcrDispositionBody(BaseModel):
    disposition: str
    by: str = "QA Engineer"
    note: str = ""


class NcrCloseBody(BaseModel):
    by: str = "QA Engineer"


@app.get("/ncr")
def ncr_list():
    ncrs = ncr.list_all()
    return {"ncrs": list(reversed(ncrs)),
            "open": sum(1 for n in ncrs if n["status"] != "CLOSED")}


@app.post("/ncr")
def ncr_create(body: NcrCreate):
    try:
        record = ncr.create(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {**record,
            "note": "FIELD event published - convergence will weigh this "
                    "NCR on the next agent run"}


@app.post("/ncr/draft")
def ncr_draft(body: NcrDraftBody):
    """ORACLE-assisted prefill: extract structured NCR fields from a typed
    field report. Assistive only - the user confirms before anything is filed."""
    from core.entity_extractor import _call_structured
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string",
                      "description": "one-line non-conformance title, <= 90 chars"},
            "equipment_tag": {"type": "string",
                              "description": "equipment tag like UPS-02A, or ''"},
            "location": {"type": "string"},
            "spec_clause": {"type": "string",
                            "description": "spec clause id like ELEC-4.2.1, or ''"},
            "severity": {"type": "string", "enum": list(ncr.SEVERITIES)},
        },
        "required": ["title", "equipment_tag", "location", "spec_clause", "severity"],
    }
    try:
        draft = _call_structured(
            "You extract structured Non-Conformance Report fields from a field "
            "engineer's free-text issue report on a data-centre EPC project. "
            "CRITICAL = safety or contract-critical defect, MAJOR = does not "
            "meet spec, MINOR = cosmetic/documentation. Use '' when a field "
            "is not stated - never invent tags or clauses.",
            body.description, "ncr_draft", schema, max_tokens=600)
        return {"draft": {k: draft.get(k, "") for k in
                          ("title", "equipment_tag", "location",
                           "spec_clause", "severity")},
                "source": "llm"}
    except Exception as exc:
        return {"draft": {"title": body.description[:90], "equipment_tag": "",
                          "location": "", "spec_clause": "", "severity": "MAJOR"},
                "source": f"fallback (LLM unavailable: {exc})"}


@app.post("/ncr/{ncr_id}/disposition")
def ncr_disposition(ncr_id: str, body: NcrDispositionBody):
    try:
        return ncr.disposition(ncr_id, body.disposition, body.by, body.note)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/ncr/{ncr_id}/close")
def ncr_close(ncr_id: str, body: NcrCloseBody):
    try:
        return ncr.close(ncr_id, body.by)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/ncr/{ncr_id}/pdf")
def ncr_pdf(ncr_id: str):
    from fastapi.responses import Response
    try:
        pdf = ncr.generate_pdf(ncr_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'inline; filename="{ncr_id}.pdf"'})


# ---------------------------------------------------------------- documents
# data/sources/ is the single document store the agents actually read - this
# section only lists, serves and (narrowly) accepts files there. Parsing
# stays with build_cache.py / the agents' own mtime checks.

_DOC_REGISTER = {
    "specification.pdf": {"label": "Project Specification",
                          "cache": ["spec_requirements.json", "spec_document.json"]},
    "ups_submittal.pdf": {"label": "Vendor Submittal - UPS-02A",
                          "cache": ["submittal_ups02a.json"]},
    "schedule.xer": {"label": "P6 Schedule (XER)", "cache": ["schedule.json"]},
    "rfi_register.xlsx": {"label": "RFI Register", "cache": ["rfi_register.json"]},
    "pid_sample.pdf": {"label": "P&ID Sample", "cache": []},
    "electrical_sld.pdf": {"label": "Electrical Single-Line Diagram", "cache": []},
}

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xer": "text/plain",
}

_DOCLOG_PATH = config.DATA_DIR / "document_log.json"
_DOCLOG_LOCK = FileLock(str(_DOCLOG_PATH) + ".lock")

# retained copies of every submittal revision, so a superseded revision can be
# re-activated ("make live") or deleted. Keyed by a short content hash.
_VERSIONS_DIR = config.DATA_DIR / "submittal_versions"
_ASSETS_DIR = config.PROJECT_ROOT / "demo" / "assets"


def _doclog_read() -> list:
    if _DOCLOG_PATH.exists():
        try:
            return json.loads(_DOCLOG_PATH.read_text())
        except json.JSONDecodeError:
            return []
    return []


def _doclog_write(log: list) -> None:
    _DOCLOG_PATH.write_text(json.dumps(log, indent=1))


def _doclog_append(entry: dict) -> None:
    with _DOCLOG_LOCK:
        log = _doclog_read()
        log.append(entry)
        _doclog_write(log)


def _sha12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


# version ids are always 12 lowercase hex chars (from _sha12) - anything else
# in the URL is rejected before it touches the filesystem
_VID_RE = re.compile(r"^[0-9a-f]{12}$")


def _save_version(data: bytes) -> str:
    """Retain a submittal revision's bytes under its content hash. Returns the
    version id. Same bytes -> same id, so re-uploads never duplicate."""
    _VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    vid = _sha12(data)
    vp = _VERSIONS_DIR / f"{vid}.pdf"
    if not vp.exists():
        vp.write_bytes(data)
    return vid


def _live_submittal_vid() -> str | None:
    return (_sha12(config.SUBMITTAL_PDF.read_bytes())
            if config.SUBMITTAL_PDF.exists() else None)


# the two bundled demo revisions are always retained & selectable (not
# user-deletable - deleting one would just respawn on the next listing)
def _seed_asset_vids() -> dict:
    seeded = {}
    for asset, label in (("ups_submittal_rev2.pdf", "Revision R2 - 1.8 MVA (bundled)"),
                         ("ups_submittal_rev3.pdf", "Revision R3 - 2.1 MVA (bundled)")):
        p = _ASSETS_DIR / asset
        if p.exists():
            seeded[_save_version(p.read_bytes())] = label
    return seeded


def _submittal_versions() -> list:
    """Every retained submittal revision: live one first, the rest newest-first."""
    seeded = _seed_asset_vids()
    if config.SUBMITTAL_PDF.exists():           # keep whatever is live retained too
        _save_version(config.SUBMITTAL_PDF.read_bytes())
    live = _live_submittal_vid()
    # FIRST doclog entry per version wins: that is the original upload, so the
    # label keeps the uploaded filename even after later "activated ..." entries
    meta: dict[str, dict] = {}
    for e in _doclog_read():
        if e.get("version_id"):
            meta.setdefault(e["version_id"], e)
    out = []
    for vp in _VERSIONS_DIR.glob("*.pdf"):
        vid = vp.stem
        m = meta.get(vid, {})
        is_seed = vid in seeded
        out.append({
            "version_id": vid,
            "label": seeded.get(vid) or m.get("original_name") or f"{vid}.pdf",
            "uploaded_by": m.get("uploaded_by") or ("bundled asset" if is_seed else "unknown"),
            "ts": m.get("ts") or datetime.fromtimestamp(
                vp.stat().st_mtime, tz=timezone.utc).isoformat(),
            "size_bytes": vp.stat().st_size,
            "live": vid == live,
            "deletable": (vid != live) and not is_seed,
        })
    out.sort(key=lambda v: v["ts"], reverse=True)   # newest first...
    out.sort(key=lambda v: not v["live"])           # ...live pinned on top (stable)
    return out


def _safe_source(name: str) -> Path:
    if Path(name).name != name or name.startswith("."):
        raise HTTPException(400, "invalid document name")
    p = config.SOURCES_DIR / name
    if not p.is_file():
        raise HTTPException(404, f"no such document: {name}")
    return p


def _parse_status(f: Path) -> str:
    info = _DOC_REGISTER.get(f.name)
    if info is None:
        return "reference only - not parsed by the pipeline"
    if not info["cache"]:
        return "on file - vision demo asset, no parse required"
    mtimes = [(config.CACHE_DIR / c).stat().st_mtime
              for c in info["cache"] if (config.CACHE_DIR / c).exists()]
    if not mtimes:
        return "not parsed yet - run scripts/build_cache.py"
    if f.stat().st_mtime > min(mtimes):
        # SPECTRA re-parses the submittal itself on mtime; everything else
        # goes through build_cache.py
        return ("changed - agents re-derive on next run"
                if f.name == config.SUBMITTAL_PDF.name
                else "changed - rebuild cache to re-parse")
    return "parsed"


@app.get("/documents")
def list_documents():
    # full per-file upload history, newest first - a resubmittal SUPERSEDES
    # the live file, so the log is the only trace of what landed when
    uploads: dict[str, list] = {}
    for e in _doclog_read():
        uploads.setdefault(e["filename"], []).append(e)
    revision = None
    try:
        from core import demo_tools
        revision = demo_tools.active_revision().get("revision")
    except Exception:
        pass
    docs = []
    for f in sorted(config.SOURCES_DIR.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        entry = {
            "name": f.name,
            "label": _DOC_REGISTER.get(f.name, {}).get("label", "Reference document"),
            "pipeline": f.name in _DOC_REGISTER,
            "size_bytes": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            "status": _parse_status(f),
        }
        if f.name == config.SUBMITTAL_PDF.name:
            if revision:
                entry["revision"] = revision
            entry["submittal_versions"] = _submittal_versions()
        if f.name in uploads:
            entry["uploaded_by"] = uploads[f.name][-1].get("uploaded_by") or None
            entry["upload_history"] = [
                {"original_name": u.get("original_name"),
                 "uploaded_by": u.get("uploaded_by"), "ts": u.get("ts")}
                for u in reversed(uploads[f.name])]
        docs.append(entry)
    return {"documents": docs}


@app.get("/documents/{name}")
def get_document(name: str):
    p = _safe_source(name)
    return FileResponse(p, media_type=_MEDIA_TYPES.get(p.suffix.lower(),
                                                       "application/octet-stream"),
                        filename=p.name, content_disposition_type="inline")


@app.post("/documents/upload")
async def upload_document(file: UploadFile = File(...), doc_type: str = Form(...),
                          uploaded_by: str = Form("")):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "file exceeds 20 MB")

    if doc_type == "submittal":
        # narrow, validated path: a vendor resubmittal supersedes the live
        # submittal, exactly like a resubmittal landing in an EDMS
        if not data.startswith(b"%PDF"):
            raise HTTPException(400, "a submittal must be a PDF")
        import io

        import pdfplumber
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        except Exception:
            raise HTTPException(400, "could not read that PDF")
        if "UPS-02A" not in text:
            raise HTTPException(
                400, "PDF does not reference equipment tag UPS-02A - "
                     "is this the right submittal?")
        config.SUBMITTAL_PDF.write_bytes(data)
        version_id = _save_version(data)   # retain so it can be re-activated/deleted
        saved = config.SUBMITTAL_PDF.name
        note = "source changed - Run All Agents to re-derive compliance"
    elif doc_type == "reference":
        name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(file.filename or "upload").name)
        if Path(name).suffix.lower() not in {".pdf", ".xlsx", ".csv", ".xer", ".docx"}:
            raise HTTPException(400, "allowed types: pdf, xlsx, csv, xer, docx")
        if name in _DOC_REGISTER:
            raise HTTPException(
                400, f"'{name}' would overwrite a pipeline source - upload a "
                     "resubmittal via the Vendor Submittal doc type instead")
        (config.SOURCES_DIR / name).write_bytes(data)
        saved = name
        note = "stored as reference - listed here, not parsed by the pipeline"
    else:
        raise HTTPException(400, "doc_type must be 'submittal' or 'reference'")

    entry = {"filename": saved, "doc_type": doc_type,
             "original_name": file.filename, "uploaded_by": uploaded_by,
             "ts": datetime.now(timezone.utc).isoformat()}
    if doc_type == "submittal":
        entry["version_id"] = version_id
    _doclog_append(entry)
    return {"status": "uploaded", "filename": saved, "doc_type": doc_type, "note": note}


class ActivateBody(BaseModel):
    by: str = ""


@app.post("/documents/submittal/versions/{version_id}/activate")
def activate_submittal_version(version_id: str, body: ActivateBody):
    """Make a retained submittal revision the live one - the resubmittal you
    select supersedes the current live document. Run All Agents to re-derive."""
    if not _VID_RE.fullmatch(version_id):
        raise HTTPException(404, "no such submittal version")
    vp = _VERSIONS_DIR / f"{version_id}.pdf"
    if not vp.is_file():
        raise HTTPException(404, f"no such submittal version: {version_id}")
    if version_id == _live_submittal_vid():
        return {"status": "already live", "version_id": version_id}
    config.SUBMITTAL_PDF.write_bytes(vp.read_bytes())
    _doclog_append({"filename": config.SUBMITTAL_PDF.name, "doc_type": "submittal",
                    "original_name": f"activated {version_id}", "uploaded_by": body.by,
                    "ts": datetime.now(timezone.utc).isoformat(), "version_id": version_id})
    return {"status": "activated", "version_id": version_id,
            "note": "source changed - Run All Agents to re-derive compliance"}


@app.delete("/documents/submittal/versions/{version_id}")
def delete_submittal_version(version_id: str):
    """Remove a retained submittal revision. The live one can't be deleted
    (activate another first); bundled demo revisions respawn on next listing."""
    if not _VID_RE.fullmatch(version_id):
        raise HTTPException(404, "no such submittal version")
    vp = _VERSIONS_DIR / f"{version_id}.pdf"
    if not vp.is_file():
        raise HTTPException(404, f"no such submittal version: {version_id}")
    if version_id == _live_submittal_vid():
        raise HTTPException(400, "cannot delete the live submittal - activate "
                                 "another revision first")
    vp.unlink()
    with _DOCLOG_LOCK:
        _doclog_write([e for e in _doclog_read() if e.get("version_id") != version_id])
    return {"status": "deleted", "version_id": version_id}


# -------------------------------------------------------------------- guide

@app.get("/guide/procedures")
def guide_procedures():
    return {"procedures": _get_guide().list_procedures()}


@app.get("/guide/procedures/{procedure_id}")
def guide_procedure(procedure_id: str):
    try:
        return _get_guide().get_procedure(procedure_id)
    except KeyError:
        raise HTTPException(404, f"unknown procedure {procedure_id}")


@app.post("/guide/session/start")
def guide_start(body: SessionStart):
    try:
        return _get_guide().start_session(body.procedure_id, body.operator)
    except KeyError:
        raise HTTPException(404, f"unknown procedure {body.procedure_id}")


@app.post("/guide/session/submit-step")
def guide_submit(body: StepSubmit):
    try:
        return _get_guide().submit_step_reading(body.session_id, body.reading)
    except KeyError:
        raise HTTPException(404, f"unknown session {body.session_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/guide/session/complete")
def guide_complete(body: SessionRef):
    try:
        return _get_guide().complete_session(body.session_id)
    except KeyError:
        raise HTTPException(404, f"unknown session {body.session_id}")


@app.get("/guide/session/{session_id}")
def guide_session(session_id: str):
    try:
        return _get_guide().get_session(session_id)
    except KeyError:
        raise HTTPException(404, f"unknown session {session_id}")
