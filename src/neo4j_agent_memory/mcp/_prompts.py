"""MCP prompt definitions for Neo4j Agent Memory.

Prompts surface as slash commands in Claude Desktop and /mcp__ commands
in Claude Code. They guide the LLM through structured workflows.

Organized into profiles:
- Core: memory-conversation (always available)
- Extended: memory-reasoning, memory-review
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp.prompts import Message

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_prompts(mcp: FastMCP, *, profile: str = "extended") -> None:
    """Register MCP prompts on the server based on profile.

    Args:
        mcp: FastMCP server instance.
        profile: Tool profile - 'core' or 'extended'.
    """
    _register_core_prompts(mcp)
    if profile == "extended":
        _register_extended_prompts(mcp)


def _register_core_prompts(mcp: FastMCP) -> None:
    """Register the core prompt (memory-conversation)."""

    @mcp.prompt(name="memory-conversation")
    def memory_conversation(session_id: str = "") -> list[Message]:
        """Initialize a memory-aware conversation.

        Loads context from memory and instructs Claude on how to use
        memory tools throughout the conversation. Use at the start
        of any conversation that should leverage stored memories.
        """
        session_hint = f" for session '{session_id}'" if session_id else ""
        return [
            Message(
                role="user",
                content=(
                    f"Start a memory-aware conversation{session_hint}.\n\n"
                    "Steps:\n"
                    "1. Call memory_get_context to load relevant memories"
                    + (f" (session_id='{session_id}')" if session_id else "")
                    + "\n"
                    "2. Review the loaded context for:\n"
                    "   - Previous conversation topics and decisions\n"
                    "   - Known user preferences\n"
                    "   - Relevant entities and relationships\n"
                    "3. Greet the user, referencing relevant context if available\n"
                    "4. Throughout the conversation:\n"
                    "   - Call memory_store_message for important user messages\n"
                    "   - Call memory_add_preference when preferences are expressed\n"
                    "   - Call memory_add_entity when new people/places/orgs are mentioned\n"
                    "   - Call memory_search if the user asks about past interactions"
                ),
            )
        ]


def _register_extended_prompts(mcp: FastMCP) -> None:
    """Register extended prompts (memory-reasoning, memory-review)."""

    @mcp.prompt(name="memory-reasoning")
    def memory_reasoning(task: str) -> list[Message]:
        """Record a reasoning trace for a complex task.

        Guides Claude through structured reasoning with step-by-step
        trace recording. Useful for debugging and learning from
        successful problem-solving approaches.
        """
        return [
            Message(
                role="user",
                content=(
                    f"Solve this task and record your reasoning: {task}\n\n"
                    "Steps:\n"
                    "1. Call memory_start_trace with the task description\n"
                    "2. For each significant reasoning step:\n"
                    "   a. Think about what to do next (thought)\n"
                    "   b. Take an action or make a decision (action)\n"
                    "   c. Observe the result (observation)\n"
                    "   d. Call memory_record_step with thought, action, observation\n"
                    "   e. If you use a tool, include tool_name, tool_args, tool_result\n"
                    "3. When the task is complete:\n"
                    "   - Call memory_complete_trace with the outcome\n"
                    "   - Set success=true if completed, false if not\n"
                    "4. Summarize the reasoning process and final outcome"
                ),
            )
        ]

    @mcp.prompt(name="memory-review")
    def memory_review() -> list[Message]:
        """Review stored knowledge and flag contradictions.

        Summarizes everything stored in memory: entities, preferences,
        facts, and recent conversations. Identifies potential
        contradictions or outdated information.
        """
        return [
            Message(
                role="user",
                content=(
                    "Review everything stored in my memory and provide a summary.\n\n"
                    "Steps:\n"
                    "1. Call memory_search with a broad query to find entities\n"
                    "2. Call memory_search with memory_types=['preferences'] to find preferences\n"
                    "3. Call memory_list_sessions to see conversation history\n"
                    "4. For the most relevant entities, call memory_get_entity for details\n"
                    "5. Compile a summary organized by:\n"
                    "   - Known entities (people, organizations, locations)\n"
                    "   - Stored preferences by category\n"
                    "   - Key facts and relationships\n"
                    "   - Recent conversation topics\n"
                    "6. Flag any potential contradictions or outdated information\n"
                    "7. Suggest any preferences or facts that should be updated"
                ),
            )
        ]
