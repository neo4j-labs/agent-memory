/** Supported, conservative cleanup for one recorded NAMS tutorial run. */
import type { CypherResult } from "@neo4j-labs/agent-memory";
import { resolve } from "node:path";
import { inspectTutorialState, TutorialRun } from "./tutorial-state.js";

export function queryRows(result: CypherResult): Array<Record<string, unknown>> {
  if (result.stats && Object.values(result.stats).some(value => value !== 0)) throw new Error("The tutorial query reported mutation statistics.");
  return (result.rows as unknown[]).map(row => {
    if (Array.isArray(row)) return Object.fromEntries(result.columns.map((column, i) => [column, row[i]]));
    if (row && typeof row === "object") return row as Record<string, unknown>;
    throw new Error("Unexpected query row shape.");
  });
}
export const CLEANUP_QUERIES = {
  derived: "MATCH (m:Message)-[:MENTIONS|EXTRACTED_FROM]-(e:Entity) WHERE m.id IN $ids RETURN DISTINCT e.id AS id, toString(e.createdAt) AS created",
  provenance: "MATCH (e:Entity {id: $id}) OPTIONAL MATCH (e)-[:EXTRACTED_FROM]->(s) RETURN e.id AS id, e.name AS name, toString(e.createdAt) AS created, collect(DISTINCT s.id) AS sources, count(DISTINCT s) AS source_count",
  neighbors: "MATCH (e:Entity {id: $id}) OPTIONAL MATCH (e)-[r]-(n) RETURN n.id AS id, labels(n) AS labels, type(r) AS relationship",
  residual: "MATCH (n) WHERE n.id IN $ids OR n.conversationId = $conversation OR n.conversation_id = $conversation RETURN n.id AS id, labels(n) AS labels LIMIT 100",
  graph: "MATCH (m:Message)-[r:MENTIONS|EXTRACTED_FROM]-(e:Entity) WHERE m.id IN $ids RETURN m.id AS message_id, type(r) AS relationship, e.id AS entity_id LIMIT 100",
} as const;
export function is404(error: unknown): boolean {
  return error !== null && typeof error === "object" && "statusCode" in error && error.statusCode === 404;
}
async function absent(read: () => Promise<unknown>): Promise<boolean> {
  try { await read(); return false; } catch (error) { if (is404(error)) return true; throw error; }
}
export async function waitForTerminalExtraction(run: TutorialRun, timeoutMs = 60_000, intervalMs = 1_000): Promise<void> {
  const ids = run.state.resources.filter(r => r.kind === "message").map(r => r.id).sort();
  const deadline = Date.now() + timeoutMs;
  do {
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    const value = await run.request(`/conversations/${encodeURIComponent(run.conversationId)}/extraction-status`, Math.min(30_000, remaining)) as { messages?: Array<{ id?: string; status?: string }> };
    if (!Array.isArray(value.messages) || value.messages.some(m => typeof m.id !== "string" || typeof m.status !== "string")) throw new Error("Malformed extraction status; retain the run for inspection.");
    const returned = value.messages.map(m => m.id!).sort();
    if (JSON.stringify(returned) !== JSON.stringify(ids)) throw new Error("Extraction status does not cover exactly the recorded messages.");
    if (value.messages.some(m => ["error", "failed"].includes(m.status!))) throw new Error("Extraction failed; retain the run for inspection before cleanup.");
    if (value.messages.every(m => m.status === "done")) return;
    if (Date.now() >= deadline) break;
    await new Promise(resolve => setTimeout(resolve, Math.min(intervalMs, Math.max(0, deadline - Date.now()))));
  } while (Date.now() <= deadline);
  throw new Error("Extraction deadline expired; no records were deleted.");
}
async function prepareEntities(run: TutorialRun): Promise<void> {
  const ids = run.state.resources.filter(r => r.kind === "message").map(r => r.id);
  const derived = queryRows(await run.client.query.cypher({ cypher: CLEANUP_QUERIES.derived, params: { ids } }));
  for (const entity of derived) {
    if (typeof entity.id !== "string" || !entity.id) throw new Error("Derived entity response has no valid ID.");
    if (!run.state.resources.some(r => r.kind === "entity" && r.id === entity.id)) run.retain({ kind: "entity", id: entity.id, disposition: "present", origin: "derived" });
  }
  for (const entity of run.state.resources.filter(r => r.kind === "entity" && r.disposition !== "deleted")) {
    const rows = queryRows(await run.client.query.cypher({ cypher: CLEANUP_QUERIES.provenance, params: { id: entity.id } }));
    if (!rows.length) {
      if (!(await absent(() => run.client.longTerm.getEntity(entity.id)))) throw new Error("Entity exists but provenance query did not find it.");
      entity.disposition = "deleted"; entity.reason = "Verified absent before cleanup"; run.save(); continue;
    }
    if (rows.length !== 1 || rows[0]!.id !== entity.id) throw new Error("Unexpected entity provenance result.");
    const proof = rows[0]!;
    const sources = proof.sources;
    const created = typeof proof.created === "string" ? Date.parse(proof.created) : NaN;
    const fresh = Number.isFinite(created) && created >= Date.parse(run.state.createdAt) && created <= Date.now();
    const exclusivelyOwned = Array.isArray(sources) && Number.isInteger(proof.source_count) &&
      proof.source_count === new Set(sources).size && sources.every(s => typeof s === "string" && ids.includes(s));
    const explicitEvidence = entity.origin !== "derived" && entity.merged === false && entity.name?.includes(run.state.runId) && entity.name === proof.name;
    const owned = fresh && exclusivelyOwned && (entity.origin === "derived" || entity.merged === false) && ((sources as unknown[]).length > 0 || explicitEvidence);
    entity.createdAt = typeof proof.created === "string" ? proof.created : undefined;
    entity.sourceIds = Array.isArray(sources) ? sources.filter((s): s is string => typeof s === "string") : [];
    entity.disposition = owned ? "present" : "retained";
    entity.reason = owned ? "Creation time and exclusively owned sources verified" : "Shared, merged, or ambiguous creation/provenance; do not delete";
    run.save();
  }
  const neighbors = new Map<string, string[]>();
  for (const entity of run.state.resources.filter(r => r.kind === "entity" && r.disposition === "present")) {
    const rows = queryRows(await run.client.query.cypher({ cypher: CLEANUP_QUERIES.neighbors, params: { id: entity.id } }));
    if (!rows.length) {
      entity.disposition = await absent(() => run.client.longTerm.getEntity(entity.id)) ? "deleted" : "retained";
      entity.reason = entity.disposition === "deleted" ? "Verified absent during adjacency inspection" : "Entity exists but adjacency query returned no row; ownership is unverified";
      continue;
    }
    const linked: string[] = [];
    for (const row of rows) {
      if (row.relationship === null && row.id === null) continue;
      if (typeof row.id !== "string" || !row.id || typeof row.relationship !== "string") {
        entity.disposition = "retained"; entity.reason = "An adjacent record has unknown identity"; break;
      }
      linked.push(row.id);
    }
    neighbors.set(entity.id, linked);
  }
  // Retention propagates through the candidate graph: do not break links to
  // another candidate that must itself remain because of shared data.
  let changed: boolean;
  do {
    changed = false;
    const owned = new Set([...ids, run.conversationId, ...run.state.resources.filter(r => r.kind === "entity" && r.disposition === "present").map(r => r.id)]);
    for (const entity of run.state.resources.filter(r => r.kind === "entity" && r.disposition === "present")) {
      if (neighbors.get(entity.id)?.some(id => !owned.has(id))) {
        entity.disposition = "retained"; entity.reason = "Connected to shared, retained, or outside data"; changed = true;
      }
    }
  } while (changed);
  run.state.cleanup!.prepared = true; run.save();
}
export async function inspectGraph(run: TutorialRun): Promise<Array<Record<string, unknown>>> {
  await run.verify();
  return queryRows(await run.client.query.cypher({ cypher: CLEANUP_QUERIES.graph,
    params: { ids: run.state.resources.filter(r => r.kind === "message").map(r => r.id) } }));
}
export async function cleanupTutorial(run: TutorialRun, options: { timeoutMs?: number; intervalMs?: number } = {}): Promise<{ complete: boolean; retainedIds: string[]; residualIds: string[] }> {
  const conversation = run.state.resources.find(r => r.kind === "conversation");
  if (!conversation) throw new Error("No recorded conversation. Preserve uncertain state; no cleanup targets can be inferred.");
  if (run.state.operations.some(op => op.status !== "done")) throw new Error("An uncertain write remains; inspect and recover exact IDs before cleanup.");
  try {
    const gone = await absent(() => run.ownedConversation());
    if (gone && !run.state.cleanup?.prepared) throw new Error("Conversation disappeared before provenance was saved; derived cleanup cannot be established.");
    if (!gone) {
      await run.verify();
      await waitForTerminalExtraction(run, options.timeoutMs ?? 60_000, options.intervalMs ?? 1_000);
    }
    run.state.cleanup ??= { prepared: false, complete: false, residualIds: [] };
    run.state.cleanup.complete = false; delete run.state.cleanup.error; run.save();
    // Recheck provenance on every retry while the conversation still exists.
    if (!gone) await prepareEntities(run);
    for (const entity of run.state.resources.filter(r => r.kind === "entity" && r.disposition === "present")) {
      if (gone) throw new Error("Conversation disappeared while an entity deletion was incomplete; retain that entity.");
      if (!(await absent(() => run.client.longTerm.getEntity(entity.id)))) await run.client.longTerm.deleteEntity(entity.id);
      if (!(await absent(() => run.client.longTerm.getEntity(entity.id)))) throw new Error(`Entity ${entity.id} remains after deletion.`);
      entity.disposition = "deleted"; entity.reason = "DELETE followed by verified GET 404"; run.save();
    }
    if (!gone) await run.client.shortTerm.deleteConversation(conversation.id);
    if (!(await absent(() => run.client.shortTerm.getConversationMetadata(conversation.id)))) throw new Error("Conversation remains after deletion.");
    conversation.disposition = "deleted";
    for (const message of run.state.resources.filter(r => r.kind === "message")) message.disposition = "deleted";
    run.save();
    const ids = run.state.resources.filter(r => r.kind !== "entity" || r.disposition === "deleted").map(r => r.id);
    const residual = queryRows(await run.client.query.cypher({ cypher: CLEANUP_QUERIES.residual, params: { ids, conversation: conversation.id } }));
    if (residual.some(row => typeof row.id !== "string")) throw new Error("Malformed residual query result.");
    const residualIds = residual.map(row => row.id as string);
    for (const resource of run.state.resources.filter(r => residualIds.includes(r.id))) {
      resource.disposition = "retained"; resource.reason = "Scoped graph read still found this ID after deletion; operator review required";
    }
    const retainedIds = run.state.resources.filter(r => r.disposition === "retained").map(r => r.id);
    run.state.cleanup.residualIds = residualIds;
    run.state.cleanup.complete = !residualIds.length && !retainedIds.length; run.save();
    if (residualIds.length) throw new Error(`Recorded residual data remains: ${residualIds.join(", ")}`);
    return { complete: run.state.cleanup.complete, retainedIds, residualIds };
  } catch (error) {
    run.state.cleanup ??= { prepared: false, complete: false, residualIds: [] };
    run.state.cleanup.complete = false;
    run.state.cleanup.error = error instanceof Error ? error.message : String(error);
    try { run.save(); } catch (saveError) { throw new AggregateError([error, saveError], "Cleanup failed and state could not be saved. Preserve both errors."); }
    throw error;
  }
}

