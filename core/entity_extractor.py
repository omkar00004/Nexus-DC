"""LLM structured extraction (Groq) of spec requirements and submittal parameters.

Extraction is faithful transcription of what the documents SAY - values, units
and comparison direction come from the prose/tables. Nothing here compares
submittal to spec; that derivation belongs to SPECTRA.

Call strategy: json_schema structured outputs -> json_object mode -> tolerant
JSON parsing, across primary then fallback model (free-tier 429 resilience).
"""
import json
import re
import time

from core import config
from core.llm_pool import groq_pool

# controlled vocabulary for cross-document parameter matching (schema
# normalisation only - values still come from the documents)
CANONICAL_PARAMETERS = [
    "rated_output", "transfer_time", "retransfer_time", "input_thd", "output_thd",
    "efficiency", "battery_autonomy", "input_voltage", "output_voltage",
    "frequency", "overload_capacity", "operating_temperature", "ip_rating",
    "generator_rating", "fuel_autonomy", "chiller_capacity", "chw_supply_temp",
    "redundancy", "certification",
]

_SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clause_id": {"type": "string"},
                    "system": {"type": "string"},
                    "parameter": {"type": "string"},
                    "canonical_parameter": {"type": ["string", "null"]},
                    "value": {"type": ["number", "string"]},
                    "unit": {"type": ["string", "null"]},
                    "comparison": {
                        "type": "string",
                        "enum": ["greater_equal", "less_equal", "equal", "range", "boolean"],
                    },
                    "value_max": {"type": ["number", "null"]},
                    "tolerance": {"type": ["string", "null"]},
                    "mandatory": {"type": "boolean"},
                    "criticality": {"type": "string", "enum": ["CRITICAL", "MAJOR", "MINOR"]},
                    "page": {"type": "integer"},
                    "source_text": {"type": "string"},
                },
                "required": ["clause_id", "system", "parameter", "value", "comparison",
                             "mandatory", "criticality", "page", "source_text"],
            },
        }
    },
    "required": ["requirements"],
}

_SUBMITTAL_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": {"type": "string"},
        "equipment_tag": {"type": "string"},
        "model": {"type": ["string", "null"]},
        "submittal_date": {"type": ["string", "null"]},
        "parameters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "parameter": {"type": "string"},
                    "canonical_parameter": {"type": ["string", "null"]},
                    "value": {"type": ["number", "string"]},
                    "unit": {"type": ["string", "null"]},
                    "page": {"type": "integer"},
                    "source_text": {"type": "string"},
                },
                "required": ["parameter", "value", "page", "source_text"],
            },
        },
    },
    "required": ["vendor", "equipment_tag", "parameters"],
}


