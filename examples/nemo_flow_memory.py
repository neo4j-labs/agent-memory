"""Run Neo4j Agent Memory's NeMo Flow integration without external services.

This example uses a tiny in-memory object with the subset of `MemoryClient`
methods that the integration uses. Replace `DemoMemoryClient` with a connected
`neo4j_agent_memory.MemoryClient` in a real application.

Install the optional dependency before running:

    pip install "neo4j-agent-memory[nemo-flow]"
    python examples/nemo_flow_memory.py
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any


@dataclass
class DemoMessage:
    role: str
    content: str


@dataclass
class DemoConversation:
    messages: list[DemoMessage]


class DemoShortTermMemory:
    """Small in-memory short-term memory for a no-credentials smoke run."""

    def __init__(self) -> None:
        self.conversations: dict[str, list[DemoMessage]] = {
            "alex:demo-thread": [
                DemoMessage("user", "Alex prefers tea in the afternoon."),
            ]
        }
        self.add_calls: list[dict[str, Any]] = []

    async def get_conversation(
        self, session_id: str, *, limit: int | None = None
    ) -> DemoConversation:
        messages = self.conversations.get(session_id, [])
        if limit is not None:
            messages = messages[-limit:]
        return DemoConversation(messages=list(messages))

    async def search_messages(self, query: str, **kwargs: Any) -> list[DemoMessage]:
        return []

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> DemoMessage:
        message = DemoMessage(role, content)
        self.conversations.setdefault(session_id, []).append(message)
        self.add_calls.append(
            {
                "session_id": session_id,
                "role": role,
                "content": content,
                "metadata": metadata,
            }
        )
        return message


class DemoMemoryClient:
    """Small Neo4j-agent-memory-compatible client for the smoke run."""

    def __init__(self) -> None:
        self.short_term = DemoShortTermMemory()


async def run_demo() -> dict[str, Any]:
    from neo4j_agent_memory.integrations import nemo_flow

    memory = DemoMemoryClient()
    handle = nemo_flow.install(
        memory,
        name="neo4j_agent_memory.example.nemo_flow",
        include_user_messages=False,
    )
    provider_requests = []

    async def demo_llm(request: Any) -> dict[str, Any]:
        provider_requests.append(request.content)
        system_context = "\n".join(
            message["content"]
            for message in request.content["messages"]
            if message.get("role") == "system"
            and "Relevant memory context:" in message.get("content", "")
        )
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": f"I found your memory: {system_context}",
                    }
                }
            ]
        }

    try:
        with nemo_flow.memory_scope(user_id="alex", thread_id="demo-thread"):
            response = await _run_instrumented_framework_llm(
                "demo-llm",
                {
                    "model": "demo-model",
                    "messages": [{"role": "user", "content": "What do I like to drink?"}],
                },
                demo_llm,
            )
    finally:
        handle.uninstall()

    return {
        "response": response,
        "provider_requests": provider_requests,
        "add_calls": memory.short_term.add_calls,
    }


async def _run_instrumented_framework_llm(
    name: str,
    content: dict[str, Any],
    provider: Any,
) -> dict[str, Any]:
    """Simulate the LLM boundary that a patched framework would own."""

    import nemo_flow

    request = nemo_flow.LLMRequest({}, content)
    return await nemo_flow.llm.execute(name, request, provider)


def main() -> None:
    print(json.dumps(asyncio.run(run_demo()), indent=2))


if __name__ == "__main__":
    main()
