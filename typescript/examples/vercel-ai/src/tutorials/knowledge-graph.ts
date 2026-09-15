import { TutorialRun, isTutorialEntryPoint } from "../../../shared/tutorial-state.js";
import { inspectGraph, runTutorialCommand, waitForTerminalExtraction } from "../../../shared/tutorial-cleanup.js";

export async function inspectDocuments(run: TutorialRun, timeoutMs = 60_000) {
  const conversation = await run.createConversation();
  console.log(`CONVERSATION_ID=${conversation.id}`);
  await run.bulkAddMessages([
    { role: "user", content: `${run.name} is a fictional organization that designs garden sensors.` },
    { role: "user", content: `Mira Vale ${run.state.runId} is an engineer at ${run.name}.` },
  ]);
  const stored = await run.verify();
  console.log(`Stored document messages verified: ${stored.messages}`);
  await waitForTerminalExtraction(run, timeoutMs);
  const graph = await inspectGraph(run);
  const entityIds = [...new Set(graph.map(row => String(row.entity_id)))];
  let expectedNameFound = false;
  for (const id of entityIds) {
    // These IDs came from the owned messages' provenance edges, not workspace search.
    run.retain({ kind: "entity", id, origin: "derived", disposition: "present" });
    const entity = await run.client.longTerm.getEntity(id);
    expectedNameFound ||= entity.name.toLowerCase() === run.name.toLowerCase();
    console.log(`Linked entity: ${entity.id} ${entity.name} (${entity.type})`);
  }
  console.log(`Run graph: ${graph.length} message/entity links`);
  if (!entityIds.length) throw new Error("Extraction finished without linked entities. Keep the state and inspect the messages before claiming graph success.");
  if (!expectedNameFound) throw new Error("Linked entities did not include this run's expected organization name. Keep the state; graph presence alone is not the lesson's success condition.");
  return { conversationId: conversation.id, entityIds };
}

if (isTutorialEntryPoint(import.meta.url)) {
  runTutorialCommand("knowledge-graph", process.argv.slice(2), "seed", ["seed"],
    (_mode, run) => inspectDocuments(run)).catch(error => { console.error(error); process.exitCode = 1; });
}
