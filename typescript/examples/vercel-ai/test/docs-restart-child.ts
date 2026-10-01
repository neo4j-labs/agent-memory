/** Each command is a fresh process with no in-memory conversation state. */
import { MockLanguageModelV4 } from "ai/test";
import { recall, teach } from "../src/tutorials/conversation.js";
import { TutorialRun } from "../../shared/tutorial-state.js";
import { cleanupTutorial } from "../../shared/tutorial-cleanup.js";

const [mode, endpoint, path] = process.argv.slice(2);
const config = { endpoint: endpoint!, apiKey: "nams_offline_docs" };
const run = mode === "teach" ? TutorialRun.create(path!, "conversation", config) : TutorialRun.load(path!, config, "conversation");
try {
  if (mode === "teach") await teach(run);
  else if (mode === "verify") console.log(JSON.stringify(await run.verify()));
  else if (mode === "cleanup") console.log(JSON.stringify(await cleanupTutorial(run)));
  else await recall(run, new MockLanguageModelV4({ doGenerate: async options => {
    const prompt = JSON.stringify(options.prompt);
    const name = prompt.match(/Lantern Orchard [a-f0-9-]{36}/)?.[0];
    if (!name || !prompt.includes("size 10")) throw new Error("The new process did not retrieve the teaching message.");
    return { content: [{ type: "text", text: `${name}, size 10.` }], finishReason: { unified: "stop", raw: "stop" },
      usage: { inputTokens: { total: 10, noCache: 10, cacheRead: 0, cacheWrite: 0 }, outputTokens: { total: 10, text: 10, reasoning: 0 } }, warnings: [] };
  } }));
} finally { await run.client.close(); }
