/**
 * An in-memory stand-in for the hosted Neo4j Agent Memory Service, spoken over
 * a stubbed `fetch`.
 *
 * Why `fetch` and not a fake `Transport`? The ontology sub-API is REST-only —
 * `RestTransport` is what maps `import_ontology` onto
 * `POST /ontologies/import`, `diff_ontology` onto `GET /ontologies/{id}/diff?from&to`,
 * and sends ontology bodies verbatim in snake_case. Stubbing `fetch` keeps that
 * route table and its snake/camel conversion in the test, so a route rename in
 * the SDK fails here. (The SDK's `@neo4j-labs/agent-memory/testing` export ships
 * `BridgeTransport` for cross-language TCK conformance; the ontology endpoints
 * are not reachable over the bridge — the Python SDK refuses them outright.)
 *
 * The fake is stateful on the two things the example teaches:
 *   - `GET /ontologies/active` is unbound (404) until something is activated,
 *     then reports whichever version was activated last;
 *   - migration jobs go `pending` → `running` → `completed`, so the test fails
 *     if the polling loop is replaced by a fixed sleep;
 *   - `POST /entities/search` returns nothing until the second call, so the test
 *     fails if `waitForExtraction()` stops being awaited.
 */

export const ENDPOINT = "https://memory.test/v1";
export const ONTOLOGY_ID = "ont_support_desk";
export const CONVERSATION_ID = "00000000-0000-0000-0000-0000000000aa";

export const SYSTEM_TEMPLATES = [
  {
    id: "ont_general",
    name: "general",
    display_name: "General",
    is_system: true,
    current_revision: 1,
    is_active: false,
  },
  {
    id: "ont_healthcare",
    name: "healthcare",
    display_name: "Healthcare",
    is_system: true,
    current_revision: 1,
    is_active: false,
  },
];

/** The draft `POST /ontologies/import` returns for the shipped Arrows document. */
export const IMPORTED_DOCUMENT = {
  domain: {
    id: "support-desk-import",
    name: "Support Desk Import",
    description: "Converted from Arrows",
  },
  entity_types: [
    {
      label: "Customer",
      pole_type: "PERSON",
      properties: [
        { name: "email", type: "string", unique: true },
        { name: "tier", type: "string" },
      ],
    },
    {
      label: "Order",
      pole_type: "EVENT",
      properties: [{ name: "order_number", type: "string", required: true }],
    },
    {
      label: "Product",
      pole_type: "OBJECT",
      properties: [{ name: "sku", type: "string", unique: true }],
    },
    {
      label: "Ticket",
      pole_type: "EVENT",
      properties: [{ name: "reference", type: "string", required: true }],
    },
  ],
  relationships: [
    { type: "PLACED", source: "Customer", target: "Order" },
    { type: "CONTAINS", source: "Order", target: "Product" },
    { type: "OPENED", source: "Customer", target: "Ticket" },
    { type: "ABOUT", source: "Ticket", target: "Order" },
    { type: "CONCERNS", source: "Ticket", target: "Product" },
  ],
};

/** NAMS speaks camelCase on the memory routes (the ontology sub-API is snake). */
const ENTITIES = [
  { id: "ent-1", name: "Priya Raman", type: "customer", createdAt: "2026-09-10T12:00:05Z" },
  { id: "ent-2", name: "TK-2210", type: "ticket", createdAt: "2026-09-10T12:00:05Z" },
  { id: "ent-3", name: "SO-4417", type: "order", createdAt: "2026-09-10T12:00:05Z" },
];

interface Version {
  id: string;
  ontology_id: string;
  revision: number;
  validation_mode: string;
  schema_json: string;
}

interface Job {
  id: string;
  ontology_id: string;
  status: string;
  total: number;
  processed: number;
  errored: number;
  spec: Record<string, unknown>;
}

