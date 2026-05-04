"""Live integration smoke for Neo4j Agent Memory with NeMo Flow."""

from __future__ import annotations

from uuid import uuid4

import pytest

pytest.importorskip("nemo_flow", reason="nemo-flow optional dependency is not installed")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_nemo_flow_recall_and_capture_with_real_memory_client(memory_client) -> None:
    """Exercise the adapter through real NeMo Flow and real Neo4j memory APIs."""
    import nemo_flow as nemo_flow_runtime

    from neo4j_agent_memory.integrations import nemo_flow

    user_id = f"test-nemo-user-{uuid4()}"
    thread_id = f"test-nemo-thread-{uuid4()}"
    session_id = f"{user_id}:{thread_id}"

    await memory_client.short_term.add_message(
        session_id,
        "user",
        "Alex prefers jasmine tea in the afternoon.",
        metadata={
            "user_id": user_id,
            "run_id": thread_id,
            "session_id": session_id,
        },
        extract_entities=False,
        generate_embedding=False,
    )

    handle = nemo_flow.install(
        memory_client,
        name=f"neo4j_agent_memory.integration.{uuid4()}",
        include_user_messages=False,
        extract_entities=False,
        generate_embeddings=False,
    )
    provider_requests = []

    async def provider(request):
        provider_requests.append(request.content)
        return {"choices": [{"message": {"content": "Jasmine tea."}}]}

    try:
        with nemo_flow.memory_scope(user_id=user_id, thread_id=thread_id):
            response = await nemo_flow_runtime.llm.execute(
                "neo4j-agent-memory-live-smoke",
                nemo_flow_runtime.LLMRequest(
                    {},
                    {
                        "model": "local-smoke",
                        "messages": [{"role": "user", "content": "What do I prefer to drink?"}],
                    },
                ),
                provider,
            )
    finally:
        handle.uninstall()

    assert response == {"choices": [{"message": {"content": "Jasmine tea."}}]}
    assert provider_requests
    messages = provider_requests[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "Relevant memory context:" in messages[0]["content"]
    assert "Alex prefers jasmine tea in the afternoon." in messages[0]["content"]

    conversation = await memory_client.short_term.get_conversation(session_id)
    stored_contents = [message.content for message in conversation.messages]
    assert "What do I prefer to drink?" in stored_contents
    assert "Jasmine tea." in stored_contents
