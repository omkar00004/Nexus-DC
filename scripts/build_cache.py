"""Parse everything in data/sources/ -> data/cache/*.json.

The cache holds EXTRACTED FACTS ONLY (parameters, activities, RFIs) - never
derived conclusions. A guard below fails the build if judgement-like keys
sneak into the submittal cache.

Usage: .venv/bin/python scripts/build_cache.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config
from core.document_parser import parse_pdf
from core.entity_extractor import extract_spec_requirements, extract_submittal_parameters
from core.register_parser import parse_register
from core.schedule_parser import parse_xer
from core.vector_store import VectorStore

FORBIDDEN_KEYS = {"deviation", "deviations", "compliant", "compliance",
                  "violation", "risk_score", "severity", "delay_days"}


def _write(name: str, payload) -> None:
    path = config.CACHE_DIR / name
    path.write_text(json.dumps(payload, indent=1, default=str))
    print(f"  wrote {path.relative_to(config.PROJECT_ROOT)}")


def _backfill_source_text(requirements: list, spec_doc: dict) -> int:
    """Fill missing source_text/page with the actual clause line from the parsed
    spec - deterministic lookup, keeps citations faithful to the document."""
    filled = 0
    for r in requirements:
        if r.get("source_text"):
            continue
        clause = r.get("clause_id", "")
        for p in spec_doc["text_by_page"]:
            if clause and clause in p["text"]:
                lines = p["text"].splitlines()
                idx = next(i for i, ln in enumerate(lines) if clause in ln)
                r["source_text"] = " ".join(lines[idx:idx + 3]).strip()[:400]
                r.setdefault("page", p["page"])
                filled += 1
                break
    return filled


def _assert_no_answer_keys(payload, source: str) -> None:
    def walk(obj):
        if isinstance(obj, dict):
            bad = FORBIDDEN_KEYS & {str(k).lower() for k in obj}
            if bad:
                raise AssertionError(f"answer-key field(s) {bad} found in {source} cache")
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
    walk(payload)


def main() -> None:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    print("[1/5] specification.pdf -> spec_document.json + spec_requirements.json")
    spec_doc = parse_pdf(config.SPEC_PDF)
    print(f"  pages={spec_doc['total_pages']}, "
          f"ocr_pages={[p['page'] for p in spec_doc['text_by_page'] if p['extraction'] == 'ocr']}, "
          f"needs_manual_review={spec_doc['low_confidence_pages']}")
    _write("spec_document.json", spec_doc)
    requirements = extract_spec_requirements(spec_doc["text_by_page"])
    filled = _backfill_source_text(requirements, spec_doc)
    print(f"  extracted {len(requirements)} clause requirements "
          f"({filled} source_text backfilled from document)")
    _write("spec_requirements.json", requirements)

    print("[2/5] ups_submittal.pdf -> submittal_document.json + submittal_ups02a.json")
    sub_doc = parse_pdf(config.SUBMITTAL_PDF)
    _write("submittal_document.json", sub_doc)
    submittal = extract_submittal_parameters(sub_doc)
    _assert_no_answer_keys(submittal, "submittal")
    print(f"  vendor={submittal.get('vendor')!r}, tag={submittal.get('equipment_tag')!r}, "
          f"parameters={len(submittal.get('parameters', []))}")
    _write("submittal_ups02a.json", submittal)

    print("[3/5] schedule.xer -> schedule.json")
    schedule = parse_xer(config.SCHEDULE_XER)
    print(f"  project={schedule['project_name']!r}, activities={len(schedule['activities'])}, "
          f"relationships={len(schedule['relationships'])}, data_date={schedule['data_date']}")
    _write("schedule.json", schedule)

    print("[4/5] rfi_register.xlsx -> rfi_register.json")
    rfis = parse_register(config.RFI_REGISTER_XLSX)
    n_open = sum(1 for r in rfis if (r.get("status") or "").lower() == "open")
    print(f"  rfis={len(rfis)} ({n_open} open)")
    _write("rfi_register.json", rfis)

    print("[5/5] indexing vector store (ChromaDB)")
    counts = VectorStore().index_from_cache()
    print(f"  indexed: {counts}")

    print("\nbuild_cache complete.")


if __name__ == "__main__":
    main()