export interface FakeNamsOptions {
  /** `search_entities` calls before extraction "completes". Default 2. */
  pollsBeforeExtraction?: number;
  /** `get_migration` polls before a job reports `completed`. Default 2. */
  pollsBeforeMigrationCompletes?: number;
  /** Pre-seed a revision, as if a previous run had created the ontology. */
  seedRevision?: boolean;
}

export class FakeNams {
  readonly calls: { method: string; path: string; query: string }[] = [];
  readonly revisions: Version[] = [];
  readonly migrations: Job[] = [];
  readonly importBodies: Record<string, unknown>[] = [];
  readonly diffQueries: string[] = [];
  readonly cypherQueries: string[] = [];
  readonly createCalls: Record<string, unknown>[] = [];
  closed = false;

  private activeVersionId: string | null = null;
  private entitySearches = 0;
  private readonly migrationPolls = new Map<string, number>();
  private readonly pollsBeforeExtraction: number;
  private readonly pollsBeforeMigrationCompletes: number;

  constructor(options: FakeNamsOptions = {}) {
    this.pollsBeforeExtraction = options.pollsBeforeExtraction ?? 2;
    this.pollsBeforeMigrationCompletes = options.pollsBeforeMigrationCompletes ?? 2;
    if (options.seedRevision) this.mintVersion(IMPORTED_DOCUMENT, "permissive");
  }

  /** Polls recorded per migration job id. */
  pollsFor(jobId: string): number {
    return this.migrationPolls.get(jobId) ?? 0;
  }

  mintVersion(document: unknown, validationMode: string): Version {
    const revision = this.revisions.length + 1;
    const version: Version = {
      id: `ov_${revision}`,
      ontology_id: ONTOLOGY_ID,
      revision,
      validation_mode: validationMode,
      // The service double-encodes the body as a `schema_json` string; the SDK
      // parses it back into an OntologyDocument.
      schema_json: JSON.stringify(document),
    };
    this.revisions.push(version);
    return version;
  }

  documentOf(revision: number): Record<string, unknown> {
    const version = this.revisions.find((v) => v.revision === revision);
    if (!version) throw new Error(`no revision ${revision}`);
    return JSON.parse(version.schema_json) as Record<string, unknown>;
  }

  private summaries(): unknown[] {
    const rows: unknown[] = [...SYSTEM_TEMPLATES];
    if (this.revisions.length > 0) {
      rows.push({
        id: ONTOLOGY_ID,
        name: "support-desk",
        display_name: "Support Desk",
        is_system: false,
        current_revision: this.revisions[this.revisions.length - 1]?.revision,
        is_active: this.activeVersionId !== null,
      });
    }
    return rows;
  }

  private active(): Version | undefined {
    return this.revisions.find((v) => v.id === this.activeVersionId);
  }

