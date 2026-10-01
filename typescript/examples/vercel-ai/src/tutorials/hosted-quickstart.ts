import { TutorialRun, isTutorialEntryPoint } from "../../../shared/tutorial-state.js";
import { runTutorialCommand } from "../../../shared/tutorial-cleanup.js";

export async function hostedQuickstart(run: TutorialRun) {
  const conversation = await run.createConversation();
  console.log(`CONVERSATION_ID=${conversation.id}`);
  await run.addMessage("user", `I am planning the fictional ${run.name} project.`);
  const stored = await run.verify();
  const context = await run.client.shortTerm.getContext(conversation.id);
  console.log(`Stored messages verified: ${stored.messages}`);
  console.log(`Recent context messages: ${context.recentMessages.length}`);
  console.log(`Keep run state: ${run.path}`);
  return conversation.id;
}

if (isTutorialEntryPoint(import.meta.url)) {
  runTutorialCommand("hosted-quickstart", process.argv.slice(2), "seed", ["seed"],
    (_mode, run) => hostedQuickstart(run)).catch(error => { console.error(error); process.exitCode = 1; });
}
