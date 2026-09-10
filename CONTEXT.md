# Neo4j Agent Memory

A three-layer memory system for AI agents (short-term, long-term, reasoning) shipped as two SDKs (Python, TypeScript) over two backends: self-hosted Neo4j ("bolt") and the hosted Neo4j Agent Memory Service ("NAMS").

## Language

### Product & backends

**NAMS**:
The hosted Neo4j Agent Memory Service (`memory.neo4jlabs.com`, REST `/v1`). Ships continuously; has no product version of its own — never tie NAMS to an SDK version number.
_Avoid_: "hosted SDK", "NAMS v0.x"

**Bolt backend**:
The self-hosted mode where the SDK talks to a user-managed Neo4j over the bolt protocol. The only backend where preferences, facts, client-side relationship creation, and the extraction pipeline configuration exist.
_Avoid_: "local mode", "OSS mode"

**POLE+O**:
The default entity classification: **P**erson, **O**bject, **L**ocation, **E**vent — the POLE model from law-enforcement analysis — **plus O**rganization as the extension. Organization is the "+O"; Object is core POLE. (Confirmed by `POLEOEntityType` in the SDK and `PoleType` in the NAMS server.)
_Avoid_: "Person, Organization, Location, Event + Object" (an inversion that appeared in one audit)

**Conformance tier**:
Bronze/Silver/Gold/Platinum labels from the agent-memory TCK certifying *client* behavioral conformance. Never a NAMS pricing, access, or service tier — NAMS has no paid tiers.
_Avoid_: "NAMS Platinum", "higher tier", any feature "gated by tier"

**Workspace**:
The NAMS tenancy boundary. Selected by workspace-bound API key or `X-Workspace-Id` header (`MEMORY_WORKSPACE_ID`); distinct from per-conversation `user` scoping.
_Avoid_: conflating with "user", "account"

### Docs effort

**Disposition table**:
The triage artifact for an external docs audit: every finding classified as *already-fixed-in-repo* (→ ops handoff), *open* (→ fix now), or *rebutted* (audit wrong, with evidence).

**Ops handoff**:
Actions required to make merged docs true in public (tag releases, republish site, CDN purge, redirects) that cannot be done by editing this repo.
