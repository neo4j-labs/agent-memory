"""Shared Aura connection through Bolt and readback for integration recipes."""

import os


def settings(embedding=None):
    from neo4j_agent_memory import BoltSettings
    from neo4j_agent_memory.llm import from_provider

    return BoltSettings(
        neo4j={
            "uri": os.environ["NEO4J_URI"],
            "username": os.environ["NEO4J_USERNAME"],
            "password": os.environ["NEO4J_PASSWORD"],
            "database": os.getenv("NEO4J_DATABASE", "neo4j"),
        },
        embedding=embedding or from_provider("openai/text-embedding-3-small", kind="embedding"),
        extraction={"extractor_type": "none"},
    )


async def verify_messages(client, session_id, expected):
    conversation = await client.short_term.get_conversation(session_id)
    contents = [message.content for message in conversation.messages]
    if not all(text in contents for text in expected):
        raise RuntimeError(f"Message readback failed for {session_id}")
    print(f"Verified stored messages; session={session_id}")


async def recorded_turn(client, session_id, prompt, respond):
    """Record a task outcome; this does not claim to capture hidden model reasoning."""
    trace = await client.reasoning.start_trace(session_id=session_id, task=prompt)
    try:
        await client.short_term.add_message(session_id, "user", prompt, extract_entities=False)
        context = await client.get_context(prompt, session_id=session_id)
        reply = str(await respond(context))
        if not reply.strip():
            raise RuntimeError("The framework returned no text")
        await client.short_term.add_message(session_id, "assistant", reply, extract_entities=False)
        await verify_messages(client, session_id, [prompt, reply])
    except Exception as exc:
        await client.reasoning.complete_trace(
            trace.id, success=False, outcome=f"Run failed: {type(exc).__name__}"
        )
        raise
    await client.reasoning.complete_trace(
        trace.id, success=True, outcome="Reply stored and read back"
    )
    stored = await client.reasoning.get_trace_with_steps(trace.id)
    if stored is None or stored.success is not True:
        raise RuntimeError(f"Trace readback failed for {trace.id}")
    print(f"Verified task trace: {trace.id}")
    return reply
