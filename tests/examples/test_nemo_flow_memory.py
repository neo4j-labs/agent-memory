"""Smoke test for the NeMo Flow memory example."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

nemo_flow = pytest.importorskip(
    "nemo_flow", reason="nemo-flow optional dependency is not installed"
)


def _load_example_module():
    path = Path(__file__).resolve().parents[2] / "examples" / "nemo_flow_memory.py"
    spec = importlib.util.spec_from_file_location("neo4j_nemo_flow_memory_example", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_nemo_flow_memory_example_smoke() -> None:
    module = _load_example_module()

    result = await module.run_demo()

    assert result["provider_requests"][0]["messages"][0]["role"] == "system"
    assert (
        "Alex prefers tea in the afternoon."
        in result["provider_requests"][0]["messages"][0]["content"]
    )
    assert result["add_calls"] == [
        {
            "session_id": "alex:demo-thread",
            "role": "user",
            "content": "What do I like to drink?",
            "metadata": {
                "user_id": "alex",
                "run_id": "demo-thread",
                "session_id": "alex:demo-thread",
            },
        },
        {
            "session_id": "alex:demo-thread",
            "role": "assistant",
            "content": (
                "I found your memory: Relevant memory context:\n"
                "## Recent Conversation\n"
                "**user**: Alex prefers tea in the afternoon."
            ),
            "metadata": {
                "user_id": "alex",
                "run_id": "demo-thread",
                "session_id": "alex:demo-thread",
            },
        },
    ]
