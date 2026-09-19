/**
 * E2E ontology lifecycle against the hosted service (staging).
 *
 * Requires MEMORY_API_KEY and RUN_ISOLATED_ONTOLOGY_TESTS=1 in an exclusively
 * owned test workspace. Mutates workspace-global active-ontology
 * state, so it snapshots and restores the active version, and deletes the
 * test clone afterward.
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { MemoryClient } from "../../src/client.js";
import { NotFoundError } from "../../src/errors.js";
import type { ActiveOntology } from "../../src/ontology/index.js";

const API_KEY = (process.env.MEMORY_API_KEY ?? "").trim();
const ENDPOINT = process.env.MEMORY_ENDPOINT ?? "https://memory.neo4jlabs.com/v1";
const WORKSPACE_ID = (process.env.MEMORY_WORKSPACE_ID ?? "").trim() || undefined;
const describeOrSkip = API_KEY.length > 0 && process.env.RUN_ISOLATED_ONTOLOGY_TESTS === "1"
  ? describe : describe.skip;

const TEMPLATE = "conservation";
const CLONE_NAME = `${TEMPLATE}-clone`;

describeOrSkip("ontology lifecycle (hosted)", () => {
  let client: MemoryClient | undefined;
  let before: ActiveOntology | undefined;
  const ownedIds = new Set<string>();
  const ownedVersions = new Map<string, string>();

  beforeAll(async () => {
    client = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY, workspaceId: WORKSPACE_ID });
    before = await client!.ontology.getActive();
    if (!before.versionId || !before.ontologyId || !before.revision ||
        !["strict", "permissive"].includes(before.validationMode ?? "")) {
      throw new Error("No authoritative prior binding; no ontology mutation is allowed");
    }
  });

  afterAll(async () => {
    if (!client) return;
    try {
      if (!before?.versionId || !before.validationMode) return;
      const active = await client!.ontology.getActive();
      const isPrior = active.versionId === before.versionId && active.validationMode === before.validationMode;
      const isOwned = active.versionId !== undefined && ownedVersions.has(active.versionId) &&
        ownedVersions.get(active.versionId) === active.validationMode;
      if (!isPrior && !isOwned) {
        throw new Error(`Unexpected active binding; retained ontology IDs ${[...ownedIds].join(", ")}`);
      }
      if (!isPrior) await client!.ontology.activate(before.versionId);
      expect(await client!.ontology.getActive()).toEqual(before);
      for (const id of ownedIds) {
        expect(await client!.ontology.getActive()).toEqual(before);
        await client!.ontology.delete(id);
        await expect(client!.ontology.get(id)).rejects.toBeInstanceOf(NotFoundError);
      }
    } finally {
      await client.close();
    }
  });

  it("lists system templates", async () => {
    const names = (await client!.ontology.list()).map((o) => o.name);
    expect(names).toContain(TEMPLATE);
    expect(names).toContain("nams-default");
  });

  it("clone → update (preserves schema) → activate → getActive", async () => {
    const existingIds = new Set((await client!.ontology.list()).map((item) => item.id));
    const v = await client!.ontology.clone(TEMPLATE);
    expect(existingIds.has(v.ontologyId)).toBe(false);
    ownedIds.add(v.ontologyId);
    ownedVersions.set(v.id, v.validationMode);
    expect(v.revision).toBe(1);
    const typeCount = v.document?.entityTypes.length ?? 0;
    expect(typeCount).toBeGreaterThan(0);

    const v2 = await client!.ontology.update({
      id: v.ontologyId,
      schema: v.document!,
      validationMode: "strict",
    });
    expect(v2.revision).toBe(2);
    expect(v2.document?.entityTypes.length).toBe(typeCount); // schema preserved

    ownedVersions.set(v2.id, v2.validationMode);
    expect(await client!.ontology.getActive()).toEqual(before);
    await client!.ontology.activate(v2.id);
    const active = await client!.ontology.getActive();
    expect(active.document.domain.id).toBe(CLONE_NAME);
    expect(active.validationMode).toBe("strict");
    expect(active.revision).toBe(2);
    expect(active.versionId).toBe(v2.id);
  });
});
