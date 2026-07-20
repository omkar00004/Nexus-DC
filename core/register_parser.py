"""openpyxl -> normalised RFI records.

EDMS exports carry banner/title rows above the real header and live Excel
formulas in derived columns, so the header row is located BY CONTENT and
formula cells are ignored (we keep source facts, not spreadsheet arithmetic).
"""
from datetime import date, datetime

import openpyxl

# canonical field -> substring matched against lowercased header cells
_CANON = {
    "rfi_id": "rfi no",
    "subject": "subject",
    "discipline": "discipline",
    "status": "status",
    "priority": "priority",
    "raised_by": "raised by",
    "ball_in_court": "ball-in-court",
    "date_raised": "date raised",
    "date_required": "date required",
    "date_closed": "date closed",
    "linked_activity": "linked activity",
    "spec_ref": "spec ref",
    "drawing_ref": "drawing ref",
    "question": "question",
    "response": "proposed solution",
    "cost_impact": "cost impact",
    "schedule_impact": "schedule impact",
}


def _clean(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    text = str(value).strip()
    if text.startswith("="):  # live formula with no cached value - not a source fact
        return None
    return text or None


def parse_register(path: str) -> list[dict]:
    wb = openpyxl.load_workbook(str(path), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    header_idx = next(
        (i for i, row in enumerate(rows)
         if row and any(c and "rfi no" in str(c).lower() for c in row)),
        None,
    )
    if header_idx is None:
        raise ValueError(f"no header row containing 'RFI No' found in {path}")

    header = [str(c).lower() if c else "" for c in rows[header_idx]]
    col_map = {}
    for field, needle in _CANON.items():
        idx = next((i for i, h in enumerate(header) if needle in h), None)
        if idx is not None:
            col_map[field] = idx

    records = []
    for row in rows[header_idx + 1:]:
        rfi_id = _clean(row[col_map["rfi_id"]]) if "rfi_id" in col_map else None
        if not rfi_id:
            continue
        records.append({field: _clean(row[idx]) for field, idx in col_map.items()})
    return records