def _tolerant_json(text: str):
    """Best-effort JSON recovery: strip fences, slice outermost braces."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


# Groq free tier: 8k TPM PER KEY - the pool round-robins across keys, so the
# inter-chunk pause only needs to cover a single key's per-minute window / N keys
_MAX_TOKENS_PER_CALL = 3000
_CHUNK_CHAR_BUDGET = 6000


def _inter_chunk_sleep() -> float:
    return max(20.0 / groq_pool().size, 2.0)


def _call_structured(system_prompt: str, user_prompt: str, schema_name: str,
                     schema: dict, max_tokens: int = _MAX_TOKENS_PER_CALL) -> dict:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    last_exc = None
    for model in (config.MODEL_EXTRACTION, config.MODEL_EXTRACTION_FALLBACK):
        for response_format in (
            {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema}},
            {"type": "json_object"},
        ):
            tokens = max_tokens
            for attempt in range(3):
                kwargs = dict(
                    model=model,
                    messages=messages,
                    temperature=config.TEMP_EXTRACTION,
                    max_tokens=tokens,
                    response_format=response_format,
                    # gpt-oss are reasoning models: hidden thinking consumes the
                    # completion budget. Extraction is transcription - keep it low.
                    reasoning_effort="low",
                )
                try:
                    resp = groq_pool().chat(**kwargs)
                    if resp.choices[0].finish_reason == "length":
                        # truncated JSON silently drops entries - treat as failure
                        last_exc = RuntimeError(f"{model} response truncated at {tokens} tokens")
                        tokens = min(tokens + 2500, 6500)
                        continue
                    return _tolerant_json(resp.choices[0].message.content)
                except Exception as exc:
                    last_exc = exc
                    if "429" in str(exc) or "rate" in str(exc).lower():
                        time.sleep(5 * (attempt + 1))
                        continue
                    break  # non-rate-limit error: try next format/model
    raise RuntimeError(f"extraction failed on all models/formats: {last_exc}")


def _chunk_pages(text_by_page: list[dict], char_budget: int = _CHUNK_CHAR_BUDGET) -> list[str]:
    """Group page texts into blobs that fit the free-tier per-call token budget."""
    chunks, current, size = [], [], 0
    for p in text_by_page:
        if not p["text"]:
            continue
        block = f"--- PAGE {p['page']} ({p['extraction']}) ---\n{p['text']}"
        if current and size + len(block) > char_budget:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(block)
        size += len(block)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def extract_spec_requirements(text_by_page: list[dict]) -> list[dict]:
    """Extract every clause-numbered numeric/boolean requirement from spec text."""
    system = (
        "You extract engineering requirements from construction specifications. "
        "Transcribe faithfully: the value, unit and comparison direction must come "
        "from the clause wording ('at least/minimum/≥' -> greater_equal, "
        "'no more than/maximum/≤' -> less_equal, '±/between' -> range with value=min "
        "and value_max=max, 'shall be' exact -> equal, presence/certification "
        "requirements -> boolean). Never invent clauses or values. "
        f"Use canonical_parameter from this list when one clearly applies, else null: "
        f"{CANONICAL_PARAMETERS}. "
        "criticality: CRITICAL for life-safety/power-continuity/capacity parameters, "
        "MAJOR for performance parameters, MINOR for administrative/documentation ones."
    )
    requirements = []
    chunks = _chunk_pages(text_by_page)
    for i, blob in enumerate(chunks):
        if i > 0:
            time.sleep(_inter_chunk_sleep())  # pace within a single key's TPM window
        user = (
            "Extract ALL requirements that carry a clause id (e.g. ELEC-4.2.1, MECH-2.1.3) "
            "from this specification excerpt. One entry per requirement parameter. Return "
            "JSON with a 'requirements' array.\n\n" + blob
        )
        result = _call_structured(system, user, "spec_requirements", _SPEC_SCHEMA)
        requirements.extend(result.get("requirements", []))
    requirements = [_normalize_requirement(r) for r in requirements]
    # dedupe (clause_id, parameter), keep first occurrence
    seen, unique = set(), []
    for r in requirements:
        key = (r.get("clause_id"), str(r.get("parameter", "")).lower())
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


_SYSTEM_FROM_PREFIX = {
    "ELEC": "Electrical", "MECH": "Mechanical", "FIRE": "Fire Protection",
    "IT": "IT/Telecom", "TEL": "IT/Telecom", "CIV": "Civil", "STR": "Structural",
}


def _normalize_requirement(r: dict) -> dict:
    """Deterministic completion of optional fields a model run may omit."""
    if not r.get("parameter"):
        r["parameter"] = r.get("canonical_parameter") or "unspecified"
    if r.get("mandatory") is None:
        r["mandatory"] = r.get("criticality") in ("CRITICAL", "MAJOR")
    if not r.get("system"):
        prefix = str(r.get("clause_id", "")).split("-")[0]
        r["system"] = _SYSTEM_FROM_PREFIX.get(prefix, prefix or "General")
    return r


def extract_submittal_parameters(parsed_pdf: dict) -> dict:
    """Extract vendor datasheet parameters - values as SUBMITTED, no judgements.

    One call PER PAGE (page text + that page's tables) so responses stay far
    below the free-tier token cap - a truncated response would silently drop
    parameters. Results are merged and deduped deterministically.
    """
    system = (
        "You extract technical parameters from vendor equipment datasheets. "
        "Be exhaustive: every row of every specification table is a parameter. "
        "Transcribe the submitted values exactly as stated, with units. Do NOT "
        "assess compliance, do NOT compare to any specification, do NOT add "
        "fields like 'deviation' or 'compliant'. "
        f"Use canonical_parameter from this list when one clearly applies, else null: "
        f"{CANONICAL_PARAMETERS}."
    )
    merged = {"vendor": None, "equipment_tag": None, "model": None,
              "submittal_date": None, "parameters": []}
    seen = set()
    first_call = True
    for p in parsed_pdf["text_by_page"]:
        page_tables = [t for t in parsed_pdf["tables"] if t["page"] == p["page"]]
        if not p["text"] and not page_tables:
            continue
        if not first_call:
            time.sleep(_inter_chunk_sleep())
        first_call = False
        tables_blob = "\n\n".join(
            f"--- TABLE ON PAGE {t['page']} ---\n" + "\n".join(" | ".join(row) for row in t["rows"])
            for t in page_tables
        )
        user = (
            f"Extract the vendor name, equipment tag, model designation and ALL technical "
            f"parameters from PAGE {p['page']} of this datasheet. Set page={p['page']} on every "
            f"parameter. Return JSON.\n\n--- PAGE {p['page']} TEXT ---\n{p['text']}\n\n{tables_blob}"
        )
        try:
            result = _call_structured(system, user, "submittal_parameters", _SUBMITTAL_SCHEMA)
        except RuntimeError:
            continue  # one bad page must not sink the document
        for field in ("vendor", "equipment_tag", "model", "submittal_date"):
            if not merged[field] and result.get(field):
                merged[field] = result[field]
        for param in result.get("parameters", []):
            key = (str(param.get("parameter", "")).lower().strip(),
                   str(param.get("value", "")).strip(), param.get("unit"))
            if key not in seen:
                seen.add(key)
                merged["parameters"].append(param)
    return merged
