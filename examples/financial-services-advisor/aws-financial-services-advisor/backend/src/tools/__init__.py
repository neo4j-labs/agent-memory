"""Tool implementations for the Financial Services Advisor agents.

Tools take their collaborators — ``neo4j_service`` for domain Cypher,
``memory_service`` for the three memory tiers — as keyword-only arguments.
:func:`bind_tool` binds whichever of those a tool declares, hides them from the
signature the LLM sees, and hands the result to Strands' ``@tool`` decorator.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from strands import tool
from strands.types.tools import AgentTool

#: Parameter names :func:`bind_tool` supplies. Anything a tool declares outside
#: this set stays visible to the model.
COLLABORATORS = ("neo4j_service", "memory_service")


def bind_tool(
    func: Callable[..., Any],
    neo4j_service: Any,
    memory_service: Any = None,
) -> AgentTool:
    """Bind collaborators to a tool function and register it with Strands.

    ``tool()`` builds the LLM-visible input schema from the function's
    ``__signature__``, its docstring and its type hints, so dropping the bound
    parameters from the signature hides them from the model while execution
    still receives them. (It matters that they are hidden rather than merely
    defaulted: a ``FinancialMemoryService`` annotation left in the signature
    makes Pydantic fail to build the schema at all.)

    Returning ``tool(wrapper)`` rather than the bare ``wrapper`` is what makes
    the tool *registerable*. ``ToolRegistry.process_tools`` accepts strings,
    dicts, modules, iterables, ``ToolProvider``s and ``AgentTool`` instances —
    a plain function matches none of those, so it is logged as an
    ``unrecognized tool specification`` and dropped, leaving the sub-agent with
    no tools at all.
    """
    signature = inspect.signature(func)
    available = {"neo4j_service": neo4j_service, "memory_service": memory_service}
    bound = {name: available[name] for name in COLLABORATORS if name in signature.parameters}
    visible_params = [param for name, param in signature.parameters.items() if name not in bound]

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        kwargs.update(bound)
        return await func(*args, **kwargs)

    wrapper.__signature__ = signature.replace(parameters=visible_params)  # type: ignore[attr-defined]
    return tool(wrapper)


__all__ = ["COLLABORATORS", "bind_tool"]
