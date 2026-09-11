"""Eval-harness example: a labelled memory-quality suite you can gate CI on.

Memory quality as a regression metric, not a leaderboard. The script seeds a
deterministic fixture graph, declares what "correct" means for it, and scores
all three dimensions of the v0.2 evaluation harness:

* **retrieval** — ``RetrievalCase``: recall@k of
  ``long_term.search_entities()`` against labelled expected entity ids.
* **audit** — ``AuditCase``: recall of
  ``(:Entity)<-[:TOUCHED]-(:ReasoningStep)`` against labelled step ids.
* **preference** — ``PreferenceCase``: F1 of
  ``long_term.get_preferences_for(active_only=True)`` against the expected
  *active* ids, so a missed supersede *and* a leaked cross-tenant preference
  both show up as a score below 1.0.

Every case is built to be able to fail:

* ``settings.memory.multi_tenant = True`` — drop a ``user_identifier=`` from
  any seed write and the run raises instead of silently writing unscoped data.
* Two preferences are seeded and one is superseded. Comment out the
  ``supersede_preference`` call and the preference score drops to 0.8.
* Two tenants are seeded. Each ``PreferenceCase`` expects only its own
  tenant's id, so a scoping leak is an F1 miss, not a silent pass.

Run from the repo root::

    uv run python examples/eval-harness/main.py
    uv run python examples/eval-harness/main.py --dimensions retrieval,preference
    uv run python examples/eval-harness/main.py --min-score 0.9 --out eval-trend.jsonl

``--min-score`` turns the script into a gate: it exits 1 (and prints the
expected-vs-actual breakdown of the failing cases) when the overall score
falls below the threshold. ``ci_gate.py`` in this directory wraps that in a
JSON-reporting CI step.

**Backend boundary.** ``client.eval.run()`` is backend-portable, but this
example's *seed* is bolt-only: ``client.users`` and ``client.graph.execute_write``
both raise ``NotSupportedError`` on the hosted NAMS backend, and the
``:TOUCHED`` audit edges the audit dimension reads are a bolt-side schema
feature. On NAMS, seed through the memory APIs and run
``--dimensions retrieval,preference``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from pydantic import SecretStr

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import (
    ExtractionConfig,
    ExtractorType,
    MemoryConfig,
)
from neo4j_agent_memory.memory.eval import (
    AuditCase,
    EvalReport,
    EvalSuite,
    PreferenceCase,
    RetrievalCase,
)
from neo4j_agent_memory.schema.models import EntityRef

#: The dimensions ``client.eval.run(suite, dimensions=[...])`` understands.
DIMENSIONS = ("retrieval", "audit", "preference")

#: Two tenants, so the preference cases can catch a cross-tenant leak.
USER_A = "eval-user-a@demo"
USER_B = "eval-user-b@demo"
DEMO_USERS = [USER_A, USER_B]

#: Session id for the seeded reasoning trace.
SESSION = "eval-demo"

# The seeded entity names carry an "(eval)" suffix for two reasons: the reset
# below can delete them by name without touching a real graph, and they can
# never collide with the "Anthem" node the companion audit-trail example
# MERGEs through its own :TOUCHED edges.
ANTHEM = "Anthem Health Plan (eval)"
MERCY = "Mercy General Hospital (eval)"
ORBIT = "Orbit Logistics (eval)"
DEMO_ENTITY_NAMES = [ANTHEM, MERCY, ORBIT]

# The labelled retrieval query. Two properties of the library shape it:
# ``add_entity`` embeds the entity *name* only (descriptions are metadata, not
# retrieval signal), and ``search_entities`` applies a 0.7 similarity floor.
# So a labelled query has to be close to the name: with all-MiniLM-L6-v2 this
# one scores ~0.77 on ANTHEM, ~0.64 on MERCY and ~0.53 on ORBIT — above the
# floor for the expected hit, below it for the near miss and the distractor.
RETRIEVAL_QUERY = "health plan"


class SeedLabels(TypedDict):
    """The ground truth produced by :func:`seed`, i.e. the labels."""

    user_a: str
    user_b: str
    active_pref_id: str
    superseded_pref_id: str
    user_b_pref_id: str
    anthem_id: str
    step_id: str


def build_settings(*, multi_tenant: bool = True) -> MemorySettings:
    """Settings for the demo: no LLM, a local embedder, no extractor."""
    return MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "password")),
        ),
        llm=None,
        # v0.3+ provider-string shorthand for a local sentence-transformers
        # embedder. The retrieval dimension needs real embeddings, so this is
        # load-bearing rather than decorative — see the README prerequisites.
        embedding="sentence-transformers/all-MiniLM-L6-v2",
        extraction=ExtractionConfig(extractor_type=ExtractorType.NONE),
        memory=MemoryConfig(
            # Every write that accepts ``user_identifier=`` must now supply
            # one or raise. That is what makes the isolation claim testable:
            # delete a kwarg in seed() and the example fails loudly.
            multi_tenant=multi_tenant,
        ),
    )


async def reset_demo_data(client: MemoryClient) -> None:
    """Delete everything a previous run seeded, so the labels stay exact.

    Bolt-only (``client.graph.execute_write``). Scoped to the demo users,
    the demo session and the three "(eval)" entity names — it never issues an
    unqualified ``MATCH (n) DETACH DELETE n``.
    """
    # Demo tenants and the preferences hanging off them.
    await client.graph.execute_write(
        """
        MATCH (u:User) WHERE u.identifier IN $users
        OPTIONAL MATCH (u)-[:HAS_PREFERENCE]->(p:Preference)
        DETACH DELETE u, p
        """,
        {"users": DEMO_USERS},
    )
    # The seeded trace, its steps and their tool calls.
    await client.graph.execute_write(
        """
        MATCH (rt:ReasoningTrace {session_id: $session})
        OPTIONAL MATCH (rt)-[:HAS_STEP]->(s:ReasoningStep)
        OPTIONAL MATCH (s)-[:USES_TOOL]->(tc:ToolCall)
        DETACH DELETE rt, s, tc
        """,
        {"session": SESSION},
    )
    # The seeded entities.
    await client.graph.execute_write(
        "MATCH (e:Entity) WHERE e.name IN $names DETACH DELETE e",
        {"names": DEMO_ENTITY_NAMES},
    )


async def seed(client: MemoryClient) -> SeedLabels:
    """Seed a deterministic fixture graph and return its labels."""
    await reset_demo_data(client)

    await client.users.upsert_user(identifier=USER_A)
    await client.users.upsert_user(identifier=USER_B)

    # --- Retrieval fixture -------------------------------------------------
    # Three CLIENT entities: one the labels expect for RETRIEVAL_QUERY, one
    # near miss, one deliberate distractor from another industry — so recall@k
    # measures something. ``resolve=False, deduplicate=False`` keeps the seed
    # byte-identical across runs: with resolution on, a fuzzy match against a
    # pre-existing "Anthem" would rename the node and move the label.
    anthem, _ = await client.long_term.add_entity(
        ANTHEM,
        "CLIENT",
        description="Healthcare payer; commercial and Medicare Advantage lines",
        resolve=False,
        deduplicate=False,
    )
    await client.long_term.add_entity(
        MERCY,
        "CLIENT",
        description="Regional hospital network and provider group",
        resolve=False,
        deduplicate=False,
    )
    await client.long_term.add_entity(
        ORBIT,
        "CLIENT",
        description="Freight forwarding and last-mile logistics operator",
        resolve=False,
        deduplicate=False,
    )

    # --- Audit fixture -----------------------------------------------------
    trace = await client.reasoning.start_trace(
        SESSION,
        f"Recommend a team for {ANTHEM}",
        user_identifier=USER_A,
    )
    step = await client.reasoning.add_step(trace.id, thought=f"Look up {ANTHEM}")
    await client.reasoning.record_tool_call(
        step.id,
        tool_name="lookup_client",
        arguments={"name": ANTHEM},
        result={"segment": "healthcare payer"},
        # ``EntityRef`` identity precedence is id > name+type > name. Passing
        # the id attaches :TOUCHED to the entity add_entity just created
        # instead of MERGE-ing a second, embedding-less "Anthem Health (eval)".
        touched_entities=[EntityRef(id=str(anthem.id), name=ANTHEM, type="CLIENT")],
    )

    # --- Preference fixture ------------------------------------------------
    # Two preferences for tenant A, the first superseded by the second. The
    # expected set below deliberately excludes the superseded id, so the case
    # fails if supersede_preference stops taking effect.
    superseded = await client.long_term.add_preference(
        "consultants",
        "Any seniority is fine for healthcare engagements",
        user_identifier=USER_A,
    )
    active = await client.long_term.add_preference(
        "consultants",
        "Only principals and senior managers on payer accounts",
        user_identifier=USER_A,
    )
    if str(active.id) == str(superseded.id):
        # add_preference dedupes semantically similar preferences inside a
        # category (cosine >= 0.95). If that fires, there is only one node and
        # the regression the preference case exists to catch is unreachable.
        raise RuntimeError(
            "The two seeded preferences were deduplicated into one node. "
            "Edit the strings so they are less similar, then re-run."
        )
    # Two positional ids — there is no ``new_preference=`` keyword. The
    # ignore is the protocol's, not ours: ``supersede_preference`` exists on
    # the bolt ``LongTermMemory`` but is not yet declared on
    # ``LongTermProtocol``, which is how ``client.long_term`` is typed.
    await client.long_term.supersede_preference(  # type: ignore[attr-defined]
        superseded.id, active.id
    )

    # Tenant B, in a different category so the global per-category dedupe in
    # add_preference cannot collapse it into tenant A's node.
    user_b_pref = await client.long_term.add_preference(
        "industries",
        "Prefers fintech engagements over healthcare",
        user_identifier=USER_B,
    )

    return SeedLabels(
        user_a=USER_A,
        user_b=USER_B,
        active_pref_id=str(active.id),
        superseded_pref_id=str(superseded.id),
        user_b_pref_id=str(user_b_pref.id),
        anthem_id=str(anthem.id),
        step_id=str(step.id),
    )


def build_suite(labels: SeedLabels) -> EvalSuite:
    """Turn the seed labels into an :class:`EvalSuite`."""
    return EvalSuite(
        retrieval=[
            RetrievalCase(
                query=RETRIEVAL_QUERY,
                expected_entity_ids={labels["anthem_id"]},
                k=5,
            ),
        ],
        audit=[
            AuditCase(
                entity_id=labels["anthem_id"],
                expected_step_ids={labels["step_id"]},
            ),
        ],
        preference=[
            # Tenant A: the active preference only — the superseded id must
            # not come back.
            PreferenceCase(
                user_identifier=labels["user_a"],
                expected_active_pref_ids={labels["active_pref_id"]},
            ),
            # Tenant B: their own preference only — tenant A's must not leak.
            PreferenceCase(
                user_identifier=labels["user_b"],
                expected_active_pref_ids={labels["user_b_pref_id"]},
            ),
        ],
    )


def parse_dimensions(raw: str | None) -> list[str] | None:
    """Parse ``--dimensions retrieval,preference`` into a list for ``eval.run``."""
    if not raw:
        return None
    wanted = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [d for d in wanted if d not in DIMENSIONS]
    if unknown:
        raise SystemExit(f"Unknown dimension(s): {', '.join(unknown)}. Pick from {DIMENSIONS}.")
    return wanted


def report_payload(
    report: EvalReport,
    *,
    min_score: float,
    include_details: bool = False,
) -> dict[str, Any]:
    """A JSON-serialisable summary — one trend row, or a CI report body."""
    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall": round(report.overall_score, 4),
        "min_score": min_score,
        "passed": report.overall_score >= min_score,
    }
    for name in DIMENSIONS:
        dimension = getattr(report, name)
        if dimension is None:
            payload[name] = None
            continue
        entry: dict[str, Any] = {
            "cases": dimension.cases,
            "score": round(dimension.score, 4),
        }
        if include_details:
            entry["details"] = dimension.details
        payload[name] = entry
    return payload


def print_report(report: EvalReport, *, min_score: float) -> None:
    """Print the scores, then the expected-vs-actual diff for any miss."""
    print("=== Eval report ===")
    print(f"Overall:    {report.overall_score:.2f}  (gate: >= {min_score:.2f})")
    for name in DIMENSIONS:
        dimension = getattr(report, name)
        if dimension is None:
            print(f"{name.capitalize() + ':':11} skipped")
            continue
        print(f"{name.capitalize() + ':':11} cases={dimension.cases} score={dimension.score:.2f}")

    for name in DIMENSIONS:
        dimension = getattr(report, name)
        if dimension is None or dimension.score >= 1.0:
            continue
        print(f"\n--- {name} misses ---")
        for detail in dimension.details:
            # ``details`` is a list of per-case dicts; the metric keys differ
            # per dimension (recall, or precision/recall/f1 for preference).
            metric = detail.get("f1", detail.get("recall"))
            if metric is not None and metric >= 1.0:
                continue
            case_key = next(
                (k for k in ("query", "entity_id", "user_identifier") if k in detail),
                None,
            )
            if case_key:
                print(f"  case {case_key}={detail[case_key]!r}")
            print(f"    expected = {detail.get('expected')}")
            print(f"    actual   = {detail.get('actual') or detail.get('retrieved')}")
            scores = {k: detail[k] for k in ("recall", "precision", "f1") if k in detail}
            print(f"    scores   = {scores}")


async def run_eval(
    *,
    dimensions: list[str] | None = None,
    settings: MemorySettings | None = None,
) -> tuple[EvalReport, SeedLabels]:
    """Seed the fixture graph, run the suite, return the report and labels."""
    async with MemoryClient(settings or build_settings()) as client:
        labels = await seed(client)
        report = await client.eval.run(build_suite(labels), dimensions=dimensions)
        return report, labels


def append_trend_row(path: Path, payload: dict[str, Any]) -> None:
    """Append one JSONL row, so a CI artifact can plot the score over time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument(
        "--min-score",
        type=float,
        default=1.0,
        help="Exit 1 when the overall score falls below this (default: 1.0).",
    )
    parser.add_argument(
        "--dimensions",
        default=None,
        help=f"Comma-separated subset of {','.join(DIMENSIONS)} (default: all).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Append one JSONL row per run to this file, for trend plotting.",
    )
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    report, _labels = await run_eval(dimensions=parse_dimensions(args.dimensions))

    print_report(report, min_score=args.min_score)
    payload = report_payload(report, min_score=args.min_score)
    if args.out:
        append_trend_row(args.out, payload)
        print(f"\nAppended one trend row to {args.out}")

    if not payload["passed"]:
        raise SystemExit(
            f"FAIL: overall {report.overall_score:.2f} < --min-score {args.min_score:.2f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
