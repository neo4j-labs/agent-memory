"""Comprehensive integration tests for reasoning memory."""

import pytest

from neo4j_agent_memory.memory.reasoning import ToolCallStatus


@pytest.mark.integration
class TestReasoningMemoryTraces:
    """Test reasoning trace operations."""

    @pytest.mark.asyncio
    async def test_start_trace_basic(self, memory_client, session_id):
        """Test starting a basic reasoning trace."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Find the best restaurant",
            generate_embedding=False,
        )

        assert trace is not None
        assert trace.id is not None
        assert trace.session_id == session_id
        assert trace.task == "Find the best restaurant"
        assert trace.success is None
        assert trace.outcome is None
        assert trace.started_at is not None

    @pytest.mark.asyncio
    async def test_start_trace_with_embedding(self, memory_client, session_id):
        """Test starting a trace with task embedding."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Search for Italian restaurants nearby",
            generate_embedding=True,
        )

        assert trace.task_embedding is not None
        assert len(trace.task_embedding) > 0

    @pytest.mark.asyncio
    async def test_complete_trace_success(self, memory_client, session_id):
        """Test completing a trace successfully."""
        # Start trace
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Test task",
            generate_embedding=False,
        )

        # Complete with success
        completed = await memory_client.reasoning.complete_trace(
            trace.id,
            outcome="Successfully found 3 restaurants",
            success=True,
        )

        assert completed.success is True
        assert completed.outcome == "Successfully found 3 restaurants"
        assert completed.completed_at is not None

    @pytest.mark.asyncio
    async def test_complete_trace_failure(self, memory_client, session_id):
        """Test completing a trace with failure."""
        # Start trace
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Test failing task",
            generate_embedding=False,
        )

        # Complete with failure
        completed = await memory_client.reasoning.complete_trace(
            trace.id,
            outcome="No results found due to API error",
            success=False,
        )

        assert completed.success is False
        assert "API error" in completed.outcome

    @pytest.mark.asyncio
    async def test_get_trace_by_id(self, memory_client, session_id):
        """Test retrieving a trace by ID."""
        # Create trace
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Retrievable task",
            generate_embedding=False,
        )

        # Retrieve by ID
        retrieved = await memory_client.reasoning.get_trace(trace.id)

        assert retrieved is not None
        assert retrieved.id == trace.id
        assert retrieved.task == "Retrievable task"

    @pytest.mark.asyncio
    async def test_get_traces_by_session(self, memory_client, session_id):
        """Test getting all traces for a session."""
        # Create multiple traces
        for i in range(3):
            await memory_client.reasoning.start_trace(
                session_id,
                task=f"Task {i}",
                generate_embedding=False,
            )

        # Get traces for session
        traces = await memory_client.reasoning.get_session_traces(session_id)

        assert len(traces) >= 3


@pytest.mark.integration
class TestReasoningMemorySteps:
    """Test reasoning step operations."""

    @pytest.mark.asyncio
    async def test_add_step_basic(self, memory_client, session_id):
        """Test adding a basic reasoning step."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Test task",
            generate_embedding=False,
        )

        step = await memory_client.reasoning.add_step(
            trace.id,
            thought="I should search for restaurants",
            action="search",
            generate_embedding=False,
        )

        assert step is not None
        assert step.id is not None
        assert step.thought == "I should search for restaurants"
        assert step.action == "search"
        assert step.step_number == 1

    @pytest.mark.asyncio
    async def test_add_step_with_observation(self, memory_client, session_id):
        """Test adding a step with observation."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Test task",
            generate_embedding=False,
        )

        step = await memory_client.reasoning.add_step(
            trace.id,
            thought="Searching for data",
            action="query_database",
            observation="Found 5 matching records",
            generate_embedding=False,
        )

        assert step.observation == "Found 5 matching records"

    @pytest.mark.asyncio
    async def test_multiple_steps_numbering(self, memory_client, session_id):
        """Test that step numbers increment correctly."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Multi-step task",
            generate_embedding=False,
        )

        steps = []
        for i in range(5):
            step = await memory_client.reasoning.add_step(
                trace.id,
                thought=f"Step {i} thought",
                action=f"action_{i}",
                generate_embedding=False,
            )
            steps.append(step)

        # Verify step numbers
        for i, step in enumerate(steps):
            assert step.step_number == i + 1

    @pytest.mark.asyncio
    async def test_get_trace_steps(self, memory_client, session_id):
        """Test getting all steps for a trace."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Test task",
            generate_embedding=False,
        )

        # Add multiple steps
        for i in range(3):
            await memory_client.reasoning.add_step(
                trace.id,
                thought=f"Thought {i}",
                action=f"action_{i}",
                generate_embedding=False,
            )

        # Get trace with steps
        full_trace = await memory_client.reasoning.get_trace(trace.id)

        assert full_trace is not None
        assert len(full_trace.steps) >= 3


