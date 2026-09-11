#!/usr/bin/env python3
"""Compatibility wrapper: movie and TV analysis.

The eight per-domain scripts were merged into one parametrized runner in
v0.6. This file is kept for one release so existing links keep working:

    python run.py --schema entertainment

Any extra arguments are forwarded, so `python entertainment_content.py --store` still works.
"""

from __future__ import annotations

import asyncio
import sys

from run import main

SCHEMA = "entertainment"

if __name__ == "__main__":
    print(f"NOTE: {__file__} is now a wrapper around: python run.py --schema {SCHEMA}")
    print(
        "      The corpus moved to samples/entertainment.py; this wrapper goes away next release.\n"
    )
    sys.exit(asyncio.run(main(["--schema", SCHEMA, *sys.argv[1:]])))