/** Common CLI modes keep inspection and cleanup independent of any model call. */
export async function runTutorialCommand(lesson: string, args: string[], seedMode: string,
  lessonModes: string[], action: (mode: string, run: TutorialRun) => Promise<unknown>,
  options: { preflight?: (mode: string) => void } = {}): Promise<void> {
  const [mode, path = `.tutorial-state/${lesson}.json`, retryPath] = args;
  const modes = [...lessonModes, "inspect", "verify", "cleanup", "retry-empty"];
  if (!mode || !modes.includes(mode)) throw new Error(`Use ${modes.join("|")} [state-file.json]; retry-empty requires old.json new.json.`);
  if (mode === "inspect") { console.log(JSON.stringify(inspectTutorialState(path, lesson), null, 2)); return; }
  const effectiveMode = mode === "retry-empty" ? seedMode : mode;
  if (lessonModes.includes(effectiveMode)) options.preflight?.(effectiveMode);
  if (mode === "retry-empty") {
    if (!retryPath || resolve(path) === resolve(retryPath)) throw new Error("retry-empty requires a distinct new state path; keep the original ledger.");
    const original = TutorialRun.load(path, undefined, lesson);
    try { original.assertUnstarted(); } finally { await original.client.close(); }
  }
  const run = mode === seedMode || mode === "retry-empty"
    ? TutorialRun.create(mode === "retry-empty" ? retryPath! : path, lesson)
    : TutorialRun.load(path, undefined, lesson);
  try {
    if (mode === "verify") console.log("Verified stored messages:", JSON.stringify(await run.verify()));
    else if (mode === "cleanup") {
      const result = await cleanupTutorial(run); console.log("Cleanup:", JSON.stringify(result)); if (!result.complete) process.exitCode = 2;
    } else await action(effectiveMode, run);
  } finally { await run.client.close(); }
}
