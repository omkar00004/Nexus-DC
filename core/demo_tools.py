"""Vendor-revision tooling for the falsifiability encore.

Professional framing: nothing is 'edited live' in front of judges - the VENDOR
ISSUES A NEW REVISION. demo/assets/ holds two authentic submittal revisions:

    ups_submittal_rev2.pdf   R2, as originally submitted (1.8 MVA - deviates)
    ups_submittal_rev3.pdf   R3, uprated module (2.1 MVA - compliant on rated
                             output; transfer time and iTHD still deviate)

scripts/what_if.py swaps which revision sits in data/sources/, exactly as a
resubmittal supersedes its predecessor in a document-management system. The
agents detect the source change (mtime vs cache) and re-derive on the next run.

The R3 asset itself is AUTHORED ONCE, OFFLINE, by redaction-editing R2
(generation tooling, not a runtime demo mechanism).
"""
import hashlib
import shutil
from pathlib import Path

import fitz  # PyMuPDF

from core import config

ASSETS_DIR = config.PROJECT_ROOT / "demo" / "assets"
REV2 = ASSETS_DIR / "ups_submittal_rev2.pdf"
REV3 = ASSETS_DIR / "ups_submittal_rev3.pdf"


def mutate_pdf(path, row_replacements=(), exact_replacements=()) -> int:
    """Redaction-edit a PDF in place.

    row_replacements:   [(row_anchor_text, old, new)] - old must sit on the
                        same row as (and right of) the anchor text
    exact_replacements: [(old, new)] - replace every occurrence of the string
    """
    doc = fitz.open(str(path))
    n_applied = 0
    for page in doc:
        todo = []
        for old, new in exact_replacements:
            for orect in page.search_for(old):
                todo.append((orect, new))
        for anchor, old, new in row_replacements:
            for arect in page.search_for(anchor):
                for orect in page.search_for(old):
                    if abs(orect.y0 - arect.y0) < 3 and orect.x0 > arect.x1 - 1:
                        todo.append((orect, new))
                        break
        for orect, _ in todo:
            page.add_redact_annot(orect)
        if todo:
            page.apply_redactions()
        for orect, new in todo:
            page.insert_text((orect.x0, orect.y1 - 1.5), new,
                             fontsize=max(orect.height * 0.75, 7), fontname="helv")
            n_applied += 1
    data = doc.tobytes()
    doc.close()
    Path(path).write_bytes(data)
    return n_applied


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generate_assets(force: bool = False) -> dict:
    """Author the two revision assets from the current source PDF (which must
    be the original R2). Run once; idempotent unless force=True."""
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    if REV2.exists() and REV3.exists() and not force:
        return {"status": "already generated"}

    import pdfplumber
    with pdfplumber.open(str(config.SUBMITTAL_PDF)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    if "1.8" not in text or "R2" not in text:
        raise RuntimeError("source PDF is not the original R2 submittal - "
                           "restore it first (scripts/what_if.py rev2)")

    shutil.copyfile(config.SUBMITTAL_PDF, REV2)
    shutil.copyfile(config.SUBMITTAL_PDF, REV3)
    n = mutate_pdf(
        REV3,
        row_replacements=[
            ("Rated apparent power", "1.8", "2.1"),   # the uprated module
            ("Rated active power", "1.62", "1.89"),   # kW follows (pf 0.9)
            ("Revision", "R2", "R3"),
            ("Date", "2026-06-18", "2026-07-20"),
        ],
        exact_replacements=[
            ("PMX-SUB-UPS02A-R2", "PMX-SUB-UPS02A-R3"),
        ],
    )
    return {"status": "generated", "replacements_in_rev3": n,
            "rev2": str(REV2), "rev3": str(REV3)}


def active_revision() -> dict:
    """Which revision currently sits in data/sources/."""
    if not (REV2.exists() and REV3.exists()):
        return {"revision": "R2 (assets not generated yet)"}
    src = _sha(config.SUBMITTAL_PDF)
    rev = "R2" if src == _sha(REV2) else "R3" if src == _sha(REV3) else "unknown"
    return {
        "revision": rev,
        "rated_output": {"R2": "1.8 MVA (deviates from >= 2.0)",
                         "R3": "2.1 MVA (compliant)"}.get(rev, "?"),
    }


def set_revision(rev: str) -> dict:
    """Drop the requested revision into data/sources/ - a resubmittal landing."""
    generate_assets()
    asset = {"R2": REV2, "R3": REV3}[rev.upper()]
    shutil.copyfile(asset, config.SUBMITTAL_PDF)
    return {**active_revision(),
            "note": "source changed - Run All Agents to re-derive"}
