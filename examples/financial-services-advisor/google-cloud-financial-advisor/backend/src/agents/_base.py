"""Shared construction for the four specialist agents.

The KYC, AML, Relationship and Compliance agents differ only in their name,
description, instruction and domain tools — so they are built by one function
here instead of four near-identical factories.

Memory wiring follows ADK's native contract:

* **reads** use ADK's built-in ``load_memory`` tool, which calls
  ``BaseMemoryService.search_memory`` on the ``Runner``'s ``memory_service``
  (our ``Neo4jMemoryService``). No per-agent search tool, no hand-rolled glue.
* **writes** stay bespoke, because "record this finding" is domain vocabulary:
  each agent gets one ``store_*_finding`` tool that writes a first-class
  ``:Fact`` through ``client.long_term.add_fact``.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Sequence
from functools import wraps
from typing import TYPE_CHECKING, Any

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool, load_memory

if TYPE_CHECKING:
    from ..services.memory_service import FinancialMemoryService
    from ..services.neo4j_service import Neo4jDomainService

logger = logging.getLogger(__name__)

#: Default Gemini model. Real deployments pass ``VERTEX_AI_MODEL_ID`` through
#: ``get_supervisor_agent`` — this is only the no-configuration fallback.
DEFAULT_MODEL = "gemini-2.5-flash"


def bind_tool(func: Callable[..., Any], neo4j_service: Neo4jDomainService) -> Callable[..., Any]:
    """Create a wrapper that binds ``neo4j_service`` to a tool function.

    ADK's ``FunctionTool`` inspects the signature to decide which parameters the
    LLM must supply, so the wrapper advertises a signature with
    ``neo4j_service`` removed. ``functools.partial`` does **not** work here: it
    sets a default but leaves the parameter in the signature.
    """
    sig = inspect.signature(func)
    new_params = [p for name, p in sig.parameters.items() if name != "neo4j_service"]

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        kwargs["neo4j_service"] = neo4j_service
        return await func(*args, **kwargs)

    wrapper.__signature__ = sig.replace(parameters=new_params)  # type: ignore[attr-defined]
    return wrapper


def domain_tools(
    functions: Sequence[Callable[..., Any]],
    neo4j_service: Neo4jDomainService | None,
) -> list[Any]:
    """Wrap domain tool functions as ``FunctionTool``s, binding Neo4j if given."""
    if neo4j_service is None:
        return [FunctionTool(func) for func in functions]
    return [FunctionTool(bind_tool(func, neo4j_service)) for func in functions]


def create_specialist_agent(
    *,
    name: str,
    description: str,
    instruction: str,
    functions: Sequence[Callable[..., Any]],
    memory_service: FinancialMemoryService | None = None,
    model: str = DEFAULT_MODEL,
    neo4j_service: Neo4jDomainService | None = None,
    write_tools: Sequence[Callable[..., Any]] = (),
) -> LlmAgent:
    """Build one specialist ``LlmAgent``.

    Args:
        name: ADK agent name (also the ``event.author`` value).
        description: What the supervisor sees when choosing a delegate.
        instruction: The agent's system instruction.
        functions: Domain tool functions taking ``neo4j_service=``.
        memory_service: When provided, ``load_memory`` plus the agent's
            write-side tools are added.
        model: Gemini model id.
        neo4j_service: Domain data service for Neo4j queries.
        write_tools: Already-bound async callables that write to memory.

    Returns:
        The configured agent.
    """
    tools: list[Any] = domain_tools(functions, neo4j_service)

    if memory_service is not None:
        # ADK's own memory-read tool, served by Neo4jMemoryService.
        tools.append(load_memory)
        tools.extend(FunctionTool(func) for func in write_tools)

    agent = LlmAgent(
        name=name,
        model=model,
        description=description,
        instruction=instruction,
        tools=tools,
    )
    logger.info("%s created (model=%s, tools=%d)", name, model, len(tools))
    return agent
