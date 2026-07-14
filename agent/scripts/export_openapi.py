"""Regenerates the checked-in OpenAPI spec (agent/openapi.json) from the live FastAPI app object --
no running server needed. Run this after adding/changing any endpoint or Pydantic model in
app/main.py, then commit the diff.

Usage: python scripts/export_openapi.py
"""
from __future__ import annotations

import json
import os
import sys

_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AGENT_ROOT)  # run standalone (python scripts/export_openapi.py) without needing PYTHONPATH set

from app.main import app  # noqa: E402 -- import after sys.path fixup above

OUTPUT_PATH = os.path.join(_AGENT_ROOT, "openapi.json")


def main() -> None:
    spec = app.openapi()
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
