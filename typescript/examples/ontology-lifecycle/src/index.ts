/**
 * Ontology lifecycle on NAMS — import, activate, ingest, diff, migrate.
 *
 * An *ontology* is the typed, versioned, validated schema the hosted Neo4j
 * Agent Memory Service extracts against: entity types (each mapped onto a
 * POLE+O `poleType`) with typed properties, plus typed relationships. This is
 * the TypeScript twin of `examples/ontology-lifecycle/main.py`, driving the same
 * eight steps through `OntologyClient`:
 *
 *   1. Survey      — `ontology.list()` and `ontology.getActive()`.
 *   2. Import      — `ontology.import({ content, format: "arrows" })` converts
 *                    `schemas/support-desk.arrows.json` into a *draft*. Nothing
 *                    is persisted until `create()`.
 *   3. Activate    — `create()`/`update()` mint an immutable revision,
 *                    `activate()` binds one.
 *   4. Ingest      — `shortTerm.bulkAddMessages()`, then
 *                    `longTerm.waitForExtraction()` + `searchEntities()`: the
 *                    entities NAMS extracts are typed by the active ontology.
 *   5. Revise      — rename `Ticket` → `SupportCase`, tighten the validation
 *                    mode to `strict`; `update()` mints revision 2.
 *   6. Diff        — `ontology.diff(id, from, to)` reports exactly what changed.
 *   7. Migrate     — `ontology.migrate({ typeMappings })` re-labels the
 *                    *already-extracted* entities; `getMigration(job.id)` is
 *                    polled to a terminal status. Dry run first, then for real.
 *                    Nothing is re-ingested — the graph is re-typed in place.
 *   8. Read back   — activate revision 2 and count the new label with
 *                    `query.cypher()`.
 *
 * The ontology surface is hosted-only and needs the REST transport (an endpoint
 * with a `/vN` segment).
 *
 * Run: `cp .env.example .env && npm install && npm start`
 */

import { MemoryClient, NotSupportedError, TransportError } from "@neo4j-labs/agent-memory";
import type {
  BulkMessageInput,
  MigrationJob,
  OntologyDocument,
  OntologySummary,
  OntologyVersion,
} from "@neo4j-labs/agent-memory";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

/** Identity lives in the document's `domain` block — NAMS reads `domain.id`. */
const DOMAIN_ID = "support-desk";
const DOMAIN_NAME = "Support Desk";

const OLD_TYPE = "Ticket";
const NEW_TYPE = "SupportCase";

const ARROWS_FILE = new URL("../schemas/support-desk.arrows.json", import.meta.url);

/** Statuses a migration job never leaves. */
const TERMINAL_STATUSES = new Set(["completed", "failed"]);

/** A support transcript that mentions every type in the ontology. */
const TRANSCRIPT: BulkMessageInput[] = [
  {
    role: "user",
    content:
      "Hi, this is Priya Raman. Order SO-4417 arrived yesterday and the Aurora Desk Lamp was cracked.",
  },
  {
    role: "assistant",
    content:
      "Sorry about that, Priya. I've opened ticket TK-2210 for order SO-4417 covering the damaged Aurora Desk Lamp.",
  },
  { role: "user", content: "Can you send a replacement lamp rather than a refund?" },
  {
    role: "assistant",
    content:
      "Done — ticket TK-2210 is now a replacement request for the Aurora Desk Lamp, shipping to the address on order SO-4417.",
  },
  {
    role: "user",
    content:
      "Also, the Northwind Cable Tray from order SO-4390 was the wrong colour. Same ticket or a new one?",
  },
  {
    role: "assistant",
    content:
      "I've opened ticket TK-2211 for the Northwind Cable Tray on order SO-4390 so the two shipments stay separate.",
  },
];

export interface RunOptions {
  /** Defaults to a hosted `MemoryClient` reading `MEMORY_API_KEY`. */
  client?: MemoryClient;
  /** Line output. Defaults to `console.log`. */
  log?: (line: string) => void;
  /** Owner of the conversation. Defaults to `DEMO_USER_ID`. */
  userId?: string;
  /** How long to wait for background entity extraction. Default 60s. */
  extractionTimeoutMs?: number;
  /** Entity-extraction poll interval. Default 1s. */
  extractionPollMs?: number;
  /** Migration poll interval. Default 1s. */
  migrationPollMs?: number;
  /** Give up polling a migration after this long. Default 120s. */
  migrationTimeoutMs?: number;
  /** Close the client when done. Tests that own the client pass `false`. */
  closeClient?: boolean;
}

