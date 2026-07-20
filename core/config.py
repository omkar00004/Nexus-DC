"""Central configuration: paths, model registry, thresholds, LLM defaults.

The model registry is env-driven (.env) so provider/model swaps are config
changes, never code changes. `resolve_models()` validates the configured
Gemini ids against the account's live models.list at startup.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# --- paths ---
DATA_DIR = PROJECT_ROOT / "data"
SOURCES_DIR = DATA_DIR / "sources"
CACHE_DIR = DATA_DIR / "cache"
CHROMA_DIR = DATA_DIR / "chroma"
KG_PATH = DATA_DIR / "knowledge_graph.json"
EVENTS_PATH = DATA_DIR / "events.json"
PROCEDURES_PATH = DATA_DIR / "tia942_procedures.json"

SPEC_PDF = SOURCES_DIR / "specification.pdf"
SUBMITTAL_PDF = SOURCES_DIR / "ups_submittal.pdf"
SCHEDULE_XER = SOURCES_DIR / "schedule.xer"
RFI_REGISTER_XLSX = SOURCES_DIR / "rfi_register.xlsx"
PID_PDF = SOURCES_DIR / "pid_sample.pdf"

# --- API keys ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# key POOL: comma-separated GROQ_API_KEYS (round-robin, per-key rate-limit
# cooldown); single GROQ_API_KEY still accepted for back-compat
GROQ_API_KEYS = [k.strip() for k in
                 (os.getenv("GROQ_API_KEYS") or os.getenv("GROQ_API_KEY", "")).split(",")
                 if k.strip()]
GROQ_API_KEY = GROQ_API_KEYS[0] if GROQ_API_KEYS else ""

# optional third provider: NVIDIA NIM (OpenAI-compatible). Enabled iff key set.
NIM_API_KEY = os.getenv("NIM_API_KEY", "")
NIM_BASE_URL = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
NIM_MODEL = os.getenv("NIM_MODEL", "meta/llama-3.1-405b-instruct")

# optional LLM observability (Langfuse): traces, latency, token usage per call.
# No-op unless both keys are set.
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

# --- model registry (role -> model id) ---
MODEL_REASONING_PRO = os.getenv("MODEL_REASONING_PRO", "gemini-3.5-flash")
MODEL_REASONING_FLASH = os.getenv("MODEL_REASONING_FLASH", "gemini-3.5-flash")
MODEL_VISION = os.getenv("MODEL_VISION", "gemini-flash-latest")
MODEL_EXTRACTION = os.getenv("MODEL_EXTRACTION", "openai/gpt-oss-120b")
MODEL_EXTRACTION_FALLBACK = os.getenv("MODEL_EXTRACTION_FALLBACK", "openai/gpt-oss-20b")

# --- LLM call defaults ---
TEMP_EXTRACTION = 0.1
TEMP_NARRATIVE = 0.3
MAX_TOKENS_DEFAULT = 2000
MAX_TOKENS_NARRATIVE = 4000

# --- convergence (demo heuristics, stated as assumptions in the UI) ---
CONVERGENCE_THRESHOLD = float(os.getenv("CONVERGENCE_THRESHOLD", "0.65"))
CRITICAL_PATH_WEIGHT = 1.5
RISK_STORM_MIN_ENTITIES = 3
SLA_PENALTY_PER_DAY_USD = float(os.getenv("SLA_PENALTY_PER_DAY_USD", "50000"))

# --- hours-saved story: ONE consistent set of per-item figures ---
HOURS_PER_SUBMITTAL = 5.5
HOURS_PER_RFI = 0.75
HOURS_PER_ITP = 2.5

EMBEDDING_MODEL = "all-MiniLM-L6-v2"

API_BASE_URL = os.getenv("NEXUS_API_URL", "http://localhost:8000")


def resolve_models(verbose: bool = True) -> dict:
    """Validate configured Gemini model ids against the account's live model list.

    Returns {"ok": [...], "missing": [...], "substitutions": {role: new_id}}.
    Network/auth failures degrade to a no-op (the configured ids stay in force)
    so an offline start never crashes the API.
    """
    report = {"ok": [], "missing": [], "substitutions": {}}
    roles = {
        "MODEL_REASONING_PRO": MODEL_REASONING_PRO,
        "MODEL_REASONING_FLASH": MODEL_REASONING_FLASH,
        "MODEL_VISION": MODEL_VISION,
    }
    try:
        from google import genai

        client = genai.Client(api_key=GEMINI_API_KEY)
        available = {m.name.removeprefix("models/") for m in client.models.list()}
    except Exception as exc:  # offline / bad key: keep configured ids
        if verbose:
            print(f"[config] model resolution skipped ({type(exc).__name__}: {exc})")
        return report

    flash_builds = sorted(m for m in available if m.startswith("gemini-") and "flash" in m)
    for role, mid in roles.items():
        if mid in available:
            report["ok"].append(mid)
        else:
            report["missing"].append(mid)
            # the Flash tier renames fast - fall back to the newest flash build
            if flash_builds:
                sub = "gemini-flash-latest" if "gemini-flash-latest" in available else flash_builds[-1]
                report["substitutions"][role] = sub
                globals()[role] = sub
                if verbose:
                    print(f"[config] {role}={mid!r} not exposed by this account; using {sub!r}")
    return report
