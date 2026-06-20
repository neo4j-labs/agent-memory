"""Unit tests for Pydantic models."""

from datetime import datetime
from uuid import UUID

from neo4j_agent_memory.memory.long_term import Entity, EntityType, Fact, Preference
from neo4j_agent_memory.memory.reasoning import (
    ReasoningStep,
    ReasoningTrace,
    ToolCall,
    ToolCallStatus,
)
from neo4j_agent_memory.memory.short_term import Conversation, Message, MessageRole


class TestMessageModel:
    """Tests for Message model."""

    def test_create_message(self):
        """Test creating a basic message."""
        msg = Message(role=MessageRole.USER, content="Hello, world!")

        assert msg.role == MessageRole.USER
        assert msg.content == "Hello, world!"
        assert isinstance(msg.id, UUID)
        assert isinstance(msg.created_at, datetime)

    def test_message_with_embedding(self):
        """Test message with embedding."""
        embedding = [0.1] * 1536
        msg = Message(
            role=MessageRole.ASSISTANT,
            content="Response",
            embedding=embedding,
        )

        assert msg.embedding == embedding
        assert len(msg.embedding) == 1536

    def test_message_roles(self):
        """Test all message roles."""
        for role in MessageRole:
            msg = Message(role=role, content="test")
            assert msg.role == role


class TestConversationModel:
    """Tests for Conversation model."""

    def test_create_conversation(self):
        """Test creating a conversation."""
        conv = Conversation(session_id="test-session")

        assert conv.session_id == "test-session"
        assert isinstance(conv.id, UUID)
        assert conv.messages == []

    def test_conversation_with_messages(self):
        """Test conversation with messages."""
        msg1 = Message(role=MessageRole.USER, content="Hi")
        msg2 = Message(role=MessageRole.ASSISTANT, content="Hello!")

        conv = Conversation(
            session_id="test-session",
            title="Test Conversation",
            messages=[msg1, msg2],
        )

        assert len(conv.messages) == 2
        assert conv.title == "Test Conversation"


class TestEntityModel:
    """Tests for Entity model."""

    def test_create_entity(self):
        """Test creating an entity."""
        entity = Entity(
            name="John Smith",
            type=EntityType.PERSON,
        )

        assert entity.name == "John Smith"
        assert entity.type == EntityType.PERSON
        assert isinstance(entity.id, UUID)

    def test_entity_display_name(self):
        """Test entity display name property."""
        # Without canonical name
        entity1 = Entity(name="John", type=EntityType.PERSON)
        assert entity1.display_name == "John"

        # With canonical name
        entity2 = Entity(
            name="Jon",
            canonical_name="John Smith",
            type=EntityType.PERSON,
        )
        assert entity2.display_name == "John Smith"

    def test_all_entity_types(self):
        """Test all entity types."""
        for entity_type in EntityType:
            entity = Entity(name="Test", type=entity_type)
            assert entity.type == entity_type


class TestPreferenceModel:
    """Tests for Preference model."""

    def test_create_preference(self):
        """Test creating a preference."""
        pref = Preference(
            category="food",
            preference="I love Italian cuisine",
        )

        assert pref.category == "food"
        assert pref.preference == "I love Italian cuisine"
        assert pref.confidence == 1.0

    def test_preference_with_context(self):
        """Test preference with context."""
        pref = Preference(
            category="music",
            preference="Prefer classical music",
            context="When working",
        )

        assert pref.context == "When working"


class TestFactModel:
    """Tests for Fact model."""

    def test_create_fact(self):
        """Test creating a fact."""
        fact = Fact(
            subject="John",
            predicate="works_at",
            object="Acme Corp",
        )

        assert fact.subject == "John"
        assert fact.predicate == "works_at"
        assert fact.object == "Acme Corp"

    def test_fact_as_triple(self):
        """Test fact as_triple property."""
        fact = Fact(
            subject="Alice",
            predicate="knows",
            object="Bob",
        )

        assert fact.as_triple == ("Alice", "knows", "Bob")

    def test_fact_with_validity(self):
        """Test fact with temporal validity."""
        now = datetime.utcnow()
        fact = Fact(
            subject="John",
            predicate="lives_in",
            object="New York",
            valid_from=now,
        )

        assert fact.valid_from == now
        assert fact.valid_until is None


class TestReasoningTraceModel:
    """Tests for ReasoningTrace model."""

    def test_create_trace(self):
        """Test creating a reasoning trace."""
        trace = ReasoningTrace(
            session_id="test-session",
            task="Find a restaurant",
        )

        assert trace.session_id == "test-session"
        assert trace.task == "Find a restaurant"
        assert trace.steps == []
        assert trace.success is None

    def test_trace_with_steps(self):
        """Test trace with reasoning steps."""
        trace_id = UUID("12345678-1234-5678-1234-567812345678")
        step = ReasoningStep(
            trace_id=trace_id,
            step_number=1,
            thought="I need to search for restaurants",
            action="search_restaurants",
        )

        trace = ReasoningTrace(
            id=trace_id,
            session_id="test-session",
            task="Find a restaurant",
            steps=[step],
        )

        assert len(trace.steps) == 1
        assert trace.steps[0].thought == "I need to search for restaurants"


class TestToolCallModel:
    """Tests for ToolCall model."""

    def test_create_tool_call(self):
        """Test creating a tool call."""
        tool_call = ToolCall(
            tool_name="search_api",
            arguments={"query": "restaurants"},
            status=ToolCallStatus.SUCCESS,
        )

        assert tool_call.tool_name == "search_api"
        assert tool_call.arguments == {"query": "restaurants"}
        assert tool_call.status == ToolCallStatus.SUCCESS

    def test_tool_call_with_result(self):
        """Test tool call with result."""
        tool_call = ToolCall(
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
            result=4,
            status=ToolCallStatus.SUCCESS,
            duration_ms=10,
        )

        assert tool_call.result == 4
        assert tool_call.duration_ms == 10

    def test_tool_call_with_error(self):
        """Test tool call with error."""
        tool_call = ToolCall(
            tool_name="api_call",
            arguments={"url": "https://example.com"},
            status=ToolCallStatus.FAILURE,
            error="Connection timeout",
        )

        assert tool_call.status == ToolCallStatus.FAILURE
        assert tool_call.error == "Connection timeout"

    def test_tool_call_accepts_completed_status(self):
        """The hosted NAMS service returns status='completed' on success;
        the SDK must parse it without raising a ValidationError (issue #128).
        """
        tool_call = ToolCall(
            tool_name="search_api",
            arguments={"query": "restaurants"},
            status=ToolCallStatus.COMPLETED,
        )

        assert tool_call.status == ToolCallStatus.COMPLETED
        assert tool_call.status == "completed"