@pytest.mark.integration
class TestReasoningMemoryToolCalls:
    """Test tool call operations."""

    @pytest.mark.asyncio
    async def test_record_tool_call_success(self, memory_client, session_id):
        """Test recording a successful tool call."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Test task",
            generate_embedding=False,
        )
        step = await memory_client.reasoning.add_step(
            trace.id,
            thought="Need to call API",
            action="call_api",
            generate_embedding=False,
        )

        tool_call = await memory_client.reasoning.record_tool_call(
            step.id,
            tool_name="weather_api",
            arguments={"city": "New York", "units": "celsius"},
            result={"temperature": 22, "conditions": "sunny"},
            status=ToolCallStatus.SUCCESS,
            duration_ms=150,
        )

        assert tool_call is not None
        assert tool_call.tool_name == "weather_api"
        assert tool_call.arguments == {"city": "New York", "units": "celsius"}
        assert tool_call.result == {"temperature": 22, "conditions": "sunny"}
        assert tool_call.status == ToolCallStatus.SUCCESS
        assert tool_call.duration_ms == 150

    @pytest.mark.asyncio
    async def test_record_tool_call_failure(self, memory_client, session_id):
        """Test recording a failed tool call."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Test task",
            generate_embedding=False,
        )
        step = await memory_client.reasoning.add_step(
            trace.id,
            thought="Attempting API call",
            action="call_api",
            generate_embedding=False,
        )

        tool_call = await memory_client.reasoning.record_tool_call(
            step.id,
            tool_name="external_api",
            arguments={"query": "test"},
            status=ToolCallStatus.ERROR,
            error="Connection timeout after 30s",
            duration_ms=30000,
        )

        assert tool_call.status == ToolCallStatus.ERROR
        assert tool_call.error == "Connection timeout after 30s"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_per_step(self, memory_client, session_id):
        """Test recording multiple tool calls for one step."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Complex task",
            generate_embedding=False,
        )
        step = await memory_client.reasoning.add_step(
            trace.id,
            thought="Need multiple APIs",
            action="multi_api_call",
            generate_embedding=False,
        )

        # Record multiple tool calls
        calls = []
        for i in range(3):
            call = await memory_client.reasoning.record_tool_call(
                step.id,
                tool_name=f"api_{i}",
                arguments={"index": i},
                result={"data": f"result_{i}"},
                status=ToolCallStatus.SUCCESS,
                duration_ms=100 + i * 50,
            )
            calls.append(call)

        assert len(calls) == 3
        assert all(c.status == ToolCallStatus.SUCCESS for c in calls)

    @pytest.mark.asyncio
    async def test_tool_call_with_complex_arguments(self, memory_client, session_id):
        """Test tool call with complex nested arguments."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Test task",
            generate_embedding=False,
        )
        step = await memory_client.reasoning.add_step(
            trace.id,
            thought="Complex query",
            action="complex_api",
            generate_embedding=False,
        )

        complex_args = {
            "filters": {
                "category": ["A", "B", "C"],
                "range": {"min": 10, "max": 100},
                "nested": {"deep": {"value": True}},
            },
            "options": ["opt1", "opt2"],
            "metadata": None,
        }

        tool_call = await memory_client.reasoning.record_tool_call(
            step.id,
            tool_name="complex_api",
            arguments=complex_args,
            result={"status": "ok"},
            status=ToolCallStatus.SUCCESS,
        )

        assert tool_call.arguments == complex_args

    @pytest.mark.asyncio
    async def test_get_trace_with_steps_includes_tool_calls(self, memory_client, session_id):
        """Tool calls are attached to their step when reading a full trace.

        Regression: ``get_trace_with_steps`` grouped tool calls by a
        ``step_id`` property that ``record_tool_call`` never persisted (it
        links via the ``USES_TOOL`` relationship), so ``step.tool_calls``
        always came back empty.
        """
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Screen owner",
            generate_embedding=False,
        )
        step = await memory_client.reasoning.add_step(
            trace.id,
            thought="Need to screen the beneficial owner",
            action="check_sanctions_list",
            generate_embedding=False,
        )
        await memory_client.reasoning.record_tool_call(
            step.id,
            tool_name="check_sanctions_list",
            arguments={"name": "Jane Doe"},
            result="NO MATCH",
            status=ToolCallStatus.SUCCESS,
        )

        full = await memory_client.reasoning.get_trace_with_steps(trace.id)

        assert full is not None
        assert len(full.steps) == 1
        recorded = full.steps[0].tool_calls
        assert len(recorded) == 1
        assert recorded[0].tool_name == "check_sanctions_list"
        assert recorded[0].arguments == {"name": "Jane Doe"}
        assert recorded[0].step_id == step.id


