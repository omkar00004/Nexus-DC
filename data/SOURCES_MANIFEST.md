# NEXUS-DC - Source Artifacts Manifest

**Honesty note.** Every file below is **synthetic content in an authentic industry format**.
Vendor ("PowerMax Systems Pvt Ltd") and project ("Meridian Data Centre - Phase 1, Nashik,
24 MW, Rated-3/Tier III") are fictional. No real company's document is reproduced and no real
branding is used. The formats are real and machine-parseable (P6 XER, PDF, XLSX, JSON) so the
NEXUS-DC parsers operate on genuine artifacts. **No answer keys:** deviations, delays and risk
scores are NOT written into these inputs - the agents must derive them.

## Files

| # | File | Format | Parsed by | Role |
|---|------|--------|-----------|------|
| 1 | `schedule.xer` | Primavera P6 XER (`ERMHDR`/`%T`/`%F`/`%R`, tab-delimited) | `xerparser` | CHRONOS - CPM + delay cascade |
| 2 | `ups_submittal.pdf` | Vendor datasheet PDF (4 pp) | pdfplumber/PyMuPDF | SPECTRA - parameter extraction |
| 3 | `specification.pdf` | Clause-numbered spec PDF (6 pp; pg 1 image-only) | pdfplumber + OCR fallback | SPECTRA/ORACLE - compliance thresholds |
| 4 | `rfi_register.xlsx` | EDMS/Procore-style register (15 RFIs) | openpyxl | TRACIS/ORACLE - RFI status + linkage |
| 5 | `electrical_sld.pdf` | Electrical single-line diagram (A3) | reference drawing | context / RFI + tag cross-refs |
| 6 | `pid_sample.pdf` | Chilled-water P&ID (A3) | limited vision demo (scoped, not production) | context / cooling system |
| 7 | `tia942_procedures.json` | Authored structured procedures (5) | direct load | GUIDE - deterministic pass/fail |

## Planted realities (in SOURCE VALUES only - agents must discover them)

**SPECTRA - three UPS-02A deviations must emerge by comparison:**
| Parameter | Submittal (source) | Spec threshold | Outcome |
|-----------|--------------------|----------------|---------|
| Rated output | **1.8 MVA** | `ELEC-4.2.1` ≥ 2.0 MVA | deviation (CRITICAL) |
| Inverter→bypass transfer | **6 ms** | `ELEC-4.2.2` ≤ 4 ms | deviation (CRITICAL) |
| Input current THD | **4.5 %** | `ELEC-4.2.3` ≤ 3 % | deviation (MAJOR) |
| Efficiency | 96.5 % | `ELEC-4.2.4` ≥ 96 % | **in spec** (distractor) |
| Battery autonomy | 10 min | `ELEC-4.2.5` ≥ 5 min | **in spec** (distractor) |

**CHRONOS - schedule cascade:** UPS-02A delivery (`ACT-014`) slips **+29 calendar days**
(target finish 2026-07-15 → current 2026-08-13) on the driving path; the finish milestone
`ACT-052` "Rated-3 (TIA-942) / Tier III (Uptime) Commissioning Complete" moves from
2026-11-26 (baseline) to **2026-12-25** (current). Data date 2026-07-20 → UPS-02A pending
(at-risk). 52 activities, 72 relationships.

**TRACIS - supply-chain buffer:** UPS-02A required-by (`ACT-033` early start) 2026-08-14 vs
revised ETA 2026-08-13 → near-zero/negative buffer → computed at-risk.

**GUIDE - scripted commissioning failure:** `CX-UPS-CM-01` step 4 acceptance is
`retransfer_time_ms ≤ 4`; a **6 ms** reading fails deterministically → sign-off blocked, RFI raised.

**ORACLE - open RFIs on critical path (real graph traversal targets):**
- `RFI-0003` (Open) → `ACT-033` UPS-02A installation
- `RFI-0007` (Open) → `ACT-046` UPS-02A commissioning/retransfer
- `RFI-0009` (Open) → `ACT-031` MV switchgear (arc-flash labelling)
- `RFI-0012` (Open) → `ACT-032` generator (fuel-tank capacity)

## Cross-file consistency
Equipment tag `UPS-02A`, activity IDs `ACT-0xx`, RFI IDs `RFI-00xx`, and clause IDs (`ELEC-4.2.x`)
are shared across the schedule, submittal, spec, register, drawings and procedures so the
knowledge graph links them into one entity view. Tiers are written "Rated-3 (TIA-942) /
Tier III (Uptime)". Standard references in the procedures are marked "representative" (no
fabricated TIA-942 clause numbers).