  /** Drop-in replacement for `globalThis.fetch`. */
  readonly fetch = async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const url = new URL(input instanceof Request ? input.url : String(input));
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.pathname.replace(/^\/v1/, "");
    this.calls.push({ method, path, query: url.search });
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
    return this.route(method, path, url, body);
  };

  private route(
    method: string,
    path: string,
    url: URL,
    body: Record<string, unknown>,
  ): Response {
    // -- memory routes (camelCase responses, like the live service) ----------
    if (method === "GET" && path === "/conversations") return json({ conversations: [] });
    if (method === "POST" && path === "/conversations") {
      return json(
        { id: CONVERSATION_ID, userId: body.userId, createdAt: "2026-09-10T12:00:00Z" },
        201,
      );
    }
    if (method === "POST" && path === `/conversations/${CONVERSATION_ID}/messages/bulk`) {
      const messages = (body.messages as { role: string; content: string }[]) ?? [];
      return json(
        {
          messages: messages.map((m, index) => ({
            id: `msg-${index + 1}`,
            conversationId: CONVERSATION_ID,
            role: m.role,
            content: m.content,
            createdAt: "2026-09-10T12:00:01Z",
          })),
        },
        201,
      );
    }
    if (method === "POST" && path === "/entities/search") {
      this.entitySearches += 1;
      const ready = this.entitySearches >= this.pollsBeforeExtraction;
      return json({ entities: ready ? ENTITIES : [], searchType: "vector" });
    }
    if (method === "POST" && path === "/query") {
      this.cypherQueries.push(String(body.cypher));
      return json({
        columns: ["labels", "count"],
        rows: [[["Entity", "SupportCase"], 2]],
        stats: {},
      });
    }

    // -- ontology sub-API (snake_case bodies and responses) ------------------
    if (method === "GET" && path === "/ontologies") return json({ ontologies: this.summaries() });
    if (method === "GET" && path === "/ontologies/active") {
      const active = this.active();
      if (!active) return json({ detail: "no active ontology" }, 404);
      return json({ ontology: JSON.parse(active.schema_json) });
    }
    if (method === "POST" && path === "/ontologies/active") {
      this.activeVersionId = String(body.version_id);
      const active = this.active();
      if (!active) throw new Error("activated a version the fake never minted");
      return json(active);
    }
    if (method === "POST" && path === "/ontologies/import") {
      this.importBodies.push(body);
      return json({
        ontology: IMPORTED_DOCUMENT,
        detected_format: "arrows",
        suggested_name: "support-desk-import",
        warnings: [
          {
            code: "pole_type_inferred",
            message: "Inferred pole_type EVENT for label 'Ticket'.",
            path: "nodes[3]",
          },
        ],
      });
    }
    if (method === "POST" && path === "/ontologies") {
      this.createCalls.push(body);
      return json(
        this.mintVersion(body.ontology, String(body.validation_mode ?? "permissive")),
        201,
      );
    }
    if (method === "PUT" && path === `/ontologies/${ONTOLOGY_ID}`) {
      return json(this.mintVersion(body.ontology, String(body.validation_mode ?? "permissive")));
    }
    if (method === "GET" && path === `/ontologies/${ONTOLOGY_ID}`) {
      return json({
        record: { id: ONTOLOGY_ID, name: "support-desk", is_system: false },
        versions: this.revisions,
      });
    }
    if (method === "GET" && path === `/ontologies/${ONTOLOGY_ID}/diff`) {
      this.diffQueries.push(url.search);
      return json({
        from_revision: Number(url.searchParams.get("from")),
        to_revision: Number(url.searchParams.get("to")),
        entity_types: {
          added: [],
          removed: [],
          renamed: [{ from: "Ticket", to: "SupportCase" }],
          modified: [],
        },
        relationships: {
          added: [],
          removed: [],
          renamed: [],
          modified: [
            { type: "OPENED", source: "Customer", target: "SupportCase" },
            { type: "ABOUT", source: "SupportCase", target: "Order" },
            { type: "CONCERNS", source: "SupportCase", target: "Product" },
          ],
        },
        mode_change: { from: "permissive", to: "strict" },
      });
    }
    if (method === "POST" && path === `/ontologies/${ONTOLOGY_ID}/migrate`) {
      const job: Job = {
        id: `mig_${this.migrations.length + 1}`,
        ontology_id: ONTOLOGY_ID,
        status: "pending",
        total: 2,
        processed: 0,
        errored: 0,
        spec: (body.spec as Record<string, unknown>) ?? {},
      };
      this.migrations.push(job);
      this.migrationPolls.set(job.id, 0);
      return json(job, 202);
    }
    if (method === "GET" && path.startsWith("/ontologies/migrations/")) {
      const jobId = path.split("/").pop() ?? "";
      const job = this.migrations.find((j) => j.id === jobId);
      if (!job) return json({ detail: "no such job" }, 404);
      const polls = (this.migrationPolls.get(jobId) ?? 0) + 1;
      this.migrationPolls.set(jobId, polls);
      if (polls < this.pollsBeforeMigrationCompletes) {
        return json({ ...job, status: "running", processed: 1 });
      }
      return json({ ...job, status: "completed", processed: job.total });
    }

    return json({ error: `unmocked route ${method} ${path}` }, 501);
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}
