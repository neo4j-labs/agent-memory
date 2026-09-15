import type { LanguageModelV4 } from "@ai-sdk/provider";
import { openai } from "@ai-sdk/openai";
import { generateText, wrapLanguageModel } from "ai";
import { agentMemoryMiddleware } from "@neo4j-labs/agent-memory/middleware/vercel-ai";
import { TutorialRun, isTutorialEntryPoint } from "../../../shared/tutorial-state.js";
import { runTutorialCommand } from "../../../shared/tutorial-cleanup.js";

export async function teach(run: TutorialRun) {
  const conversation = await run.createConversation();
  await run.addMessage("user", `My fictional running club is called ${run.name}. I wear size 10 shoes.`);
  await run.verify();
  console.log(`CONVERSATION_ID=${conversation.id}`);
  console.log(`Teaching message verified. Stop this process and keep ${run.path}.`);
  return conversation.id;
}

export async function recall(run: TutorialRun, baseModel: LanguageModelV4) {
  await run.verify();
  const before = await run.client.shortTerm.getConversation(run.conversationId);
  console.log(`Stored messages verified before this question: ${before.messages.length}`);
  const model = wrapLanguageModel({ model: baseModel,
    middleware: agentMemoryMiddleware(run.client, { conversationId: run.conversationId }) });
  // Neither the synthetic name nor the shoe size appears in this question.
  const prompt = "What is the exact full running club name I told you, including its identifier, and what shoe size did I tell you?";
  const restore = run.trackMiddlewareWrites();
  try {
    const { text } = await generateText({ model,
      system: "Answer from the supplied memory. Say when a requested fact is missing.", prompt });
    console.log(`Model returned: ${text}`);
    await run.verifyTurn(before.messages.map(m => m.id), prompt, text);
    console.log("New user and assistant messages verified in storage.");
    if (!text.includes(run.name) || !/\b10\b/.test(text)) throw new Error("Storage passed, but recall did not include the run-specific club and size.");
    console.log("Recall verified against the run-specific club and shoe size.");
    return text;
  } finally { restore(); }
}

if (isTutorialEntryPoint(import.meta.url)) {
  runTutorialCommand("conversation", process.argv.slice(2), "teach", ["teach", "recall"], (mode, run) => {
    if (mode === "teach") return teach(run);
    if (!process.env.OPENAI_API_KEY) throw new Error("Set OPENAI_API_KEY for recall; teach, inspect, verify and cleanup do not need it.");
    return recall(run, openai(process.env.OPENAI_MODEL ?? "gpt-4o-mini"));
  }).catch(error => { console.error(error); process.exitCode = 1; });
}
