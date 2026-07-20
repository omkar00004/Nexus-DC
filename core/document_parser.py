"""PDF parsing: pdfplumber for text/tables, PyMuPDF+pytesseract per-page OCR fallback.

Rules:
- A page whose extracted text is near-empty is rasterised (300 dpi) and OCR'd.
  If tesseract is unavailable the page is marked needs_manual_review and the
  pipeline continues - never crashes.
- Tables are located by CONTENT PATTERN (find_tables_by_content), never by a
  fixed page index.
"""
import re

import fitz  # PyMuPDF
import pdfplumber

MIN_TEXT_CHARS = 40
OCR_DPI = 300


def _ocr_page(path: str, page_index: int) -> tuple[str | None, str]:
    """Rasterise one page and OCR it. Returns (text, status)."""
    try:
        import pytesseract
        from PIL import Image

        with fitz.open(path) as doc:
            pix = doc[page_index].get_pixmap(dpi=OCR_DPI)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        text = pytesseract.image_to_string(img)
        if text and text.strip():
            return text, "ocr"
        return None, "needs_manual_review"
    except Exception:
        return None, "needs_manual_review"


def parse_pdf(path: str) -> dict:
    """Parse a PDF into text-by-page + tables, with per-page OCR fallback.

    Returns:
        {filename, total_pages,
         text_by_page: [{page, text, extraction}],   # extraction: text|ocr|needs_manual_review
         tables: [{page, rows}],
         low_confidence_pages: [page numbers]}
    """
    path = str(path)
    text_by_page, tables, low_confidence = [], [], []

    def norm(s: str) -> str:
        # project convention: plain hyphens everywhere downstream
        return s.replace("—", "-").replace("–", "-")

    with pdfplumber.open(path) as pdf:
        total_pages = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            pageno = i + 1
            text = norm((page.extract_text() or "").strip())
            extraction = "text"
            if len(text) < MIN_TEXT_CHARS:
                ocr_text, status = _ocr_page(path, i)
                extraction = status
                if status == "ocr":
                    text = ocr_text.strip()
                else:
                    low_confidence.append(pageno)
            text_by_page.append({"page": pageno, "text": text, "extraction": extraction})
            for raw in page.extract_tables():
                rows = [[norm((c or "").strip()) for c in row] for row in raw]
                if any(any(c for c in row) for row in rows):
                    tables.append({"page": pageno, "rows": rows})
    return {
        "filename": path.rsplit("/", 1)[-1],
        "total_pages": total_pages,
        "text_by_page": text_by_page,
        "tables": tables,
        "low_confidence_pages": low_confidence,
    }


def full_text(parsed: dict) -> str:
    return "\n".join(p["text"] for p in parsed["text_by_page"])


def find_tables_by_content(parsed: dict, patterns: list[str], min_rows: int = 2) -> list[dict]:
    """Return tables whose flattened content matches ALL regex patterns.

    This is how the UPS datasheet table is found regardless of which page the
    vendor put it on.
    """
    matches = []
    for table in parsed["tables"]:
        if len(table["rows"]) < min_rows:
            continue
        flat = " | ".join(" | ".join(row) for row in table["rows"])
        if all(re.search(p, flat, re.IGNORECASE) for p in patterns):
            matches.append(table)
    return matches