export interface RunResult {
  ontologyId: string;
  /** The two revisions this run minted, in order. */
  revisions: number[];
  conversationId: string;
  extractionReady: boolean;
  entityNames: string[];
  /** One entry per migration job: the dry run, then the real one. */
  migrations: { dryRun: boolean; id: string; status?: string; processed?: number }[];
  activeRevision?: number;
  activeValidationMode?: string;
  /** Rows returned by the closing `query.cypher()` read-back. */
  labelCounts: unknown[][];
}

/**
 * Return a copy of `document` with one entity type relabelled.
 *
 * Relationship endpoints reference entity types by label, so they have to be
 * rewritten too — otherwise revision 2 describes relationships between a type
 * that no longer exists and NAMS rejects the update.
 */
export function renameEntityType(
  document: OntologyDocument,
  from: string,
  to: string,
): OntologyDocument {
  const revised = structuredClone(document);
  for (const entityType of revised.entityTypes) {
    if (entityType.label === from) entityType.label = to;
  }
  for (const relationship of revised.relationships) {
    if (relationship.source === from) relationship.source = to;
    if (relationship.target === from) relationship.target = to;
  }
  return revised;
}

/**
 * Render one half of an `OntologyDiff` (entity types or relationships). The leaf
 * shapes are server-defined (they mirror the full ontology type system), so read
 * them defensively rather than modelling them here.
 */
export function describeSection(label: string, section: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const kind of ["added", "removed", "renamed", "modified"]) {
    const items = section[kind];
    if (Array.isArray(items) && items.length > 0) {
      parts.push(`${kind}=${items.length} [${items.map(describeItem).join(", ")}]`);
    }
  }
  return `   ${label}: ${parts.length > 0 ? parts.join(", ") : "no changes"}`;
}

