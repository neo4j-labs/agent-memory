"""Domain-specific tool definitions for the audit-trail example.

Demonstrates the OMG-style ``infer_touched_entities`` pattern: a
hand-written mapping from tool calls to the entities those calls
reference. Used by the ``on_tool_call_recorded`` hook in ``main.py``
to write ``(:ReasoningStep)-[:TOUCHED]->(:Entity)`` edges automatically
after every ``record_tool_call``.
"""

from __future__ import annotations

from typing import Any

from neo4j_agent_memory.schema.models import EntityRef


# tag::infer_touched[]
def infer_touched(
    tool_name: str,
    arguments: dict[str, Any],
    result: Any,
) -> list[EntityRef]:
    """Map a tool call to the entities it touched.

    This mapping is unavoidably domain-specific — only the agent author
    knows that ``recommend_team(client_name=...)`` references a
    ``:Client`` entity, that ``find_consultants(skill=...)`` references
    a ``:Skill``, etc. The library can't infer this; it provides the
    hook that calls into this function.
    """
    if tool_name == "recommend_team":
        refs: list[EntityRef] = []
        client_name = arguments.get("client_name")
        if client_name:
            refs.append(EntityRef(name=client_name, type="Client"))
        # The result is typically a list of consultant dicts.
        if isinstance(result, list):
            for row in result:
                if isinstance(row, dict) and "consultant" in row:
                    refs.append(
                        EntityRef(name=row["consultant"], type="PERSON")
                    )
        return refs

    if tool_name == "find_consultants":
        skill = arguments.get("skill")
        return [EntityRef(name=skill, type="Skill")] if skill else []

    if tool_name == "lookup_industry":
        industry = arguments.get("industry") or arguments.get("name")
        return [EntityRef(name=industry, type="Industry")] if industry else []

    return []
# end::infer_touched[]
