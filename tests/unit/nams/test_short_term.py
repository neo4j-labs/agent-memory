"""Tests for nams/short_term.py — NamsShortTermMemory.

Endpoint shapes verified against the live NAMS OpenAPI spec.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.core.protocols import ShortTermProtocol
from neo4j_agent_memory.memory.short_term import (
    Conversation,
    Message,
    MessageRole,
)
from neo4j_agent_memory.nams import HttpTransport, NamsShortTermMemory, StaticApiKeyAuth


@pytest.fixture
async def transport(nams_config):
    auth = StaticApiKeyAuth.from_config(nams_config)
    t = HttpTransport.from_config(nams_config, auth=auth)
    async with t:
        yield t


@pytest.fixture
def short_term(transport) -> NamsShortTermMemory:
    return NamsShortTermMemory(transport)


CONV = "00000000-0000-0000-0000-0000000c1d00"

# NAMS uses camelCase end-to-end; createdAt is absent on POST responses.
SAMPLE_MESSAGE = {
    "id": "00000000-0000-0000-0000-000000000001",
    "conversationId": "00000000-0000-0000-0000-000000000aaa",
    "content": "hi",
    "role": "user",
}

SAMPLE_MESSAGE_WITH_TIMESTAMP = {
    **SAMPLE_MESSAGE,
    "createdAt": "2026-05-17T12:00:00Z",
}

SAMPLE_CONVERSATION = {
    "id": "00000000-0000-0000-0000-000000000aaa",
    "userId": "alice",
    "workspaceId": "ws-1",
    "createdAt": "2026-05-17T11:00:00Z",
    "updatedAt": "2026-05-17T11:30:00Z",
}


class TestConversationIdAlias:
    """A.3 — conversation_id accepted as an alias for session_id (NAMS)."""

    @respx.mock
    async def test_add_message_via_conversation_id_alias(self, short_term):
        route = respx.post("https://memory.test/v1/conversations/conv-9/messages").respond(
            200, json=SAMPLE_MESSAGE
        )
        await short_term.add_message(conversation_id="conv-9", role="user", content="hi")
        assert route.called

    @respx.mock
    async def test_session_id_wins_when_both_supplied(self, short_term):
        # Route only matches the session_id path; if conversation_id were used
        # instead, the request would 404 against an unmocked path.
        route = respx.post("https://memory.test/v1/conversations/real-sess/messages").respond(
            200, json=SAMPLE_MESSAGE
        )
        await short_term.add_message(
            session_id="real-sess", conversation_id="BOGUS", role="user", content="hi"
        )
        assert route.called

    async def test_add_message_neither_raises_type_error(self, short_term):
        with pytest.raises(TypeError, match="session_id.*conversation_id|conversation_id"):
            await short_term.add_message(role="user", content="hi")

    async def test_search_messages_neither_raises_value_error(self, short_term):
        with pytest.raises(ValueError, match="session_id"):
            await short_term.search_messages("q")

    @respx.mock
    async def test_search_messages_via_conversation_id_alias(self, short_term):
        route = respx.post("https://memory.test/v1/conversations/conv-7/search").respond(
            200, json={"messages": [], "searchType": "vector"}
        )
        await short_term.search_messages("q", conversation_id="conv-7")
        assert route.called


class TestProtocolConformance:
    def test_satisfies_short_term_protocol(self, short_term):
        assert isinstance(short_term, ShortTermProtocol)


class TestAddMessage:
    @respx.mock
    async def test_basic(self, short_term):
        route = respx.post(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00/messages"
        ).respond(201, json=SAMPLE_MESSAGE)
        msg = await short_term.add_message("00000000-0000-0000-0000-0000000c1d00", "user", "hi")
        assert isinstance(msg, Message)
        assert msg.role == MessageRole.USER
        assert msg.content == "hi"
        body = json.loads(route.calls[0].request.content)
        assert body == {"content": "hi", "role": "user"}

    @respx.mock
    async def test_bolt_only_kwargs_dropped(self, short_term):
        route = respx.post(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00/messages"
        ).respond(201, json=SAMPLE_MESSAGE)
        await short_term.add_message(
            "00000000-0000-0000-0000-0000000c1d00",
            "user",
            "hi",
            metadata={"src": "x"},
            user_identifier="alice",
            extract_entities=True,
        )
        body = json.loads(route.calls[0].request.content)
        # NAMS spec accepts only content + role.
        assert body == {"content": "hi", "role": "user"}


class TestGetConversation:
    """get_conversation does 2 HTTP calls: header + messages."""

    @respx.mock
    async def test_assembles_header_and_messages(self, short_term):
        respx.get(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00"
        ).respond(200, json=SAMPLE_CONVERSATION)
        respx.get(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00/messages"
        ).respond(200, json={"messages": [SAMPLE_MESSAGE_WITH_TIMESTAMP]})

        conv = await short_term.get_conversation("00000000-0000-0000-0000-0000000c1d00")
        assert isinstance(conv, Conversation)
        assert len(conv.messages) == 1
        assert conv.messages[0].content == "hi"

    @respx.mock
    async def test_with_limit(self, short_term):
        respx.get(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00"
        ).respond(200, json=SAMPLE_CONVERSATION)
        route = respx.get(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00/messages"
        ).respond(200, json={"messages": []})
        await short_term.get_conversation("00000000-0000-0000-0000-0000000c1d00", limit=20)
        assert route.calls[0].request.url.params["limit"] == "20"

    @respx.mock
    async def test_returns_insertion_order_and_asks_for_the_cap(self, short_term):
        """The service lists newest first (default 50, max 200); the Protocol is
        insertion order. TCK SPEC 2.7 (test_multiple_messages_preserve_order).
        """
        respx.get(f"https://memory.test/v1/conversations/{CONV}").respond(
            200, json=SAMPLE_CONVERSATION
        )
        newest_first = [
            {
                **SAMPLE_MESSAGE_WITH_TIMESTAMP,
                "id": f"00000000-0000-0000-0000-{i:012d}",
                "content": f"Message {i}",
            }
            for i in reversed(range(5))
        ]
        route = respx.get(f"https://memory.test/v1/conversations/{CONV}/messages").respond(
            200, json={"messages": newest_first}
        )

        conv = await short_term.get_conversation(CONV)

        assert [m.content for m in conv.messages] == [f"Message {i}" for i in range(5)]
        assert route.calls[0].request.url.params["limit"] == "200"

    @respx.mock
    async def test_a_limit_above_the_cap_is_clamped(self, short_term, caplog):
        respx.get(f"https://memory.test/v1/conversations/{CONV}").respond(
            200, json=SAMPLE_CONVERSATION
        )
        route = respx.get(f"https://memory.test/v1/conversations/{CONV}/messages").respond(
            200, json={"messages": []}
        )

        with caplog.at_level("WARNING", logger="neo4j_agent_memory.nams.short_term"):
            await short_term.get_conversation(CONV, limit=1000)

        assert route.calls[0].request.url.params["limit"] == "200"
        assert "newest 200 messages" in caplog.text

    @respx.mock
    async def test_the_bridge_keeps_its_order_and_limit(self, bridge_config):
        """The TCK bridge returns insertion order and has no 200 cap."""
        auth = StaticApiKeyAuth.from_config(bridge_config)
        respx.post("https://memory.test/get_conversation").respond(200, json=SAMPLE_CONVERSATION)
        messages = respx.post("https://memory.test/list_messages").respond(
            200,
            json={
                "messages": [
                    {
                        **SAMPLE_MESSAGE_WITH_TIMESTAMP,
                        "id": f"00000000-0000-0000-0000-{i:012d}",
                        "content": f"Message {i}",
                    }
                    for i in range(3)
                ]
            },
        )
        async with HttpTransport.from_config(bridge_config, auth=auth) as t:
            conv = await NamsShortTermMemory(t).get_conversation(CONV)

        assert [m.content for m in conv.messages] == ["Message 0", "Message 1", "Message 2"]
        assert "limit" not in json.loads(messages.calls[0].request.content or b"{}")

    @respx.mock
    async def test_handles_bare_list_messages_response(self, short_term):
        respx.get(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00"
        ).respond(200, json=SAMPLE_CONVERSATION)
        respx.get(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00/messages"
        ).respond(200, json=[SAMPLE_MESSAGE_WITH_TIMESTAMP])
        conv = await short_term.get_conversation("00000000-0000-0000-0000-0000000c1d00")
        assert len(conv.messages) == 1


class TestSearchMessages:
    @respx.mock
    async def test_scoped_to_conversation(self, short_term):
        route = respx.post(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00/search"
        ).respond(200, json={"messages": [SAMPLE_MESSAGE_WITH_TIMESTAMP], "searchType": "vector"})
        msgs = await short_term.search_messages(
            "hello", session_id="00000000-0000-0000-0000-0000000c1d00", limit=5
        )
        assert len(msgs) == 1
        body = json.loads(route.calls[0].request.content)
        assert body == {"query": "hello", "limit": 5}

    async def test_requires_session_id(self, short_term):
        with pytest.raises(ValueError, match="session_id"):
            await short_term.search_messages("hello")

    @respx.mock
    async def test_empty_results(self, short_term):
        respx.post(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00/search"
        ).respond(200, json={"messages": [], "searchType": "vector"})
        msgs = await short_term.search_messages(
            "nothing", session_id="00000000-0000-0000-0000-0000000c1d00"
        )
        assert msgs == []


class TestListSessions:
    async def test_raises_not_supported(self, short_term):
        with pytest.raises(NotSupportedError) as exc_info:
            await short_term.list_sessions(limit=50)
        assert "list_sessions" in exc_info.value.method


class TestDeleteMessage:
    async def test_raises_not_supported(self, short_term):
        with pytest.raises(NotSupportedError):
            await short_term.delete_message("msg-id")


class TestClearSession:
    @respx.mock
    async def test_basic(self, short_term):
        route = respx.delete(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00"
        ).respond(204)
        result = await short_term.clear_session("00000000-0000-0000-0000-0000000c1d00")
        assert result is None
        assert route.called


class TestGetContext:
    @respx.mock
    async def test_assembles_three_tier(self, short_term):
        respx.get(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00/context"
        ).respond(
            200,
            json={
                "reflections": [{"content": "user prefers home cooking"}],
                "observations": [{"content": "loves italian"}],
                "recentMessages": [{"role": "user", "content": "hi"}],
            },
        )
        ctx = await short_term.get_context(
            "query", session_id="00000000-0000-0000-0000-0000000c1d00"
        )
        assert "Reflections" in ctx
        assert "user prefers home cooking" in ctx
        assert "Observations" in ctx
        assert "Recent Messages" in ctx

    async def test_requires_session_id(self, short_term):
        with pytest.raises(ValueError, match="session_id"):
            await short_term.get_context("query")


class TestGetConversationSummary:
    async def test_raises_not_supported(self, short_term):
        with pytest.raises(NotSupportedError):
            await short_term.get_conversation_summary("00000000-0000-0000-0000-0000000c1d00")


class TestCreateConversation:
    @respx.mock
    async def test_basic(self, short_term):
        route = respx.post("https://memory.test/v1/conversations").respond(
            201,
            json={
                "id": "00000000-0000-0000-0000-0000000c1d00",
                "userId": "alice",
                "workspaceId": "ws",
            },
        )
        conv = await short_term.create_conversation("my-session", user_identifier="alice")
        assert isinstance(conv, Conversation)
        body = json.loads(route.calls[0].request.content)
        # NAMS accepts only {userId, metadata}.
        assert body == {"userId": "alice"}

    @respx.mock
    async def test_ignores_title_kwarg(self, short_term):
        """title is bolt-side concept — NAMS spec does not accept it."""
        route = respx.post("https://memory.test/v1/conversations").respond(
            201,
            json={
                "id": "00000000-0000-0000-0000-0000000c1d00",
                "userId": "alice",
                "workspaceId": "ws",
            },
        )
        await short_term.create_conversation("session", title="ignored", user_identifier="alice")
        body = json.loads(route.calls[0].request.content)
        assert "title" not in body


class TestListConversations:
    @respx.mock
    async def test_with_envelope(self, short_term):
        respx.get("https://memory.test/v1/conversations").respond(
            200, json={"conversations": [SAMPLE_CONVERSATION]}
        )
        convs = await short_term.list_conversations()
        assert len(convs) == 1

    @respx.mock
    async def test_with_bare_list(self, short_term):
        respx.get("https://memory.test/v1/conversations").respond(200, json=[SAMPLE_CONVERSATION])
        convs = await short_term.list_conversations()
        assert len(convs) == 1

    @staticmethod
    def _conversations(start: int, count: int) -> list[dict]:
        return [
            {**SAMPLE_CONVERSATION, "id": f"00000000-0000-0000-0000-{i:012d}"}
            for i in range(start, start + count)
        ]

    @respx.mock
    async def test_a_limit_above_the_page_cap_follows_next_cursor(self, short_term):
        """The regression: ``limit=1000`` is a 400 ``invalid_limit`` (cap 200).

        The Strands memory store and session manager both list with 1000.
        """
        route = respx.get("https://memory.test/v1/conversations").mock(
            side_effect=[
                httpx.Response(
                    200, json={"conversations": self._conversations(0, 200), "next_cursor": "c1"}
                ),
                httpx.Response(
                    200, json={"conversations": self._conversations(200, 30), "next_cursor": ""}
                ),
            ]
        )

        convs = await short_term.list_conversations(user_identifier="alice", limit=1000)

        assert len(convs) == 230
        first, second = (call.request.url.params for call in route.calls)
        assert dict(first) == {"userId": "alice", "limit": "200"}
        assert dict(second) == {"userId": "alice", "limit": "200", "cursor": "c1"}

    @respx.mock
    async def test_paging_stops_at_the_limit(self, short_term):
        route = respx.get("https://memory.test/v1/conversations").mock(
            side_effect=[
                httpx.Response(
                    200, json={"conversations": self._conversations(0, 200), "next_cursor": "c1"}
                ),
                httpx.Response(
                    200, json={"conversations": self._conversations(200, 50), "next_cursor": "c2"}
                ),
            ]
        )

        convs = await short_term.list_conversations(limit=250)

        assert len(convs) == 250
        assert route.calls[1].request.url.params["limit"] == "50"
        assert route.call_count == 2

    @respx.mock
    async def test_the_default_limit_matches_bolt(self, short_term):
        route = respx.get("https://memory.test/v1/conversations").respond(
            200, json={"conversations": [], "next_cursor": ""}
        )
        await short_term.list_conversations()
        assert route.calls[0].request.url.params["limit"] == "100"


class TestBulkAddMessages:
    @respx.mock
    async def test_basic(self, short_term):
        route = respx.post(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00/messages/bulk"
        ).respond(
            201,
            json={"messages": [SAMPLE_MESSAGE, SAMPLE_MESSAGE]},
        )
        msgs = await short_term.bulk_add_messages(
            "00000000-0000-0000-0000-0000000c1d00",
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        )
        assert len(msgs) == 2
        body = json.loads(route.calls[0].request.content)
        assert body == {
            "messages": [
                {"content": "hi", "role": "user"},
                {"content": "hello", "role": "assistant"},
            ]
        }


class TestGetObservations:
    @respx.mock
    async def test_with_envelope(self, short_term):
        respx.get(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00/observations"
        ).respond(200, json={"observations": [{"content": "user likes Italian food"}]})
        obs = await short_term.get_observations("00000000-0000-0000-0000-0000000c1d00", limit=20)
        assert len(obs) == 1


class TestGetReflections:
    @respx.mock
    async def test_with_envelope(self, short_term):
        respx.get(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00/reflections"
        ).respond(200, json={"reflections": [{"content": "user prefers cooking at home"}]})
        refs = await short_term.get_reflections("00000000-0000-0000-0000-0000000c1d00")
        assert len(refs) == 1


class TestGetExtractionStatus:
    @respx.mock
    async def test_parses_summary_and_completeness(self, short_term):
        respx.get(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00/extraction-status"
        ).respond(
            200,
            json={
                "messages": [{"id": "m1", "status": "completed"}],
                "summary": {"completed": 2, "failed": 1},
            },
        )
        status = await short_term.get_extraction_status("00000000-0000-0000-0000-0000000c1d00")
        assert status.summary == {"completed": 2, "failed": 1}
        assert status.pending_count == 0
        assert status.is_complete is True

    @respx.mock
    async def test_pending_is_not_complete(self, short_term):
        respx.get(
            "https://memory.test/v1/conversations/00000000-0000-0000-0000-0000000c1d00/extraction-status"
        ).respond(200, json={"summary": {"pending": 3, "completed": 1}})
        status = await short_term.get_extraction_status(
            conversation_id="00000000-0000-0000-0000-0000000c1d00"
        )
        assert status.pending_count == 3
        assert status.is_complete is False


class TestBridgeRouting:
    """Bridge protocol = ``POST /<snake_case_method>`` (no path templating)."""

    @respx.mock
    async def test_add_message_bridge_path(self, bridge_config):
        auth = StaticApiKeyAuth.from_config(bridge_config)
        route = respx.post("https://memory.test/add_message").respond(201, json=SAMPLE_MESSAGE)
        async with HttpTransport.from_config(bridge_config, auth=auth) as t:
            st = NamsShortTermMemory(t)
            await st.add_message("00000000-0000-0000-0000-0000000c1d00", "user", "hi")
        assert route.called
