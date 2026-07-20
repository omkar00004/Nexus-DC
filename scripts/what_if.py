"""Falsifiability encore driver: swap which vendor submittal revision is live.

    .venv/bin/python scripts/what_if.py status   # which revision is in data/sources/
    .venv/bin/python scripts/what_if.py rev3     # vendor resubmits: uprated 2.1 MVA
    .venv/bin/python scripts/what_if.py rev2     # roll back to the original R2

Demo narration for `rev3`:
    "PowerMax has just issued Revision 3 with the uprated 2.1 MVA module -
     watch the platform ingest the resubmittal."
Then click Run All Agents: the ELEC-4.2.1 deviation disappears (3 -> 2), the
convergence score recomputes. `rev2` brings it all back. Nothing here touches
caches or results - only the source document changes, like on a real project.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import demo_tools


def main() -> None:
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    if cmd == "status":
        print(demo_tools.active_revision())
    elif cmd in ("rev2", "rev3"):
        result = demo_tools.set_revision({"rev2": "R2", "rev3": "R3"}[cmd])
        print(result)
        print("-> now click 'Run All Agents' (or POST /agents/all/run) to re-derive")
    elif cmd == "generate":
        print(demo_tools.generate_assets(force="--force" in sys.argv))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
