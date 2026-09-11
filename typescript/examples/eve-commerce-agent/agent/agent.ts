/**
 * Runtime config for the root agent.
 *
 * Two credential paths, because eve supports both:
 *
 *   - `AI_GATEWAY_API_KEY` (or a linked Vercel project) + a gateway model id
 *     string — the default, nothing else to install.
 *   - `OPENAI_API_KEY` + the `@ai-sdk/openai` provider — used automatically when
 *     that key is set, which keeps this example runnable with the same key as
 *     the rest of the repo's TypeScript examples.
 *
 * A provider-authored `LanguageModel` (the second path) keeps this file a runtime
 * entry rather than a compile-only constant; both forms are supported by
 * `defineAgent`.
 */

import { createOpenAI } from "@ai-sdk/openai";
import { defineAgent } from "eve";

/** Vercel AI Gateway model id, used when OPENAI_API_KEY is not set. */
const GATEWAY_MODEL = process.env.EVE_MODEL ?? "openai/gpt-5.4-mini";

/** Direct-provider model id, used when OPENAI_API_KEY is set. */
const OPENAI_MODEL = process.env.OPENAI_MODEL ?? "gpt-5-mini";

const useOpenAIDirectly =
  process.env.OPENAI_API_KEY !== undefined && process.env.OPENAI_API_KEY !== "";

export default defineAgent({
  model: useOpenAIDirectly ? createOpenAI()(OPENAI_MODEL) : GATEWAY_MODEL,

  // Least privilege: a shopping assistant has no business running shell
  // commands, writing files or browsing the web. `defaultTools: false` drops
  // every optional framework tool; `agent/tools/load_skill.ts` and
  // `agent/tools/ask_question.ts` add back the two it does need.
  defaultTools: false,

  limits: {
    // A demo should not be able to run up a bill unattended.
    maxTokenCostUsdPerSession: 1,
  },
});
