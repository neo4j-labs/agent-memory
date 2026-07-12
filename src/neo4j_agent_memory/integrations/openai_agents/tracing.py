"""OpenAI Agents SDK tracing integration.

Records OpenAI agent executions as reasoning traces for
learning from past interactions and improving future responses.
"""

from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.memory.reasoning import ToolCallStatus

if TYPE_CHECKING:
    from neo4j_agent_memory.memory.reasoning import ReasoningTrace

    from .memory import Neo4jOpenAIMemory

try:
    import openai  # noqa: F401 - verify openai is installed

    async def record_agent_trace(
        memory: "Neo4jOpenAIMemory",
        messages: list[dict[str, Any]],
        task: str,
        tool_calls: list[dict[str, Any]] | None = None,
        outcome: str | None = None,
        success: bool = True,
        generate_embedding: bool = True,
    ) -> "ReasoningTrace":
        """
        Record an OpenAI agent execution as a reasoning trace.

        This function captures the agent's conversation and tool usage
        as a reasoning trace that can be retrieved for similar future tasks.

        Args:
            memory: The Neo4jOpenAIMemory instance
            messages: The conversation messages from the agent execution
            task: Description of the task the agent was performing
            tool_calls: Optional list of tool calls made during execution
            outcome: Optional description of the outcome
            success: Whether the task completed successfully
            generate_embedding: Whether to generate embedding for similarity search

        Returns:
            The created ReasoningTrace object

        Example:
            # After agent completes
            trace = await record_agent_trace(
                memory=memory,
                messages=response.messages,
                task="Help user find product recommendations",
                tool_calls=extracted_tool_calls,
                outcome="Successfully provided 3 product recommendations",
                success=True,
            )
        """
        client = memory.memory_client

        # Start the trace
        trace = await client.reasoning.start_trace(
            session_id=memory.session_id,
            task=task,
            generate_embedding=generate_embedding,
        )

        # Process messages and extract steps
        current_step = None

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "user":
                # User messages become the context/input
                if current_step is None:
                    current_step = await client.reasoning.add_step(
                        trace_id=trace.id,
                        thought=f"User input: {content[:200]}..."
                        if len(content) > 200
                        else f"User input: {content}",
                    )

            elif role == "assistant":
                # Assistant messages with tool_calls
                msg_tool_calls = msg.get("tool_calls", [])

                if msg_tool_calls:
                    # Create step for tool usage
                    if current_step is None:
                        current_step = await client.reasoning.add_step(
                            trace_id=trace.id,
                            thought="Processing request with tools",
                            action="Using function tools",
                        )

                    # Record each tool call
                    for tc in msg_tool_calls:
                        func = tc.get("function", {})
                        tool_name = func.get("name", "unknown")
                        arguments = func.get("arguments", "{}")

                        # Parse arguments if string
                        if isinstance(arguments, str):
                            try:
                                import json

                                arguments = json.loads(arguments)
                            except (json.JSONDecodeError, TypeError):
                                arguments = {"raw": arguments}

                        await client.reasoning.record_tool_call(
                            step_id=current_step.id,
                            tool_name=tool_name,
                            arguments=arguments,
                            status=ToolCallStatus.SUCCESS,  # Default to success
                        )

                elif content:
                    # Assistant message without tool calls (final response)
                    if current_step:
                        # Add observation to current step
                        pass  # Could update step with observation
                    else:
                        current_step = await client.reasoning.add_step(
                            trace_id=trace.id,
                            thought="Generating response",
                            action=content[:200] + "..." if len(content) > 200 else content,
                        )

            elif role == "tool":
                # Tool response message
                if current_step and content:
                    # Could record tool result here
                    pass

        # Process explicit tool_calls if provided
        if tool_calls and current_step:
            for tc in tool_calls:
                tool_name = tc.get("name", tc.get("function", {}).get("name", "unknown"))
                arguments = tc.get("arguments", tc.get("function", {}).get("arguments", {}))
                result = tc.get("result")
                status = tc.get("status", "success")
                error = tc.get("error")

                # Parse arguments if string
                if isinstance(arguments, str):
                    try:
                        import json

                        arguments = json.loads(arguments)
                    except (json.JSONDecodeError, TypeError):
                        arguments = {"raw": arguments}

                await client.reasoning.record_tool_call(
                    step_id=current_step.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    result=result,
                    status=status,
                    error=error,
                )

        # Complete the trace
        await client.reasoning.complete_trace(
            trace_id=trace.id,
            outcome=outcome,
            success=success,
        )

        # Return the completed trace, falling back to the in-memory trace
        # if the re-fetch returns nothing.
        completed = await client.reasoning.get_trace(trace.id)
        return completed if completed is not None else trace

    async def get_similar_traces(
        memory: "Neo4jOpenAIMemory",
        task: str,
        limit: int = 5,
    ) -> list["ReasoningTrace"]:
        """
        Find similar past reasoning traces for a task.

        Use this to retrieve past experiences that may help inform
        the agent's approach to a similar task.

        Args:
            memory: The Neo4jOpenAIMemory instance
            task: The task description to find similar traces for
            limit: Maximum number of traces to return

        Returns:
            List of similar ReasoningTrace objects
        """
        return await memory.memory_client.reasoning.get_similar_traces(
            task=task,
            limit=limit,
        )

    def format_traces_for_prompt(traces: list["ReasoningTrace"]) -> str:
        """
        Format reasoning traces for inclusion in a system prompt.

        Args:
            traces: List of ReasoningTrace objects

        Returns:
            Formatted string describing past similar tasks
        """
        if not traces:
            return ""

        parts = ["## Past experience with similar tasks:\n"]

        for trace in traces:
            parts.append(f"### Task: {trace.task}")

            if trace.steps:
                parts.append("Steps taken:")
                for step in trace.steps[:3]:  # Limit steps shown
                    if step.thought:
                        parts.append(f"  - Thought: {step.thought[:100]}...")
                    if step.action:
                        parts.append(f"  - Action: {step.action[:100]}...")

            if trace.outcome:
                parts.append(f"Outcome: {trace.outcome}")

            if trace.success is not None:
                status = "✓ Succeeded" if trace.success else "✗ Failed"
                parts.append(f"Status: {status}")

            parts.append("")  # Blank line between traces

        return "\n".join(parts)


except ImportError:
    # OpenAI not installed
    pass