@pytest.mark.integration
class TestReasoningMemorySearch:
    """Test reasoning memory search functionality."""

    @pytest.mark.asyncio
    async def test_search_similar_traces(self, memory_client, session_id):
        """Test finding similar reasoning traces."""
        # Create traces for different tasks
        tasks = [
            "Find Italian restaurants nearby",
            "Search for pizza places",
            "Calculate the square root of 144",
            "Book a flight to Paris",
            "Find Japanese restaurants downtown",
        ]

        for task in tasks:
            trace = await memory_client.reasoning.start_trace(
                session_id,
                task=task,
                generate_embedding=True,
            )
            await memory_client.reasoning.complete_trace(
                trace.id,
                outcome=f"Completed: {task}",
                success=True,
            )

        # Search for restaurant-related traces
        similar = await memory_client.reasoning.get_similar_traces(
            "restaurant recommendations",
            limit=5,
        )

        assert isinstance(similar, list)

    @pytest.mark.asyncio
    async def test_get_tool_statistics(self, memory_client, session_id):
        """Test getting tool usage statistics."""
        # Create trace with tool calls
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Test task",
            generate_embedding=False,
        )
        step = await memory_client.reasoning.add_step(
            trace.id,
            thought="Testing tools",
            action="tool_test",
            generate_embedding=False,
        )

        # Record various tool calls
        for i in range(5):
            await memory_client.reasoning.record_tool_call(
                step.id,
                tool_name="test_tool",
                arguments={"call": i},
                status=ToolCallStatus.SUCCESS if i < 4 else ToolCallStatus.ERROR,
                duration_ms=100 + i * 10,
            )

        # Get statistics (if implemented)
        # stats = await memory_client.reasoning.get_tool_statistics("test_tool")
        # This depends on implementation


