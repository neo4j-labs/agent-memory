"""Memory consolidation primitives — dry-runnable hygiene jobs.

Three primitives, all idempotent and dry-runnable:

* :meth:`dedupe_entities` — find pairs of entities with high embedding
  similarity and either flag them with ``:SAME_AS`` or report them.
* :meth:`summarize_long_traces` — find traces with many steps that
  haven't been summarized yet.
* :meth:`detect_superseded_preferences` — find preferences with
  near-duplicate content suggesting one supersedes the other.

Each primitive:

* Defaults to ``dry_run=True`` — no mutations.
* Returns a :class:`ConsolidationReport` with per-candidate breakdown.
* Writes a ``(:ConsolidationRun {id, kind, started_at, completed_at, stats_json})``
  audit node when ``dry_run=False`` so consecutive runs can pick up where
  the last left off.

These build on existing primitives (the ``:SAME_AS`` edge written by
deduplication, the ``valid_from`` / ``valid_until`` properties on
:class:`Preference`) — they're orchestration over already-shipped infra.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from neo4j_agent_memory.schema.models import (
    ConsolidationCandidate,
    ConsolidationReport,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.graph.client import Neo4jClient


class ConsolidationMemory:
    """``client.consolidation`` — hygiene jobs for memory upkeep."""

    def __init__(self, client: Neo4jClient):
        self._client = client

    async def dedupe_entities(
        self,
        *,
        similarity_threshold: float = 0.95,
        max_pairs: int = 10_000,
        dry_run: bool = True,
    ) -> ConsolidationReport:
        """Find entity pairs with high embedding similarity and merge them.

        Pairs already linked via ``:SAME_AS`` are skipped. When
        ``dry_run=False``, each pair gets a ``[:SAME_AS]`` edge with
        ``status='auto_consolidated'`` and the run is recorded as a
        ``:ConsolidationRun`` audit node.

        Args:
            similarity_threshold: Cosine similarity floor (0-1). Pairs
                above this are candidates for merging.
            max_pairs: Cap on the number of candidate pairs to return.
            dry_run: When True, no mutations.
        """
        # Find candidate pairs via vector similarity. We compare each
        # entity to its top-k nearest neighbors of the same type, then
        # filter to pairs above the threshold and not already merged.
        candidates_rows = await self._client.execute_read(
            """
            MATCH (e1:Entity)
            WHERE e1.embedding IS NOT NULL
            CALL {
                WITH e1
                CALL db.index.vector.queryNodes(
                    'entity_embedding_idx', 5, e1.embedding
                ) YIELD node AS e2, score
                WHERE e2 <> e1
                  AND e2.type = e1.type
                  AND score >= $threshold
                  AND NOT (e1)-[:SAME_AS]-(e2)
                  AND e1.id < e2.id
                RETURN e2, score
            }
            RETURN e1.id AS a_id, e1.name AS a_name,
                   e2.id AS b_id, e2.name AS b_name,
                   score
            ORDER BY score DESC
            LIMIT $max_pairs
            """,
            {"threshold": similarity_threshold, "max_pairs": max_pairs},
        )

        candidates: list[ConsolidationCandidate] = []
        for row in candidates_rows:
            candidates.append(
                ConsolidationCandidate(
                    kind="dedupe_entity_pair",
                    description=(
                        f"{row['a_name']!r} ~= {row['b_name']!r} (score {row['score']:.3f})"
                    ),
                    payload={
                        "a_id": row["a_id"],
                        "b_id": row["b_id"],
                        "score": row["score"],
                    },
                )
            )

        run_id: str | None = None
        if not dry_run and candidates:
            for cand in candidates:
                payload = cand.payload
                await self._client.execute_write(
                    """
                    MATCH (a:Entity {id: $a_id})
                    MATCH (b:Entity {id: $b_id})
                    MERGE (a)-[r:SAME_AS]->(b)
                    ON CREATE SET r.confidence = $score,
                                  r.match_type = 'embedding',
                                  r.status = 'auto_consolidated',
                                  r.created_at = datetime()
                    """,
                    payload,
                )
                cand.action_taken = True
            run_id = await self._record_run(
                "dedupe_entities",
                stats={
                    "candidates": len(candidates),
                    "applied": sum(1 for c in candidates if c.action_taken),
                    "threshold": similarity_threshold,
                },
            )

        return ConsolidationReport(
            kind="dedupe_entities",
            dry_run=dry_run,
            candidates=candidates,
            run_id=run_id,
        )

    async def summarize_long_traces(
        self,
        *,
        min_steps: int = 20,
        max_traces: int = 1_000,
        dry_run: bool = True,
    ) -> ConsolidationReport:
        """Identify reasoning traces with many steps that lack a summary.

        v0.5 ships the *identification* layer; the actual summarization
        is callsite-specific (LLM choice, prompt, model selection) and
        is left to the caller. When ``dry_run=False`` the primitive
        marks each long trace with ``trace.summarization_pending = true``
        so an out-of-band summarizer can pick them up.

        Args:
            min_steps: Trace must have at least this many steps to qualify.
            max_traces: Cap on candidates returned.
            dry_run: When True, no mutations.
        """
        rows = await self._client.execute_read(
            """
            MATCH (rt:ReasoningTrace)-[:HAS_STEP]->(s:ReasoningStep)
            WITH rt, count(s) AS step_count
            WHERE step_count >= $min_steps
              AND coalesce(rt.summarization_pending, false) = false
              AND coalesce(rt.has_summary, false) = false
            RETURN rt.id AS trace_id, rt.task AS task, step_count
            ORDER BY step_count DESC
            LIMIT $max_traces
            """,
            {"min_steps": min_steps, "max_traces": max_traces},
        )

        candidates: list[ConsolidationCandidate] = []
        for row in rows:
            candidates.append(
                ConsolidationCandidate(
                    kind="summarize_trace",
                    description=(
                        f"Trace {row['trace_id']} ({row['step_count']} steps): {row['task']!r}"
                    ),
                    payload={
                        "trace_id": row["trace_id"],
                        "step_count": row["step_count"],
                    },
                )
            )

        run_id: str | None = None
        if not dry_run and candidates:
            for cand in candidates:
                await self._client.execute_write(
                    """
                    MATCH (rt:ReasoningTrace {id: $trace_id})
                    SET rt.summarization_pending = true,
                        rt.flagged_for_summary_at = datetime()
                    """,
                    {"trace_id": cand.payload["trace_id"]},
                )
                cand.action_taken = True
            run_id = await self._record_run(
                "summarize_long_traces",
                stats={
                    "candidates": len(candidates),
                    "min_steps": min_steps,
                },
            )

        return ConsolidationReport(
            kind="summarize_long_traces",
            dry_run=dry_run,
            candidates=candidates,
            run_id=run_id,
        )

    async def detect_superseded_preferences(
        self,
        *,
        user_identifier: str | None = None,
        similarity_threshold: float = 0.92,
        dry_run: bool = True,
    ) -> ConsolidationReport:
        """Find preference pairs likely to be supersedes.

        Two preferences in the same category, scoped to the same user,
        with high embedding similarity but different text, are flagged
        as supersede candidates — the newer one likely supersedes the
        older.

        When ``dry_run=False``, each candidate becomes a
        :meth:`LongTermMemory.supersede_preference` call.

        Args:
            user_identifier: Restrict to this user. ``None`` checks all users.
            similarity_threshold: Cosine similarity floor (0-1).
            dry_run: When True, no mutations.
        """
        params: dict[str, Any] = {"threshold": similarity_threshold}
        user_match = ""
        if user_identifier is not None:
            user_match = "MATCH (u:User {identifier: $user_identifier})-[:HAS_PREFERENCE]->(old)"
            params["user_identifier"] = user_identifier

        rows = await self._client.execute_read(
            f"""
            MATCH (old:Preference)
            {user_match}
            WHERE old.embedding IS NOT NULL
              AND NOT (old)-[:SUPERSEDED_BY]->()
            CALL {{
                WITH old
                CALL db.index.vector.queryNodes(
                    'preference_embedding_idx', 5, old.embedding
                ) YIELD node AS new, score
                WHERE new <> old
                  AND new.category = old.category
                  AND new.preference <> old.preference
                  AND score >= $threshold
                  AND coalesce(new.created_at, datetime()) >
                      coalesce(old.created_at, datetime())
                RETURN new, score
            }}
            RETURN old.id AS old_id, old.preference AS old_text,
                   new.id AS new_id, new.preference AS new_text,
                   score
            ORDER BY score DESC
            """,
            params,
        )

        candidates: list[ConsolidationCandidate] = []
        for row in rows:
            candidates.append(
                ConsolidationCandidate(
                    kind="supersede_preference",
                    description=(
                        f"{row['old_text']!r} → {row['new_text']!r} (score {row['score']:.3f})"
                    ),
                    payload={
                        "old_id": row["old_id"],
                        "new_id": row["new_id"],
                        "score": row["score"],
                    },
                )
            )

        run_id: str | None = None
        if not dry_run and candidates:
            for cand in candidates:
                await self._client.execute_write(
                    """
                    MATCH (old:Preference {id: $old_id})
                    MATCH (new:Preference {id: $new_id})
                    MERGE (old)-[:SUPERSEDED_BY]->(new)
                    SET old.valid_until = coalesce(old.valid_until, datetime())
                    """,
                    cand.payload,
                )
                cand.action_taken = True
            run_id = await self._record_run(
                "detect_superseded_preferences",
                stats={
                    "candidates": len(candidates),
                    "threshold": similarity_threshold,
                    "user_identifier": user_identifier,
                },
            )

        return ConsolidationReport(
            kind="detect_superseded_preferences",
            dry_run=dry_run,
            candidates=candidates,
            run_id=run_id,
        )

    async def archive_expired_conversations(
        self,
        *,
        ttl_days: int | None = None,
        dry_run: bool = True,
    ) -> ConsolidationReport:
        """Mark conversations older than ``ttl_days`` as archived.

        Pairs with ``MemorySettings.memory.conversation_ttl_days`` — when
        not provided, falls back to the configured default. Sets
        ``c.archived = true`` and ``c.archived_at = datetime()`` on each
        matching Conversation; does not delete data.
        """
        if ttl_days is None:
            raise ValueError(
                "archive_expired_conversations requires ttl_days. Pass it "
                "explicitly or read it from settings.memory.conversation_ttl_days."
            )

        rows = await self._client.execute_read(
            """
            MATCH (c:Conversation)
            WHERE coalesce(c.archived, false) = false
              AND coalesce(c.created_at, c.updated_at) < datetime() - duration({days: $ttl_days})
            RETURN c.id AS id, c.session_id AS session_id,
                   coalesce(c.created_at, c.updated_at) AS created_at
            ORDER BY created_at ASC
            """,
            {"ttl_days": ttl_days},
        )

        candidates: list[ConsolidationCandidate] = []
        for row in rows:
            candidates.append(
                ConsolidationCandidate(
                    kind="archive_conversation",
                    description=(f"Conversation {row['session_id']} ({row['created_at']!s})"),
                    payload={"id": row["id"]},
                )
            )

        run_id: str | None = None
        if not dry_run and candidates:
            await self._client.execute_write(
                """
                UNWIND $ids AS id
                MATCH (c:Conversation {id: id})
                SET c.archived = true,
                    c.archived_at = datetime()
                """,
                {"ids": [c.payload["id"] for c in candidates]},
            )
            for cand in candidates:
                cand.action_taken = True
            run_id = await self._record_run(
                "archive_expired_conversations",
                stats={"candidates": len(candidates), "ttl_days": ttl_days},
            )

        return ConsolidationReport(
            kind="archive_expired_conversations",
            dry_run=dry_run,
            candidates=candidates,
            run_id=run_id,
        )

    async def record_read_audit(
        self,
        query: str,
        *,
        user_identifier: str | None = None,
        kind: str = "memory.read",
        result_count: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record a ``:MemoryReadAudit`` node describing a read this caller performed.

        Use as the explicit-opt-in audit primitive. The library does not
        intercept every ``execute_read`` to record automatically (that
        would multiply traffic by 2x and most reads are uninteresting);
        instead, callers wanting an audit trail invoke this helper
        immediately after sensitive reads.

        Args:
            query: Free-text label for the read (e.g. the natural-language
                query the agent issued, or the API endpoint name).
            user_identifier: Optional :User to attribute the audit to.
                Writes ``(:User)-[:PERFORMED_READ]->(:MemoryReadAudit)``.
            kind: Audit category (e.g. "preference.read", "graph.export").
            result_count: Optional record count for the read.
            metadata: Free-form JSON-serializable dict attached to the audit node.

        Returns:
            The id of the audit node.
        """
        audit_id = str(uuid4())
        await self._client.execute_write(
            """
            CREATE (a:MemoryReadAudit {
                id: $audit_id,
                kind: $kind,
                query: $query,
                user_identifier: $user_identifier,
                result_count: $result_count,
                metadata_json: $metadata_json,
                recorded_at: datetime()
            })
            """,
            {
                "audit_id": audit_id,
                "kind": kind,
                "query": query,
                "user_identifier": user_identifier,
                "result_count": result_count,
                "metadata_json": json.dumps(metadata) if metadata else None,
            },
        )
        if user_identifier is not None:
            await self._client.execute_write(
                """
                MERGE (u:User {identifier: $user_identifier})
                ON CREATE SET u.id = $user_identifier, u.created_at = datetime()
                WITH u
                MATCH (a:MemoryReadAudit {id: $audit_id})
                MERGE (u)-[:PERFORMED_READ]->(a)
                """,
                {
                    "user_identifier": user_identifier,
                    "audit_id": audit_id,
                },
            )
        return audit_id

    async def _record_run(self, kind: str, *, stats: dict[str, Any]) -> str:
        """Write a ``:ConsolidationRun`` audit node and return its id."""
        run_id = str(uuid4())
        await self._client.execute_write(
            """
            CREATE (cr:ConsolidationRun {
                id: $id,
                kind: $kind,
                started_at: datetime(),
                completed_at: datetime(),
                stats_json: $stats_json
            })
            """,
            {
                "id": run_id,
                "kind": kind,
                "stats_json": json.dumps(stats, default=str),
            },
        )
        return run_id
