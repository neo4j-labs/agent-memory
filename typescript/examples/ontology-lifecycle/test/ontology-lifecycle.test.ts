/**
 * Runs the whole ontology lifecycle offline: no API key, no network, no Neo4j.
 *
 * `fetch` is stubbed with `FakeNams`, so the real `RestTransport` route table
 * (and its snake/camel body handling for the ontology sub-API) is exercised —
 * a renamed route or a dropped `snakeBody` flag fails here.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { readFile } from "node:fs/promises";
import { main, describeSection, renameEntityType } from "../src/index.js";
import { ENDPOINT, FakeNams, IMPORTED_DOCUMENT, ONTOLOGY_ID } from "./fake-nams.js";
import type { OntologyDocument } from "@neo4j-labs/agent-memory";

const ARROWS_FILE = new URL("../schemas/support-desk.arrows.json", import.meta.url);

function camelDocument(): OntologyDocument {
  return {
    domain: IMPORTED_DOCUMENT.domain,
    entityTypes: IMPORTED_DOCUMENT.entity_types.map((e) => ({
      label: e.label,
      poleType: e.pole_type,
      properties: e.properties,
    })),
    relationships: IMPORTED_DOCUMENT.relationships,
  };
}

async function run(fake: FakeNams) {
  const lines: string[] = [];
  const result = await main({
    log: (line) => lines.push(line),
    extractionPollMs: 0,
    migrationPollMs: 0,
    extractionTimeoutMs: 2_000,
    migrationTimeoutMs: 2_000,
  });
  return { result, out: lines.join("\n") };
}

describe("ontology lifecycle", () => {
  let fake: FakeNams;

  beforeEach(() => {
    fake = new FakeNams();
    vi.stubEnv("MEMORY_API_KEY", "nams_test");
    vi.stubEnv("MEMORY_ENDPOINT", ENDPOINT);
    vi.stubEnv("MEMORY_WORKSPACE_ID", "");
    vi.stubGlobal("fetch", fake.fetch);
  });

  it("walks import -> activate -> ingest -> diff -> migrate -> read back", async () => {
    const { result, out } = await run(fake);

    // 1. survey — a fresh workspace has nothing bound
    expect(out).toContain("2 system template(s), 0 workspace-owned");
    expect(out).toContain("none bound yet");
    // 2. import
    expect(out).toContain("detected format: arrows");
    expect(out).toContain("Customer -> PERSON");
    expect(out).toContain("Ticket -> EVENT");
    expect(out).toContain("warning [pole_type_inferred]");
    // 3. create + activate
    expect(out).toContain("Created support-desk revision 1 (permissive)");
    expect(out).toContain("Activated: Support Desk revision 1 (permissive)");
    // 4. ingest under the active ontology
    expect(out).toContain("stored 6 message(s) in one request");
    expect(out).toContain("Extraction settled: true; 3 entity(ies) searchable");
    expect(out).toContain("Priya Raman (customer)");
    // 5/6. revise + diff
    expect(out).toContain("Revision 2: Ticket -> SupportCase, validation mode permissive -> strict");
    expect(out).toContain("renamed=1 [Ticket -> SupportCase]");
    expect(out).toContain("modified=3");
    expect(out).toContain('{"from":"permissive","to":"strict"}');
    // 7. migrate, dry run then real, each polled to completion
    expect(out).toContain("migration mig_1 queued (dry run), status=pending");
    expect(out).toContain("migration completed (dry run): 2 re-labelled, 0 errored");
    expect(out).toContain("migration mig_2 queued (for real), status=pending");
    expect(out).toContain("migration completed (for real): 2 re-labelled, 0 errored");
    // 8. read back under revision 2
    expect(out).toContain("Active: Support Desk revision 2 (strict)");

    expect(result.ontologyId).toBe(ONTOLOGY_ID);
    expect(result.revisions).toEqual([1, 2]);
    expect(result.extractionReady).toBe(true);
    expect(result.entityNames).toContain("Priya Raman");
    expect(result.activeRevision).toBe(2);
    expect(result.activeValidationMode).toBe("strict");
    expect(result.labelCounts).toEqual([[["Entity", "SupportCase"], 2]]);
  });

  it("persists the identity from the document's domain block, not the converter's guess", async () => {
    await run(fake);

    const created = fake.documentOf(1) as { domain: { id: string; name: string } };
    expect(created.domain.id).toBe("support-desk");
    expect(created.domain.name).toBe("Support Desk");
    // Revision 2 carries the rename; revision 1 does not.
    const revision1 = fake.documentOf(1) as { entity_types: { label: string }[] };
    const revision2 = fake.documentOf(2) as { entity_types: { label: string }[] };
    expect(revision1.entity_types.map((e) => e.label)).toContain("Ticket");
    expect(revision2.entity_types.map((e) => e.label)).toContain("SupportCase");
    expect(fake.revisions[1]?.validation_mode).toBe("strict");
  });

  it("imports the Arrows document it ships, verbatim", async () => {
    await run(fake);

    expect(fake.importBodies).toHaveLength(1);
    const body = fake.importBodies[0] as { format: string; content: string };
    expect(body.format).toBe("arrows");
    expect(JSON.parse(body.content)).toEqual(
      JSON.parse(await readFile(ARROWS_FILE, "utf8")),
    );
  });

  it("drives the migration from the job spec the diff implies, and polls it", async () => {
    const { result } = await run(fake);

    expect(fake.migrations).toHaveLength(2);
    const [dry, real] = fake.migrations.map((job) => job.spec as Record<string, unknown>);
    expect(dry?.dry_run).toBe(true);
    expect(real?.dry_run).toBe(false);
    for (const spec of [dry, real]) {
      expect(spec?.type_mappings).toEqual([{ from: "Ticket", to: "SupportCase" }]);
      expect(spec?.from_version_id).toBe("ov_1");
      expect(spec?.to_version_id).toBe("ov_2");
      expect(spec?.batch_size).toBe(500);
    }
    // Both jobs were polled past their non-terminal status.
    for (const job of fake.migrations) expect(fake.pollsFor(job.id)).toBeGreaterThanOrEqual(2);
    expect(result.migrations.map((m) => m.status)).toEqual(["completed", "completed"]);
    // The diff asked for the revisions this run actually minted.
    expect(fake.diffQueries).toEqual(["?from=1&to=2"]);
  });

  it("sends a read-only Cypher read-back", async () => {
    await run(fake);

    expect(fake.cypherQueries).toHaveLength(1);
    const query = (fake.cypherQueries[0] ?? "").toUpperCase();
    expect(query).toContain("SUPPORTCASE");
    for (const keyword of ["CREATE", "MERGE", "DELETE", "SET "]) {
      expect(query).not.toContain(keyword);
    }
  });

  it("versions an ontology the workspace already owns instead of duplicating it", async () => {
    const seeded = new FakeNams({ seedRevision: true });
    vi.stubGlobal("fetch", seeded.fetch);

    const { result, out } = await run(seeded);

    expect(out).toContain("Reused support-desk: new revision 2 (permissive)");
    expect(seeded.createCalls).toHaveLength(0);
    expect(result.revisions).toEqual([2, 3]);
    expect(seeded.diffQueries).toEqual(["?from=2&to=3"]);
  });

  it("fails by name without MEMORY_API_KEY", async () => {
    vi.stubEnv("MEMORY_API_KEY", "");
    await expect(main()).rejects.toThrow(/MEMORY_API_KEY/);
  });

  it("stops before persisting anything when the conversion fails", async () => {
    const broken = new FakeNams();
    vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(input instanceof Request ? input.url : String(input));
      if (url.pathname.endsWith("/ontologies/import")) {
        return new Response(
          JSON.stringify({
            ontology: null,
            warnings: [{ code: "unsupported", message: "Unrecognised document" }],
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return broken.fetch(input, init);
    });

    await expect(run(broken)).rejects.toThrow(/Unrecognised document/);
    expect(broken.revisions).toHaveLength(0);
  });
});

describe("helpers", () => {
  it("renameEntityType rewrites relationship endpoints and leaves the source alone", () => {
    const document = camelDocument();
    const revised = renameEntityType(document, "Ticket", "SupportCase");

    expect(revised.entityTypes.map((e) => e.label)).toEqual([
      "Customer",
      "Order",
      "Product",
      "SupportCase",
    ]);
    for (const relationship of revised.relationships) {
      expect(relationship.source).not.toBe("Ticket");
      expect(relationship.target).not.toBe("Ticket");
    }
    // A dangling endpoint would make revision 2 invalid; the original document
    // must stay intact so the diff has something to compare against.
    expect(document.entityTypes.map((e) => e.label)).toContain("Ticket");
  });

  it("describeSection reads the server-defined diff shapes defensively", () => {
    expect(describeSection("entity types", {})).toContain("no changes");
    expect(
      describeSection("entity types", { renamed: [{ from: "A", to: "B" }], added: [] }),
    ).toContain("renamed=1 [A -> B]");
    expect(describeSection("relationships", { added: [{ type: "OPENED" }] })).toContain(
      "added=1 [OPENED]",
    );
  });
});
