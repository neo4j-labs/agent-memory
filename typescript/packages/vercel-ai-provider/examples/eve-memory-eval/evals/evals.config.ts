import { defineEvalConfig } from "eve/evals";
import { startFakeNams } from "./fake-nams.js";

export default defineEvalConfig({
  // Start the fake NAMS server before the agent starts.
  async setup() {
    const nams = await startFakeNams();
    process.env.MEMORY_ENDPOINT = nams.endpoint;
    process.env.MEMORY_API_KEY = "eval-key";
    return { nams };
  },
  async teardown(context) {
    await context?.nams.close();
  },
});
