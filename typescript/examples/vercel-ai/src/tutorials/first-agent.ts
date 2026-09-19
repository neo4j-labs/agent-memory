import type { LanguageModelV4 } from "@ai-sdk/provider";
import { openai } from "@ai-sdk/openai";
import { generateText, wrapLanguageModel } from "ai";
import { agentMemoryMiddleware } from "@neo4j-labs/agent-memory/middleware/vercel-ai";
import { TutorialRun, isTutorialEntryPoint } from "../../../shared/tutorial-state.js";
import { runTutorialCommand } from "../../../shared/tutorial-cleanup.js";

export async function firstAgent(run: TutorialRun, baseModel: LanguageModelV4) {
  const conversation = await run.createConversation();
  console.log(`CONVERSATION_ID=${conversation.id}`);
  const entity = await run.addEntity();
  const storedEntity = await run.client.longTerm.getEntity(entity.id);
  console.log(`ENTITY_ID=${storedEntity.id} name=${storedEntity.name}`);
  const message = await run.addMessage("user", `My fictional project is called ${run.name}.`);
  console.log(`Stored message: ${message.id}`);
  const matches = await run.client.shortTerm.searchMessages(run.name, { conversationId: conversation.id, limit: 5 });
  console.log(`Conversation search matches: ${matches.length}`);

  await run.verify();
  const before = await run.client.shortTerm.getConversation(conversation.id);
  const prompt = "What is the exact full project name I told you, including its identifier?";
  const model = wrapLanguageModel({ model: baseModel,
    middleware: agentMemoryMiddleware(run.client, { conversationId: conversation.id }) });
  const restore = run.trackMiddlewareWrites();
  try {
    const answer = await generateText({ model,
      system: "Use the supplied conversation history. If a fact is missing, say so.", prompt });
    console.log(`Model returned: ${answer.text}`);
    await run.verifyTurn(before.messages.map(m => m.id), prompt, answer.text);
    console.log("New user and assistant messages verified in storage.");
    if (!answer.text.includes(run.name)) throw new Error("Storage passed, but the model did not recall the run-specific project name.");
    console.log("Recall verified against the run-specific project name.");
    return { conversationId: conversation.id, entityId: entity.id, answer: answer.text };
  } finally { restore(); }
}

export async function firstAgentCli(args: string[]) {
  await runTutorialCommand("first-agent", args, "seed", ["seed"],
    (_mode, run) => firstAgent(run, openai(process.env.OPENAI_MODEL ?? "gpt-4o-mini")), {
      preflight: () => {
        if (!process.env.OPENAI_API_KEY?.trim()) throw new Error("Set OPENAI_API_KEY before seed or retry-empty. No state or records were created; rerun after setting it.");
      },
    });
}

if (isTutorialEntryPoint(import.meta.url)) {
  firstAgentCli(process.argv.slice(2)).catch(error => { console.error(error); process.exitCode = 1; });
}
