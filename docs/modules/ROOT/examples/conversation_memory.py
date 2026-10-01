"""Pass persisted history and explicit preferences to an actual chat completion."""

import argparse
import asyncio
import os

from core_memory_settings import settings

from neo4j_agent_memory import MemoryClient

USER = "docs-shopper"
OLD_SESSION = "docs-shopping-first"
NEW_SESSION = "docs-shopping-return"
PRODUCT = "Trail Starter: fictional walking shoe; price USD 45; wide fit."


async def seed(client):
    if (await client.short_term.get_conversation(OLD_SESSION)).messages:
        raise RuntimeError("Already seeded; run resume or use a fresh tutorial database")
    await client.short_term.add_message(
        OLD_SESSION,
        "user",
        "I need wide walking shoes for a city trip.",
        user_identifier=USER,
        extract_entities=False,
    )
    await client.short_term.add_message(
        OLD_SESSION,
        "assistant",
        "I will look for wide walking shoes.",
        user_identifier=USER,
        extract_entities=False,
    )
    await client.long_term.add_preference(
        "budget",
        "Spend at most USD 60 on walking shoes",
        user_identifier=USER,
        context="Explicit preference supplied by this exercise",
    )
    await client.long_term.add_entity(
        "Trail Starter",
        "OBJECT",
        subtype="PRODUCT",
        description=PRODUCT,
        resolve=False,
        deduplicate=False,
    )
    print("Stored: first conversation, explicit shopper preference, fictional product")


async def resume(client, llm, model):
    history = await client.short_term.get_conversation(OLD_SESSION)
    preferences = await client.long_term.get_preferences_for(USER)
    if len(history.messages) != 2 or not preferences:
        raise RuntimeError("Run seed before resume")
    prompt = "What would you recommend for the trip I mentioned?"
    user_message = await client.short_term.add_message(
        NEW_SESSION, "user", prompt, user_identifier=USER, extract_entities=False
    )
    trace = await client.reasoning.start_trace(
        NEW_SESSION,
        "Recommend a product using saved shopper context",
        triggered_by_message_id=user_message.id,
        user_identifier=USER,
    )
    try:
        step = await client.reasoning.add_step(trace.id, action="Read the tutorial product")
        products = await client.query.cypher(
            "MATCH (e:Entity {name: $name, type: 'OBJECT', subtype: 'PRODUCT'}) "
            "RETURN e.description AS description",
            {"name": "Trail Starter"},
        )
        await client.reasoning.record_tool_call(
            step.id, "lookup_product", {"name": "Trail Starter"}, result=products
        )
        if not products:
            raise RuntimeError("Seeded product was not found")
        system = (
            "You are a shopping assistant. Use only the supplied fictional catalog. "
            "Do not invent stock or prices.\nSaved preferences:\n"
            + "\n".join(p.preference for p in preferences)
            + "\nCatalog:\n"
            + "\n".join(p["description"] for p in products)
        )
        messages = [{"role": "system", "content": system}]
        messages.extend({"role": m.role.value, "content": m.content} for m in history.messages)
        messages.append({"role": "user", "content": prompt})
        print(
            f"Retrieved: {len(history.messages)} prior messages and {len(preferences)} preferences"
        )
        response = await llm.chat.completions.create(model=model, messages=messages)
        answer = response.choices[0].message.content
        if not answer:
            raise RuntimeError("The model returned no text")
        saved = await client.short_term.add_message(
            NEW_SESSION, "assistant", answer, user_identifier=USER, extract_entities=False
        )
        await client.reasoning.complete_trace(
            trace.id, outcome="Saved a response using retrieved shopper context", success=True
        )
    except Exception as error:
        await client.reasoning.complete_trace(
            trace.id, outcome=f"Response failed: {type(error).__name__}", success=False
        )
        raise
    readback = await client.short_term.get_conversation(NEW_SESSION)
    assert any(message.id == saved.id for message in readback.messages)
    recorded = await client.reasoning.get_trace_with_steps(trace.id)
    assert recorded is not None and recorded.steps
    assert any(step.tool_calls for step in recorded.steps)
    print("Verified: new response and product-lookup trace read back")
    print(answer)
    return messages


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["seed", "resume"])
    args = parser.parse_args()
    async with MemoryClient(settings()) as client:
        if args.command == "seed":
            await seed(client)
        else:
            # Imported here: only this branch calls the chat model. Both branches
            # still need the openai extra, which the settings use for embeddings.
            from openai import AsyncOpenAI

            async with AsyncOpenAI() as llm:
                await resume(client, llm, os.environ["OPENAI_MODEL"])


if __name__ == "__main__":
    asyncio.run(main())