@pytest.mark.integration
class TestReasoningMemoryLifecycle:
    """Test complete reasoning trace lifecycle."""

    @pytest.mark.asyncio
    async def test_complete_trace_lifecycle(self, memory_client, session_id):
        """Test a complete reasoning trace from start to finish."""
        # Start trace
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Find and book a restaurant reservation",
            generate_embedding=True,
        )
        assert trace.success is None

        # Step 1: Search for restaurants
        step1 = await memory_client.reasoning.add_step(
            trace.id,
            thought="First, I need to search for available restaurants",
            action="search_restaurants",
            generate_embedding=True,
        )

        await memory_client.reasoning.record_tool_call(
            step1.id,
            tool_name="restaurant_search_api",
            arguments={"cuisine": "Italian", "location": "downtown"},
            result=[
                {"name": "La Trattoria", "available": True},
                {"name": "Pasta Palace", "available": True},
            ],
            status=ToolCallStatus.SUCCESS,
            duration_ms=250,
        )

        # Update step with observation
        step1_updated = await memory_client.reasoning.add_step(
            trace.id,
            thought="Found 2 restaurants, La Trattoria looks good",
            action="evaluate_options",
            observation="La Trattoria has better reviews",
            generate_embedding=False,
        )

        # Step 2: Make reservation
        step2 = await memory_client.reasoning.add_step(
            trace.id,
            thought="Now I'll make a reservation at La Trattoria",
            action="book_reservation",
            generate_embedding=False,
        )

        await memory_client.reasoning.record_tool_call(
            step2.id,
            tool_name="reservation_api",
            arguments={
                "restaurant": "La Trattoria",
                "date": "2024-12-20",
                "time": "19:00",
                "party_size": 2,
            },
            result={"confirmation": "RES-12345", "status": "confirmed"},
            status=ToolCallStatus.SUCCESS,
            duration_ms=500,
        )

        # Complete trace
        completed = await memory_client.reasoning.complete_trace(
            trace.id,
            outcome="Successfully booked reservation at La Trattoria for 7pm",
            success=True,
        )

        # Verify complete trace
        assert completed.success is True
        assert "La Trattoria" in completed.outcome
        assert completed.completed_at is not None

        # Retrieve full trace and verify structure
        full_trace = await memory_client.reasoning.get_trace(trace.id)
        assert full_trace is not None
        assert len(full_trace.steps) >= 3


@pytest.mark.integration
class TestReasoningMemoryEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_trace_with_empty_task(self, memory_client, session_id):
        """Test creating trace with empty task."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="",
            generate_embedding=False,
        )

        assert trace is not None
        assert trace.task == ""

    @pytest.mark.asyncio
    async def test_step_with_long_thought(self, memory_client, session_id):
        """Test step with very long thought."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Test task",
            generate_embedding=False,
        )

        long_thought = "I am thinking... " * 500  # ~8KB

        step = await memory_client.reasoning.add_step(
            trace.id,
            thought=long_thought,
            action="think",
            generate_embedding=False,
        )

        assert len(step.thought) == len(long_thought)

    @pytest.mark.asyncio
    async def test_tool_call_with_large_result(self, memory_client, session_id):
        """Test tool call with large result data."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Test task",
            generate_embedding=False,
        )
        step = await memory_client.reasoning.add_step(
            trace.id,
            thought="Large result test",
            action="big_query",
            generate_embedding=False,
        )

        large_result = {"items": [{"id": i, "data": "x" * 100} for i in range(100)]}

        tool_call = await memory_client.reasoning.record_tool_call(
            step.id,
            tool_name="big_query_api",
            arguments={},
            result=large_result,
            status=ToolCallStatus.SUCCESS,
        )

        assert len(tool_call.result["items"]) == 100

    @pytest.mark.asyncio
    async def test_concurrent_trace_operations(self, memory_client, session_id):
        """Test concurrent operations on traces."""
        import asyncio

        # Create multiple traces concurrently
        async def create_trace(index):
            trace = await memory_client.reasoning.start_trace(
                session_id,
                task=f"Concurrent task {index}",
                generate_embedding=False,
            )
            step = await memory_client.reasoning.add_step(
                trace.id,
                thought=f"Step for task {index}",
                action=f"action_{index}",
                generate_embedding=False,
            )
            await memory_client.reasoning.complete_trace(
                trace.id,
                outcome=f"Completed task {index}",
                success=True,
            )
            return trace.id

        tasks = [create_trace(i) for i in range(10)]
        trace_ids = await asyncio.gather(*tasks)

        assert len(trace_ids) == 10
        assert len(set(trace_ids)) == 10  # All unique

    @pytest.mark.asyncio
    async def test_get_nonexistent_trace(self, memory_client):
        """Test retrieving a non-existent trace."""
        trace = await memory_client.reasoning.get_trace("nonexistent-trace-id-12345")

        assert trace is None

    @pytest.mark.asyncio
    async def test_tool_call_statuses(self, memory_client, session_id):
        """Test all tool call status values."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            task="Status test",
            generate_embedding=False,
        )
        step = await memory_client.reasoning.add_step(
            trace.id,
            thought="Testing statuses",
            action="status_test",
            generate_embedding=False,
        )

        statuses = [ToolCallStatus.SUCCESS, ToolCallStatus.ERROR, ToolCallStatus.TIMEOUT]

        for status in statuses:
            call = await memory_client.reasoning.record_tool_call(
                step.id,
                tool_name=f"tool_{status.value}",
                arguments={},
                status=status,
            )
            assert call.status == status


