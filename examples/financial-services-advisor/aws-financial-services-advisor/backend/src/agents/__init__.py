"""Strands agents for the Financial Services Advisor.

One supervisor builds and delegates to the four specialist sub-agents; their
system prompts live in :mod:`.prompts`.
"""

from .supervisor import create_supervisor_agent, get_supervisor_agent, reset_supervisor_agent

__all__ = [
    "create_supervisor_agent",
    "get_supervisor_agent",
    "reset_supervisor_agent",
]
