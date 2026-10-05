import { defineAgent } from "eve";
import { mockModel } from "eve/evals";

// A fake model with fixed replies, so no API key is needed.
export default defineAgent({
  defaultTools: false,
  // The fake model needs its size set by hand.
  modelContextWindowTokens: 128_000,
  model: mockModel({
    modelId: "nams-e2e",
    respond: ({ lastUserMessage, messages, toolResults }) => {
      const said = lastUserMessage ?? "";
      if (toolResults.length > 0) {
        return `tool ${toolResults[0].name} returned ${JSON.stringify(toolResults[0].output)}`;
      }
      if (said.startsWith("remember")) {
        return { toolCalls: [{ name: "nams__remember", input: { memory: "Prefers aisle seats" } }] };
      }
      if (said.startsWith("search")) {
        return { toolCalls: [{ name: "nams__search", input: { query: "aisle" } }] };
      }
      const recalled = messages.find((m) => m.text.includes("<nams_memory>"));
      return recalled ? `recalled: ${recalled.text}` : "nothing recalled";
    },
  }),
});
