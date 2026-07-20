# NEXUS-DC - AI Project Intelligence for Data-Centre EPC Delivery

Multi-agent system that unifies specifications, vendor submittals, Primavera P6
schedules, RFIs and TIA-942 commissioning records into one reasoning layer - with a
**Convergence Engine** that fires when the *same equipment item* is independently
flagged by two or more agents, reconciling their signals into a single causal
narrative with quantified SLA exposure and mitigations.

Hackathon prototype for the "Data Centre EPC Project Delivery" problem statement.

## Honesty note (read first)

Every file in `data/sources/` is **synthetic content in an authentic industry
format** - a real P6 XER parsed by `xerparser`, real PDFs parsed by
pdfplumber/PyMuPDF with a genuine OCR fallback (page 1 of the spec is image-only on
purpose), a real EDMS-style XLSX register. **No answer keys:** the sources contain no
deviation flags, delay figures or risk scores - every conclusion is computed by the
agents at run time, and changing a source value changes the conclusions (see
`scripts/verify.py`). The P&ID vision path is scoped as a limited demo: general-purpose
vision models are not reliable at reading dense engineering drawings — symbol and
line recognition is the domain of specialised P&ID digitisation pipelines — so we
demo the capability honestly rather than claiming production accuracy.

## Architecture

![NEXUS-DC Architecture](diagrams/Nexus_DC%20Architecture.png)

Human field reports join the same machinery: filing an NCR publishes a `FIELD`
event to the bus, so a site engineer's observation converges with the agents'
computed signals on the same equipment.

## Quick start - one command

```bash
bash demo/run_demo.sh
```

That single command bootstraps everything on a fresh clone: Python venv +
dependencies, `.env` scaffold, the parsed document cache, frontend dependencies -
then starts the API (:8000) and the UI (:5173). The only manual step: the first
run creates `.env` and asks you to paste in two **free** API keys
([Gemini / AI Studio](https://aistudio.google.com/apikey) and
[Groq](https://console.groq.com/keys)), then re-run the same command.

Prerequisites: Python 3.11+, Node 18+. Optional: `tesseract` for the OCR
fallback (`brew install tesseract` / `apt-get install tesseract-ocr` - degrades
gracefully if absent).

## The 3-minute judge demo

1. **Run All Agents** (Risk Dashboard). SPECTRA parses the vendor submittal PDF
   live and derives **3 deviations** (1.8 MVA vs ≥2.0, 6 ms vs ≤4, 4.5% THD vs
   ≤3%); CHRONOS runs a 5,000-trial Monte Carlo on the real P6 schedule; TRACIS
   computes delivery buffers - and the **Convergence Engine** fires one unified
   alert on UPS-02A: multiple independent agents, one piece of equipment, days
   late and dollars at stake.
2. **Fix the deviation the way a real project would.** Open **Documents**,
   upload `demo/assets/ups_submittal_rev3.pdf` as a *Vendor submittal
   (resubmittal)* - the uprated 2.1 MVA revision supersedes the live document,
   exactly like a resubmittal landing in an EDMS. Run All Agents again: the
   ELEC-4.2.1 deviation **disappears** (the other two remain - the alert
   honestly stays). Nothing was scripted; the conclusion changed because the
   source changed.
3. **Raise an NCR** (NCR page): type a field issue in plain language, let the
   LLM prefill the structured form, file it - it becomes a knowledge-graph
   record with a lifecycle (open → disposition → closed), a generated
   signature-ready PDF, and a **fourth independent signal** into the
   Convergence Engine.
4. **Ask ORACLE**: "Which open RFIs sit on the critical path?" - answered by
   multi-hop graph traversal with citations, not keyword search.


Verification (including the change-the-source falsifiability checks):
`.venv/bin/python scripts/verify.py`.
Unit/API tests (no LLM keys needed, isolated from `data/`):
`.venv/bin/pytest tests/`.

## LLM configuration (provider-per-role, config-driven)

| Role (env var) | Default | Used for |
|---|---|---|
| `MODEL_REASONING_PRO` | `gemini-3.5-flash`* | SPECTRA qualitative review, CHRONOS narrative, Convergence synthesis |
| `MODEL_REASONING_FLASH` | `gemini-3.5-flash` | ORACLE synthesis, GUIDE summaries |
| `MODEL_VISION` | `gemini-flash-latest` | P&ID demo (rolling alias - never hardcode the fast-renaming Flash tier) |
| `MODEL_EXTRACTION` | `openai/gpt-oss-120b` (Groq) | Structured extraction of spec/submittal parameters |
| `MODEL_EXTRACTION_FALLBACK` | `openai/gpt-oss-20b` (Groq) | Rate-limit fallback |

**Groq key pool:** set `GROQ_API_KEYS` to a comma-separated list - extraction calls
round-robin across keys (N keys ≈ N × 8k free-tier TPM), and a rate-limited key is
put on cooldown and skipped automatically (`core/llm_pool.py`).

**Optional NVIDIA NIM fallback:** set `NIM_API_KEY` (build.nvidia.com; OpenAI-compatible)
and NIM becomes the last-resort extraction provider when *every* Groq key is cooling
down - a different provider entirely, so correlated outages can't sink a live demo.
Kept as fallback rather than primary: Groq's ~10× token throughput is what keeps
SPECTRA's <60 s live-parse demo comfortable, while NIM's larger open models add
accuracy headroom we currently don't need (extraction is already verified exact).

*The Gemini free tier exposes no Pro-tier quota; with billing enabled set
`MODEL_REASONING_PRO=gemini-3.1-pro-preview` - a one-line change. Configured ids are
validated against the account's live `models.list` at API startup. Deterministic
computation always runs first; LLMs handle only qualitative judgement and narrative,
and acceptance criteria in GUIDE are authored data an LLM can never override.

## Repository layout

```
data/sources/          real-format inputs (PDF, XER, XLSX)  + SOURCES_MANIFEST.md
data/cache/            parsed facts (generated, git-ignored) - the demo-safety fallback
data/tia942_procedures.json   the ONE authored structured file (acceptance criteria)
core/                  parsers, entity extractor, KG, vector store, event bus, NCR, config
agents/                SPECTRA, CHRONOS, TRACIS, GUIDE, ORACLE (+ provider router)
convergence/engine.py  cross-agent convergence scoring + narrative
api/main.py            FastAPI - the only process that touches data/
frontend/              React + Vite + Tailwind (5 routes), pure HTTP client
scripts/               build_cache.py, verify.py, what_if.py
demo/                  run_demo.sh, assets/ (submittal revisions R2/R3)
```

## Known assumptions (stated, not hidden)

- Convergence threshold 0.65 and the 1.5 critical-path weight are demo heuristics
  (config constants, labelled as such in every alert payload).
- SLA exposure uses an assumed $50,000/day liquidated-damages rate.
- Hours-saved = submittals×5.5 h + RFIs×0.75 h + ITPs×2.5 h, computed live; the
  dashboard figure and the spoken figure are the same number by construction.
- Tiers are written "Rated-3 (TIA-942) / Tier III (Uptime)" - Rated is TIA-942
  terminology, Tier is Uptime Institute's. TIA-942-C's AI addendum is announced
  (Mar 2026), not published; NEXUS-DC is built to ingest it on publication.
