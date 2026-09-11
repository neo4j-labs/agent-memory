#!/usr/bin/env python3
"""Compatibility wrapper: legal document analysis.

The eight per-domain scripts were merged into one parametrized runner in
v0.6. This file is kept for one release so existing links keep working:

    python run.py --schema legal

Any extra arguments are forwarded, so `python legal_documents.py --store` still works.
"""

from __future__ import annotations

import asyncio
import sys

from run import main

SCHEMA = "legal"

if __name__ == "__main__":
    print(f"NOTE: {__file__} is now a wrapper around: python run.py --schema {SCHEMA}")
    print("      The corpus moved to samples/legal.py; this wrapper goes away next release.\n")
    sys.exit(asyncio.run(main(["--schema", SCHEMA, *sys.argv[1:]])))
