/** Explicitly record returned Desktop tool IDs; no hidden interception or reseeding. */
import { readFileSync } from "node:fs";
import { cleanupTutorial } from "./tutorial-cleanup.js";
import { MemoryClient } from "@neo4j-labs/agent-memory";
import { TutorialRun, inspectTutorialState, isTutorialEntryPoint, resolveTutorialConfig } from "./tutorial-state.js";

export async function recordMcpResult(run: TutorialRun, kind: string, id: string, parentId?: string, createReceipt?: unknown): Promise<void> {
  if (run.state.cleanup) throw new Error("Cleanup already began; preserve the existing ledger.");
  if (!id) throw new Error("Supply the actual ID returned by the tool.");
  if (kind === "conversation") {
    if (run.state.resources.some(r => r.kind === "conversation" && r.id !== id)) throw new Error("This ledger already records another conversation.");
    const c = await run.client.shortTerm.getConversationMetadata(id);
    if (c.userId !== run.userId || c.metadata?.run !== run.state.runId || !Number.isFinite(Date.parse(c.createdAt)) || Date.parse(c.createdAt) < Date.parse(run.state.createdAt)) throw new Error("Returned conversation does not have this run's ownership/creation evidence.");
    run.retain({ kind, id, createdAt: c.createdAt, disposition: "present" });
  } else if (kind === "message") {
    if (parentId !== run.conversationId) throw new Error("Supply the recorded conversation ID for the message.");
    await run.ownedConversation();
    const conversation = await run.client.shortTerm.getConversation(parentId);
    const message = conversation.messages.find(m => m.id === id);
    if (!message || !Number.isFinite(Date.parse(message.timestamp)) || Date.parse(message.timestamp) < Date.parse(run.state.createdAt)) throw new Error("Message ID is not a newly created message in this run's conversation.");
    run.recordMessage(message, parentId);
  } else if (kind === "entity") {
    const entity = await run.client.longTerm.getEntity(id);
    // The SDK adds merge evidence to the create result; a later GET can omit it.
    // Retain the exact create-result evidence rather than inferring non-merge from GET.
    const receipt = createReceipt as { id?: unknown; name?: unknown; metadata?: Record<string, unknown> } | undefined;
    const validReceipt = receipt?.id === id && receipt.name === entity.name &&
      (receipt.metadata === undefined || (receipt.metadata !== null && typeof receipt.metadata === "object" && !Array.isArray(receipt.metadata)));
    const merged = validReceipt ? Boolean(receipt.metadata?.nams_resolution) : undefined;
    const created = Date.parse(entity.createdAt);
    const ownedName = entity.name.includes(run.state.runId);
    run.retain({ kind, id, name: entity.name, createdAt: entity.createdAt, origin: "recorded",
      merged, disposition: validReceipt && !merged && ownedName && Number.isFinite(created) && created >= Date.parse(run.state.createdAt) ? "present" : "retained",
      reason: validReceipt ? "Create-result merge evidence recorded; cleanup must verify server provenance before deletion" : "Create-result merge evidence missing or mismatched; retain this entity" });
  } else throw new Error("Record only conversation, message, or entity IDs; no other resource has supported tutorial cleanup.");
}

export async function tutorialMcpCli(args: string[]): Promise<void> {
  const [mode, path, kind, id, parentId] = args;
  if (mode === "check-access") {
    const client = new MemoryClient({ ...resolveTutorialConfig(), transport: "rest", timeout: 30_000 });
    try {
      await client.shortTerm.listConversations({ limit: 1 });
      console.log("NAMS authenticated read succeeded; no records were created or printed.");
    } finally { await client.close(); }
    return;
  }
  if (!path) throw new Error("Use init|inspect|record|verify|cleanup <state-file.json> [kind id parent-id-or-create-result.json].");
  if (mode === "inspect") { console.log(JSON.stringify(inspectTutorialState(path, "mcp"), null, 2)); return; }
  if (!["init", "record", "verify", "cleanup"].includes(mode ?? "")) throw new Error("Use check-access, init, inspect, record, verify, or cleanup.");
  const run = mode === "init" ? TutorialRun.create(path, "mcp") : TutorialRun.load(path, undefined, "mcp");
  try {
    if (mode === "init") console.log(JSON.stringify(run.inspect(), null, 2));
    else if (mode === "record") {
      const receipt = kind === "entity" && parentId ? JSON.parse(readFileSync(parentId, "utf8")) as unknown : undefined;
      await recordMcpResult(run, kind!, id!, kind === "message" ? parentId : undefined, receipt);
      console.log(`Recorded ${kind} ID ${id}`);
    }
    else if (mode === "verify") console.log("Verified stored messages:", JSON.stringify(await run.verify()));
    else if (mode === "cleanup") {
      const result = await cleanupTutorial(run); console.log("Cleanup:", JSON.stringify(result)); if (!result.complete) process.exitCode = 2;
    } else throw new Error("Use init, inspect, record, verify, or cleanup.");
  } finally { await run.client.close(); }
}
if (isTutorialEntryPoint(import.meta.url)) {
  tutorialMcpCli(process.argv.slice(2)).catch(error => { console.error(error); process.exitCode = 1; });
}