@pytest.mark.integration
class TestReasoningMemoryMessageLinking:
    """Test linking reasoning memory to short-term memory messages."""

    @pytest.mark.asyncio
    async def test_tool_call_with_message_link(self, memory_client, session_id):
        """Tool call can be linked to triggering message."""
        from neo4j_agent_memory.memory.short_term import MessageRole

        # Create a message
        msg = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Search for restaurants",
            extract_entities=False,
            generate_embedding=False,
        )

        # Create trace and step
        trace = await memory_client.reasoning.start_trace(
            session_id,
            "Find restaurants",
            generate_embedding=False,
        )
        step = await memory_client.reasoning.add_step(
            trace.id,
            thought="Searching",
            action="search",
            generate_embedding=False,
        )

        # Record tool call with message link
        tool_call = await memory_client.reasoning.record_tool_call(
            step.id,
            tool_name="restaurant_search",
            arguments={"query": "Italian"},
            result={"count": 5},
            status=ToolCallStatus.SUCCESS,
            message_id=msg.id,
        )

        # Verify relationship
        result = await memory_client._client.execute_read(
            """
            MATCH (tc:ToolCall {id: $tc_id})-[:TRIGGERED_BY]->(m:Message {id: $msg_id})
            RETURN count(*) AS count
            """,
            {"tc_id": str(tool_call.id), "msg_id": str(msg.id)},
        )
        assert result[0]["count"] == 1

    @pytest.mark.asyncio
    async def test_tool_call_without_message_link(self, memory_client, session_id):
        """Tool call without message link should not create relationship."""
        # Create trace and step
        trace = await memory_client.reasoning.start_trace(
            session_id,
            "Background task",
            generate_embedding=False,
        )
        step = await memory_client.reasoning.add_step(
            trace.id,
            thought="Processing",
            action="process",
            generate_embedding=False,
        )

        # Record tool call without message link
        tool_call = await memory_client.reasoning.record_tool_call(
            step.id,
            tool_name="background_tool",
            arguments={},
            status=ToolCallStatus.SUCCESS,
        )

        # Verify no TRIGGERED_BY relationship exists
        result = await memory_client._client.execute_read(
            """
            MATCH (tc:ToolCall {id: $tc_id})-[:TRIGGERED_BY]->()
            RETURN count(*) AS count
            """,
            {"tc_id": str(tool_call.id)},
        )
        assert result[0]["count"] == 0

    @pytest.mark.asyncio
    async def test_trace_initiated_by_message(self, memory_client, session_id):
        """Trace can be linked to initiating message."""
        from neo4j_agent_memory.memory.short_term import MessageRole

        msg = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Help me plan a trip",
            extract_entities=False,
            generate_embedding=False,
        )

        trace = await memory_client.reasoning.start_trace(
            session_id,
            "Plan trip to Paris",
            generate_embedding=False,
            triggered_by_message_id=msg.id,
        )

        # Verify relationship
        result = await memory_client._client.execute_read(
            """
            MATCH (rt:ReasoningTrace {id: $trace_id})-[:INITIATED_BY]->(m:Message {id: $msg_id})
            RETURN count(*) AS count
            """,
            {"trace_id": str(trace.id), "msg_id": str(msg.id)},
        )
        assert result[0]["count"] == 1

    @pytest.mark.asyncio
    async def test_trace_without_message_link(self, memory_client, session_id):
        """Trace without message link should not create relationship."""
        trace = await memory_client.reasoning.start_trace(
            session_id,
            "Standalone task",
            generate_embedding=False,
        )

        # Verify no INITIATED_BY relationship exists
        result = await memory_client._client.execute_read(
            """
            MATCH (rt:ReasoningTrace {id: $trace_id})-[:INITIATED_BY]->()
            RETURN count(*) AS count
            """,
            {"trace_id": str(trace.id)},
        )
        assert result[0]["count"] == 0

    @pytest.mark.asyncio
    async def test_link_trace_to_message_post_hoc(self, memory_client, session_id):
        """Test linking trace to message after creation."""
        from neo4j_agent_memory.memory.short_term import MessageRole

        # Create message
        msg = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Process this request",
            extract_entities=False,
            generate_embedding=False,
        )

        # Create trace without initial link
        trace = await memory_client.reasoning.start_trace(
            session_id,
            "Process request",
            generate_embedding=False,
        )

        # Verify no relationship initially
        result = await memory_client._client.execute_read(
            """
            MATCH (rt:ReasoningTrace {id: $trace_id})-[:INITIATED_BY]->()
            RETURN count(*) AS count
            """,
            {"trace_id": str(trace.id)},
        )
        assert result[0]["count"] == 0

        # Link after the fact
        await memory_client.reasoning.link_trace_to_message(trace.id, msg.id)

        # Verify relationship now exists
        result = await memory_client._client.execute_read(
            """
            MATCH (rt:ReasoningTrace {id: $trace_id})-[:INITIATED_BY]->(m:Message {id: $msg_id})
            RETURN count(*) AS count
            """,
            {"trace_id": str(trace.id), "msg_id": str(msg.id)},
        )
        assert result[0]["count"] == 1

    @pytest.mark.asyncio
    async def test_full_conversation_trace_linking(self, memory_client, session_id):
        """Test complete flow linking conversation to trace and tool calls."""
        from neo4j_agent_memory.memory.short_term import MessageRole

        # User message triggers processing
        user_msg = await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "What's the weather in New York?",
            extract_entities=False,
            generate_embedding=False,
        )

        # Create trace linked to user message
        trace = await memory_client.reasoning.start_trace(
            session_id,
            "Get weather information",
            generate_embedding=False,
            triggered_by_message_id=user_msg.id,
        )

        # Add step and tool call
        step = await memory_client.reasoning.add_step(
            trace.id,
            thought="Need to call weather API",
            action="get_weather",
            generate_embedding=False,
        )

        tool_call = await memory_client.reasoning.record_tool_call(
            step.id,
            tool_name="weather_api",
            arguments={"city": "New York"},
            result={"temperature": 72, "conditions": "sunny"},
            status=ToolCallStatus.SUCCESS,
            message_id=user_msg.id,
        )

        # Complete trace
        await memory_client.reasoning.complete_trace(
            trace.id,
            outcome="Weather retrieved successfully",
            success=True,
        )

        # Add assistant response
        assistant_msg = await memory_client.short_term.add_message(
            session_id,
            MessageRole.ASSISTANT,
            "The weather in New York is 72°F and sunny!",
            extract_entities=False,
            generate_embedding=False,
        )

        # Verify the full graph structure
        # User message -> NEXT_MESSAGE -> Assistant message
        result = await memory_client._client.execute_read(
            """
            MATCH (m1:Message {id: $user_id})-[:NEXT_MESSAGE]->(m2:Message {id: $assistant_id})
            RETURN count(*) AS count
            """,
            {"user_id": str(user_msg.id), "assistant_id": str(assistant_msg.id)},
        )
        assert result[0]["count"] == 1

        # Trace -[:INITIATED_BY]-> User message
        result = await memory_client._client.execute_read(
            """
            MATCH (rt:ReasoningTrace {id: $trace_id})-[:INITIATED_BY]->(m:Message {id: $msg_id})
            RETURN count(*) AS count
            """,
            {"trace_id": str(trace.id), "msg_id": str(user_msg.id)},
        )
        assert result[0]["count"] == 1

        # ToolCall -[:TRIGGERED_BY]-> User message
        result = await memory_client._client.execute_read(
            """
            MATCH (tc:ToolCall {id: $tc_id})-[:TRIGGERED_BY]->(m:Message {id: $msg_id})
            RETURN count(*) AS count
            """,
            {"tc_id": str(tool_call.id), "msg_id": str(user_msg.id)},
        )
        assert result[0]["count"] == 1
