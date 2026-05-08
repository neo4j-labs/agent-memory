"""Integration tests for v0.4 P0.3 (User) and P0.4 (Preference relationships)."""

from __future__ import annotations

from datetime import datetime

import pytest

from neo4j_agent_memory.schema.models import EntityRef


@pytest.mark.integration
@pytest.mark.asyncio
class TestUserMemory:
    async def test_upsert_get_list(self, clean_memory_client):
        client = clean_memory_client
        u1 = await client.users.upsert_user(
            identifier="sara@omg.com", attributes={"role": "manager"}
        )
        assert u1.identifier == "sara@omg.com"
        assert u1.attributes == {"role": "manager"}

        # Idempotent
        u2 = await client.users.upsert_user(
            identifier="sara@omg.com", attributes={"role": "director"}
        )
        assert u2.id == u1.id
        assert u2.attributes == {"role": "director"}

        fetched = await client.users.get_user("sara@omg.com")
        assert fetched is not None
        assert fetched.identifier == "sara@omg.com"

        await client.users.upsert_user(identifier="liam@omg.com")
        users = await client.users.list_users()
        assert {u.identifier for u in users} == {"sara@omg.com", "liam@omg.com"}

    async def test_get_user_returns_none_for_unknown(self, clean_memory_client):
        client = clean_memory_client
        assert await client.users.get_user("nobody@nowhere.com") is None


@pytest.mark.integration
@pytest.mark.asyncio
class TestPreferenceRelationships:
    async def test_add_preference_with_user_and_applies_to(self, clean_memory_client):
        client = clean_memory_client
        await client.users.upsert_user(identifier="sara@omg.com")

        pref = await client.long_term.add_preference(
            "consultants",
            "Prefer senior consultants on healthcare clients",
            user_identifier="sara@omg.com",
            applies_to=[EntityRef(name="Healthcare", type="Industry")],
        )

        rows = await client.graph.execute_read(
            """
            MATCH (u:User {identifier: 'sara@omg.com'})-[:HAS_PREFERENCE]->(p:Preference {id: $id})
                  -[:APPLIES_TO]->(e:Entity)
            RETURN e.name AS name, e.type AS type, p.valid_from AS vf
            """,
            {"id": str(pref.id)},
        )
        assert len(rows) == 1
        assert rows[0]["name"] == "Healthcare"
        assert rows[0]["type"] == "Industry"
        assert rows[0]["vf"] is not None  # valid_from set on creation

    async def test_supersede_preference_writes_edge_and_valid_until(self, clean_memory_client):
        client = clean_memory_client
        await client.users.upsert_user(identifier="sara@omg.com")

        old_pref = await client.long_term.add_preference(
            "consultants", "Prefer junior consultants", user_identifier="sara@omg.com"
        )
        new_pref = await client.long_term.add_preference(
            "consultants", "Prefer senior consultants", user_identifier="sara@omg.com"
        )

        await client.long_term.supersede_preference(old_pref.id, new_pref.id)

        rows = await client.graph.execute_read(
            """
            MATCH (old:Preference {id: $old})-[:SUPERSEDED_BY]->(new:Preference {id: $new})
            RETURN old.valid_until AS valid_until
            """,
            {"old": str(old_pref.id), "new": str(new_pref.id)},
        )
        assert len(rows) == 1
        assert rows[0]["valid_until"] is not None

    async def test_get_preferences_for_active_only(self, clean_memory_client):
        client = clean_memory_client
        await client.users.upsert_user(identifier="sara@omg.com")

        old_pref = await client.long_term.add_preference(
            "consultants", "Prefer junior", user_identifier="sara@omg.com"
        )
        new_pref = await client.long_term.add_preference(
            "consultants", "Prefer senior", user_identifier="sara@omg.com"
        )
        await client.long_term.supersede_preference(old_pref.id, new_pref.id)

        active = await client.long_term.get_preferences_for("sara@omg.com", active_only=True)
        active_ids = {str(p.id) for p in active}
        assert str(new_pref.id) in active_ids
        assert str(old_pref.id) not in active_ids

        all_prefs = await client.long_term.get_preferences_for("sara@omg.com", active_only=False)
        all_ids = {str(p.id) for p in all_prefs}
        assert str(old_pref.id) in all_ids
        assert str(new_pref.id) in all_ids

    async def test_get_preferences_for_scoped_by_applies_to(self, clean_memory_client):
        client = clean_memory_client
        await client.users.upsert_user(identifier="sara@omg.com")

        await client.long_term.add_preference(
            "consultants",
            "Senior on healthcare",
            user_identifier="sara@omg.com",
            applies_to=[EntityRef(name="Healthcare", type="Industry")],
        )
        await client.long_term.add_preference(
            "consultants",
            "Junior on retail",
            user_identifier="sara@omg.com",
            applies_to=[EntityRef(name="Retail", type="Industry")],
        )

        prefs = await client.long_term.get_preferences_for(
            "sara@omg.com",
            applies_to=EntityRef(name="Healthcare", type="Industry"),
        )
        assert len(prefs) == 1
        assert "healthcare" in prefs[0].preference.lower()

    async def test_as_of_returns_pre_supersede_snapshot(self, clean_memory_client):
        """Bi-temporal: ``as_of`` returns the preference active at that instant.

        Timeline:
            t0: add old_pref          (old.valid_from = t0)
            as_of: snapshot before supersede (well before t1)
            t1: supersede(old, new)   (old.valid_until = t1)

        At ``as_of`` (between t0 and t1), the old preference is "active":
        valid_from <= as_of < valid_until.
        """
        import asyncio

        client = clean_memory_client
        await client.users.upsert_user(identifier="sara@omg.com")

        old_pref = await client.long_term.add_preference(
            "consultants", "Prefer junior", user_identifier="sara@omg.com"
        )

        # Sleep so the wall clock is comfortably past ``old_pref.valid_from``
        # before we snapshot.
        await asyncio.sleep(0.05)
        as_of = datetime.utcnow()
        await asyncio.sleep(0.05)

        new_pref = await client.long_term.add_preference(
            "consultants", "Prefer senior", user_identifier="sara@omg.com"
        )
        await client.long_term.supersede_preference(old_pref.id, new_pref.id)

        # ``active_only=False`` because at ``as_of`` the old pref had not
        # yet been superseded — but :SUPERSEDED_BY is a topology fact, not
        # a temporal one, so we must opt out of the supersede filter.
        prefs = await client.long_term.get_preferences_for(
            "sara@omg.com", active_only=False, as_of=as_of
        )
        ids = {str(p.id) for p in prefs}
        assert str(old_pref.id) in ids
