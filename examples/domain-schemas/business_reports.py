#!/usr/bin/env python3
"""Compatibility wrapper: business document analysis.

The eight per-domain scripts were merged into one parametrized runner in
v0.6. This file is kept for one release so existing links keep working:

    python run.py --schema business

Any extra arguments are forwarded, so `python business_reports.py --store` still works.
"""

from __future__ import annotations

import asyncio
import sys

from run import main

SCHEMA = "business"

if __name__ == "__main__":
    print(f"NOTE: {__file__} is now a wrapper around: python run.py --schema {SCHEMA}")
    print("      The corpus moved to samples/business.py; this wrapper goes away next release.\n")
    sys.exit(asyncio.run(main(["--schema", SCHEMA, *sys.argv[1:]])))