function describeItem(item: unknown): string {
  if (typeof item !== "object" || item === null) return String(item);
  const record = item as Record<string, unknown>;
  if (record.from && record.to) return `${String(record.from)} -> ${String(record.to)}`;
  for (const key of ["label", "type", "name"]) {
    if (record[key]) return String(record[key]);
  }
  return JSON.stringify(record);
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Enqueue a label-rename migration and poll it to a terminal status.
 *
 * `migrate()` returns as soon as the job is *queued* — the re-labelling runs
 * server-side in batches, so the job id is the only handle on progress. Poll it;
 * do not sleep a fixed amount and hope.
 */
export async function runMigration(
  client: MemoryClient,
  options: {
    ontologyId: string;
    fromVersion: OntologyVersion;
    toVersion: OntologyVersion;
    dryRun: boolean;
    log: (line: string) => void;
    pollMs: number;
    timeoutMs: number;
  },
): Promise<MigrationJob> {
  let job = await client.ontology.migrate(options.ontologyId, {
    fromVersionId: options.fromVersion.id,
    toVersionId: options.toVersion.id,
    typeMappings: [{ from: OLD_TYPE, to: NEW_TYPE }],
    dryRun: options.dryRun,
    batchSize: 500,
  });
  const mode = options.dryRun ? "dry run" : "for real";
  options.log(`   migration ${job.id} queued (${mode}), status=${String(job.status)}`);

  const deadline = Date.now() + options.timeoutMs;
  while (job.status === undefined || !TERMINAL_STATUSES.has(job.status)) {
    if (Date.now() >= deadline) {
      options.log(
        `   still ${String(job.status)} after ${options.timeoutMs}ms — stopped polling`,
      );
      return job;
    }
    await sleep(options.pollMs);
    job = await client.ontology.getMigration(job.id);
    options.log(`   ... ${String(job.status)}: ${job.processed ?? 0}/${job.total ?? 0} node(s)`);
  }

  if (job.status === "failed") {
    options.log(`   migration failed: ${String(job.errorMessage)}`);
  } else {
    options.log(
      `   migration ${job.status} (${mode}): ${job.processed ?? 0} re-labelled, ` +
        `${job.errored ?? 0} errored`,
    );
  }
  return job;
}

/** `getActive()` on a workspace with nothing bound: 404, or an unparseable body. */
function isUnbound(error: unknown): boolean {
  if (error instanceof NotSupportedError) return true;
  return error instanceof TransportError && error.statusCode === 404;
}

export async function main(options: RunOptions = {}): Promise<RunResult> {
  const log = options.log ?? ((line: string) => console.log(line));
  // Fail fast and by name, rather than as a transport 401 ten lines later.
  if (!options.client && !process.env.MEMORY_API_KEY) {
    throw new Error(
      "Set MEMORY_API_KEY (see .env.example). The ontology surface is hosted-only. " +
        "Get a key at https://memory.neo4jlabs.com",
    );
  }
  const closeClient = options.closeClient ?? options.client === undefined;
  const client =
    options.client ?? new MemoryClient({ endpoint: process.env.MEMORY_ENDPOINT });
  const extractionTimeoutMs = options.extractionTimeoutMs ?? 60_000;
  const extractionPollMs = options.extractionPollMs ?? 1_000;
  const migrationPollMs = options.migrationPollMs ?? 1_000;
  const migrationTimeoutMs = options.migrationTimeoutMs ?? 120_000;

  try {
    // 1. Survey the workspace ------------------------------------------------
    const summaries = await client.ontology.list();
    const system = summaries.filter((s: OntologySummary) => s.isSystem);
    const owned = summaries.filter((s: OntologySummary) => !s.isSystem);
    log(
      `\nOntologies visible: ${system.length} system template(s), ` +
        `${owned.length} workspace-owned`,
    );
    for (const summary of owned) {
      log(`   ${summary.name} rev ${summary.currentRevision ?? "?"}${summary.isActive ? " (active)" : ""}`);
    }

    try {
      const active = await client.ontology.getActive();
      log(
        `Active ontology: ${active.document.domain.name} ` +
          `(revision ${active.revision ?? "?"}, ${active.validationMode ?? "?"})`,
      );
    } catch (error) {
      if (!isUnbound(error)) throw error;
      // A fresh workspace has nothing bound: extraction falls back to POLE+O.
      log("Active ontology: none bound yet (extraction uses plain POLE+O)");
    }

    // 2. Import the Arrows document into a draft -----------------------------
    const arrowsJson = await readFile(ARROWS_FILE, "utf8");
    const draft = await client.ontology.import({ content: arrowsJson, format: "arrows" });
    if (!draft.document) {
      throw new Error(
        `NAMS could not convert support-desk.arrows.json into an ontology: ` +
          draft.warnings.map((w) => w.message ?? w.code ?? "unknown").join("; "),
      );
    }
    const document = draft.document;
    log(
      `\nImported support-desk.arrows.json (detected format: ${draft.detectedFormat ?? "?"}, ` +
        `suggested name: ${draft.suggestedName ?? "?"}): ` +
        `${document.entityTypes.length} entity type(s), ${document.relationships.length} relationship(s)`,
    );
    for (const entityType of document.entityTypes) {
      const properties = entityType.properties.map((p) => p.name).join(", ") || "no properties";
      log(`   ${entityType.label} -> ${entityType.poleType} (${properties})`);
    }
    for (const warning of draft.warnings) {
      // Conversion is lossy by nature — Arrows has no POLE+O column, so the
      // converter guesses and tells you where it did.
      log(`   warning [${warning.code ?? "?"}] ${warning.message ?? ""}`);
    }

    document.domain.id = DOMAIN_ID;
    document.domain.name = DOMAIN_NAME;
    document.domain.description =
      "E-commerce support desk: customers, orders, products, tickets";
    document.domain.emoji = "🎧";

    // 3. Persist as a revision and activate it -------------------------------
    const existing = owned.find((s: OntologySummary) => s.name === DOMAIN_ID);
    let v1: OntologyVersion;
    if (existing === undefined) {
      v1 = await client.ontology.create({
        name: DOMAIN_NAME,
        schema: document,
        validationMode: "permissive",
      });
      log(`\nCreated ${DOMAIN_ID} revision ${v1.revision} (${v1.validationMode})`);
    } else {
      // Re-run: revisions are immutable, so this mints the next one rather
      // than overwriting revision 1.
      v1 = await client.ontology.update({
        id: existing.id,
        schema: document,
        validationMode: "permissive",
      });
      log(`\nReused ${DOMAIN_ID}: new revision ${v1.revision} (${v1.validationMode})`);
    }

    const ontologyId = v1.ontologyId;
    await client.ontology.activate(v1.id);
    let active = await client.ontology.getActive();
    log(
      `Activated: ${active.document.domain.name} revision ${active.revision ?? "?"} ` +
        `(${active.validationMode ?? "?"}) — extraction now validates against it`,
    );

    // 4. Ingest under the active ontology ------------------------------------
    const userId = options.userId ?? process.env.DEMO_USER_ID ?? "ontology-lifecycle-demo";
    const conversation = await client.shortTerm.createConversation({
      userId,
      metadata: { source: "ontology-lifecycle-example" },
    });
    const stored = await client.shortTerm.bulkAddMessages(conversation.id, TRANSCRIPT);
    log(
      `\nConversation ${conversation.id}: stored ${stored.length} message(s) in one request`,
    );

    // Extraction is a background pipeline; await it rather than sleeping.
    const extractionReady = await client.longTerm.waitForExtraction({
      expectedNames: ["Priya Raman"],
      timeoutMs: extractionTimeoutMs,
      intervalMs: extractionPollMs,
    });
    const entities = await client.longTerm.searchEntities("support ticket order", { limit: 10 });
    log(`Extraction settled: ${extractionReady}; ${entities.length} entity(ies) searchable`);
    for (const entity of entities) log(`   ${entity.name} (${entity.type})`);

    // 5. Revise: rename a type and tighten validation ------------------------
    const revised = renameEntityType(document, OLD_TYPE, NEW_TYPE);
    const v2 = await client.ontology.update({
      id: ontologyId,
      schema: revised,
      validationMode: "strict",
    });
    log(
      `\nRevision ${v2.revision}: ${OLD_TYPE} -> ${NEW_TYPE}, ` +
        `validation mode ${v1.validationMode} -> ${v2.validationMode}`,
    );

    // 6. Diff the two revisions ----------------------------------------------
    const diff = await client.ontology.diff(ontologyId, v1.revision, v2.revision);
    log(`Diff revision ${diff.fromRevision ?? "?"} -> ${diff.toRevision ?? "?"}:`);
    log(describeSection("entity types", diff.entityTypes));
    log(describeSection("relationships", diff.relationships));
    if (diff.modeChange) log(`   validation mode: ${JSON.stringify(diff.modeChange)}`);

    // 7. Migrate the already-extracted entities ------------------------------
    // The payoff: the graph gets re-typed without re-ingesting a single
    // message. Dry run first — it reports counts and touches nothing.
    log("\nMigrating existing entities onto revision 2:");
    const migrations: RunResult["migrations"] = [];
    for (const dryRun of [true, false]) {
      const job = await runMigration(client, {
        ontologyId,
        fromVersion: v1,
        toVersion: v2,
        dryRun,
        log,
        pollMs: migrationPollMs,
        timeoutMs: migrationTimeoutMs,
      });
      migrations.push({ dryRun, id: job.id, status: job.status, processed: job.processed });
    }

    // 8. Activate revision 2 and read the new label back ---------------------
    await client.ontology.activate(v2.id);
    active = await client.ontology.getActive();
    log(
      `\nActive: ${active.document.domain.name} revision ${active.revision ?? "?"} ` +
        `(${active.validationMode ?? "?"})`,
    );

    const result = await client.query.cypher({
      cypher:
        `MATCH (e:Entity) WHERE e:${NEW_TYPE} OR e:${OLD_TYPE} ` +
        "RETURN labels(e) AS labels, count(*) AS count ORDER BY count DESC",
    });
    log(`Label counts after migration: ${JSON.stringify(result.rows)}`);

    log(
      `\nDone. Inspect ${DOMAIN_ID} at https://memory.neo4jlabs.com. ` +
        `Clean up with: await client.ontology.delete("${ontologyId}")`,
    );

    return {
      ontologyId,
      revisions: [v1.revision, v2.revision],
      conversationId: conversation.id,
      extractionReady,
      entityNames: entities.map((e) => e.name),
      migrations,
      activeRevision: active.revision,
      activeValidationMode: active.validationMode,
      labelCounts: result.rows,
    };
  } finally {
    if (closeClient) await client.close();
  }
}

// Only run when executed directly, so tests can import `main`.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err: unknown) => {
    console.error(err);
    process.exit(1);
  });
}
