/**
 * Two things are checked here:
 *
 *  1. Doc-page fidelity: each program is embedded byte-for-byte on its how-to
 *     page, exactly like `typescript/examples/vercel-ai/test/docs-tutorials.test.ts`.
 *     The pages are
 *     `docs/modules/ROOT/pages/how-to/typescript/nams-ai-provider-<mode>-mode.adoc`.
 *     Whenever a program and its page drift apart, this half of the suite
 *     fails with a diff; re-embed the program on the page after editing it.
 *
 *  2. Offline behavior: each program runs against the hosted-service stub
 *     shared with `typescript/examples/vercel-ai` and the AI SDK's own
 *     `MockLanguageModelV4` -- no API key, no network, no Neo4j. The
 *     assertions are the ones that fail if memory stops working: the second
 *     session/turn recalls (or explicitly queries and gets back) what the
 *     first one stored. Hooks mode checks the effect of each of the eight
 *     lifecycle events, not the mock model's canned answers.
 */
import { readFile } from "node:fs/promises";
import type {
  LanguageModelV4CallOptions,
  LanguageModelV4FinishReason,
  LanguageModelV4GenerateResult,
  LanguageModelV4Usage,
} from "@ai-sdk/provider";
import { MockLanguageModelV4 } from "ai/test";
import { afterEach, describe, expect, it, vi } from "vitest";
import { hooksModeDemo } from "../src/hooks-mode.js";
import { middlewareModeDemo } from "../src/middleware-mode.js";
import { providerModeDemo } from "../src/provider-mode.js";
import { toolsModeDemo } from "../src/tools-mode.js";
import { startHostedStub } from "../../vercel-ai/test/docs-hosted-stub.js";

const root = new URL("../../../../", import.meta.url);
const modes = ["provider", "middleware", "tools", "hooks"] as const;

const FINISH: LanguageModelV4FinishReason = { unified: "stop", raw: "stop" };
const TOOL_CALLS: LanguageModelV4FinishReason = { unified: "tool-calls", raw: "tool_calls" };
const USAGE: LanguageModelV4Usage = {
  inputTokens: { total: 10, noCache: 10, cacheRead: 0, cacheWrite: 0 },
  outputTokens: { total: 10, text: 10, reasoning: 0 },
};

function textResult(text: string): LanguageModelV4GenerateResult {
  return { content: [{ type: "text", text }], finishReason: FINISH, usage: USAGE, warnings: [] };
}

function toolCallResult(toolCallId: string, toolName: string, input: unknown): LanguageModelV4GenerateResult {
  // Real providers return tool arguments as a raw JSON string; the AI SDK
  // parses it with the tool's schema, so a plain object here is rejected.
  return {
    content: [{ type: "tool-call", toolCallId, toolName, input: JSON.stringify(input) }],
    finishReason: TOOL_CALLS,
    usage: USAGE,
    warnings: [],
  };
}

/** A mock model that returns the next answer in `answers` on each call, repeating the last one. */
function sequencedMockModel(answers: string[]) {
  const calls: LanguageModelV4CallOptions[] = [];
  let i = 0;
  const model = new MockLanguageModelV4({
    doGenerate: async options => {
      calls.push(options);
      const text = answers[i] ?? answers[answers.length - 1]!;
      i++;
      return textResult(text);
    },
  });
  return { model, calls };
}

function promptText(call: LanguageModelV4CallOptions): string {
  return JSON.stringify(call.prompt);
}

/** The current turn's user text: the *last* user-role message, since restored history carries earlier turns too. */
function currentTurnText(options: LanguageModelV4CallOptions): string {
  const users = options.prompt.filter(m => m.role === "user");
  return JSON.stringify(users.at(-1)?.content ?? "");
}

function toolResultCount(options: LanguageModelV4CallOptions): number {
  return options.prompt.filter(m => m.role === "tool").length;
}

async function withOfflineEnv<T>(stub: Awaited<ReturnType<typeof startHostedStub>>, run: () => Promise<T>): Promise<T> {
  process.env.MEMORY_API_KEY = "nams_offline";
  process.env.MEMORY_ENDPOINT = stub.endpoint;
  try {
    return await run();
  } finally {
    delete process.env.MEMORY_API_KEY;
    delete process.env.MEMORY_ENDPOINT;
  }
}

afterEach(() => {
  delete process.env.MEMORY_API_KEY;
  delete process.env.MEMORY_ENDPOINT;
});

describe("NAMS AI provider doc pages", () => {
  it.each(modes)("keeps the %s-mode program identical to its embedded doc page", async mode => {
    const doc = await readFile(
      new URL(`docs/modules/ROOT/pages/how-to/typescript/nams-ai-provider-${mode}-mode.adoc`, root),
      "utf8",
    );
    const source = await readFile(new URL(`../src/${mode}-mode.ts`, import.meta.url), "utf8");
    expect(doc).toContain(`[source,typescript]\n----\n${source}----`);
  });
});

describe("NAMS AI provider offline runs", () => {
  it("provider mode: teaches a fact in session 1 and recalls it in session 2 via NAMS", async () => {
    const stub = await startHostedStub();
    try {
      await withOfflineEnv(stub, async () => {
        const { model, calls } = sequencedMockModel([
          "Nice to meet you, Alex!",
          "You work at TechCorp, on the graph platform team.",
        ]);
        const { taught, recalled } = await providerModeDemo(() => model);

        expect(taught).toBe("Nice to meet you, Alex!");
        expect(recalled).toBe("You work at TechCorp, on the graph platform team.");
        // Session 2's prompt carries session 1's fact back out of NAMS -- the
        // script never passed it in a second time.
        expect(promptText(calls[1]!)).toContain("TechCorp");
      });

      expect(stub.calls).toContain("POST /conversations");
      // The query string isn't recorded in `calls` (pathname only) -- the
      // path itself is shared with the POST above, so check the method pairs up.
      expect(stub.calls).toContain("GET /conversations");
      expect(stub.conversations.size).toBe(1);
      expect([...stub.conversations.values()][0]!.messages).toHaveLength(4);
    } finally {
      await stub.close();
    }
  });

  it("middleware mode: teaches a fact in turn 1 and recalls it in turn 2 via NAMS", async () => {
    const stub = await startHostedStub();
    try {
      await withOfflineEnv(stub, async () => {
        const { model, calls } = sequencedMockModel([
          "Great choice! Rust is loved for its safety and performance.",
          "Your favourite programming language is Rust.",
        ]);
        const { taught, recalled } = await middlewareModeDemo(() => model);

        expect(taught).toContain("Rust");
        expect(recalled).toBe("Your favourite programming language is Rust.");
        expect(promptText(calls[1]!)).toContain("Rust");
      });

      expect(stub.calls).toContain("POST /conversations");
      expect(stub.conversations.size).toBe(1);
      expect([...stub.conversations.values()][0]!.messages).toHaveLength(4);
    } finally {
      await stub.close();
    }
  });

  it("tools mode: turn 1 stores a preference via store_memory, turn 2 recalls it via query_memory", async () => {
    const stub = await startHostedStub();
    try {
      const turn2Calls: LanguageModelV4CallOptions[] = [];
      await withOfflineEnv(stub, async () => {
        const model = new MockLanguageModelV4({
          doGenerate: async options => {
            const turn1 = currentTurnText(options).includes("Got any editor tips");
            const steps = toolResultCount(options);
            if (turn1) {
              if (steps === 0) return toolCallResult("call-q1", "query_memory", { query: "editor preferences", limit: 5 });
              if (steps === 1) {
                return toolCallResult("call-s1", "store_memory", {
                  content: "User's editor is Neovim, and they prefer short answers",
                  type: "user_preference",
                  confidence: 0.9,
                  tags: [],
                });
              }
              return textResult("Got it -- short answers, and I'll remember you use Neovim.");
            }
            // Turn 2: fresh tool set, same user -- recall only, no store_memory.
            // The stub's search is a substring match, so query a word the stored memory contains.
            turn2Calls.push(options);
            if (steps === 0) return toolCallResult("call-q2", "query_memory", { query: "editor", limit: 5 });
            return textResult("You use Neovim, and you like short answers.");
          },
        });

        const { taught, recalled } = await toolsModeDemo(() => model);
        expect(taught).toContain("Neovim");
        expect(recalled).toBe("You use Neovim, and you like short answers.");
      });

      // The canned answer above proves nothing on its own: turn 2's
      // query_memory call must have returned what turn 1 stored.
      const turn2ToolResults = turn2Calls.flatMap(call => call.prompt.filter(m => m.role === "tool"));
      expect(turn2ToolResults).toHaveLength(1);
      expect(JSON.stringify(turn2ToolResults[0])).toContain("query_memory");
      expect(JSON.stringify(turn2ToolResults[0])).toContain("Neovim");

      expect(stub.calls).toContain("POST /entities");
      expect(stub.calls.filter(c => c === "POST /entities/search").length).toBeGreaterThanOrEqual(2);
      expect(stub.entities.size).toBe(1);
      expect([...stub.entities.values()][0]!["name"]).toContain("Neovim");
    } finally {
      await stub.close();
    }
  });

  it("hooks mode: all eight lifecycle events take effect and the transcript persists via bulk write", async () => {
    const stub = await startHostedStub();
    const CARD = "4111 1111 1111 1111";
    // The model calls of each turn, keyed by the turn's user text.
    const turnCalls = new Map<string, LanguageModelV4CallOptions[]>();
    const turnOf = (options: LanguageModelV4CallOptions): string => {
      const text = currentTurnText(options);
      if (text.includes("weather in Oslo")) return "weather";
      if (text.includes("graph databases")) return "flaky";
      if (text.includes("delete my account")) return "delete";
      return "card";
    };
    const logs = vi.spyOn(console, "log").mockImplementation(() => {});
    const warnings = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const answers = await withOfflineEnv(stub, async () => {
        const model = new MockLanguageModelV4({
          doGenerate: async options => {
            const turn = turnOf(options);
            turnCalls.set(turn, [...(turnCalls.get(turn) ?? []), options]);
            const steps = toolResultCount(options);
            if (turn === "weather") {
              return steps === 0 ? toolCallResult("call-weather", "get_weather", { city: "Oslo" }) : textResult("It's sunny in Oslo, 21C.");
            }
            if (turn === "flaky") {
              return steps === 0 ? toolCallResult("call-flaky", "flaky_lookup", { topic: "graph databases" }) : textResult("Graph databases are well documented.");
            }
            if (turn === "delete") {
              return steps === 0 ? toolCallResult("call-delete", "delete_account", { confirm: true }) : textResult("I can't do that -- account deletion is disabled in this demo.");
            }
            // Turn 4 is answered directly. The mock echoes the raw card number
            // (a real model never sees it) so PreMemoryWrite has something to redact.
            return textResult(`Got it, but I can't store card numbers like ${CARD}.`);
          },
        });
        return hooksModeDemo(() => model);
      });
      const system = (turn: string): string => JSON.stringify(turnCalls.get(turn)![0]!.prompt.filter(m => m.role === "system"));
      const toolResults = (turn: string): string =>
        JSON.stringify(turnCalls.get(turn)!.flatMap(call => call.prompt.filter(m => m.role === "tool")));
      const logged = logs.mock.calls.map(args => args.join(" "));
      const warned = warnings.mock.calls.map(args => args.join(" "));

      expect(answers).toHaveLength(4);
      expect([...turnCalls.keys()]).toEqual(["weather", "flaky", "delete", "card"]);

      // SessionStart: its note reaches the first turn's instructions, and only the first.
      expect(system("weather")).toContain("free plan");
      expect(system("flaky")).not.toContain("free plan");
      // PostToolUse: the get_weather note is carried into the next turn.
      expect(system("flaky")).toContain("Last weather lookup");
      // PostToolUseFailure: flaky_lookup failed once, was retried, and the model got the fact.
      expect(warned.some(line => line.includes("flaky_lookup failed once, retrying."))).toBe(true);
      expect(toolResults("flaky")).toContain("graph databases is well documented");
      // PreToolUse: delete_account was denied, so the model got the block, never the tool's output.
      expect(toolResults("delete")).toContain('"blocked":true');
      expect(toolResults("delete")).toContain("Account deletion is disabled in this demo.");
      expect(toolResults("delete")).not.toContain("deleted");
      // UserPromptSubmit: the model never sees the card number.
      expect(JSON.stringify(turnCalls.get("card"))).not.toContain(CARD);
      expect(currentTurnText(turnCalls.get("card")![0]!)).toContain("[redacted card]");
      expect(warned.some(line => line.includes("Redacted a card number."))).toBe(true);
      // Stop logs once per turn; SessionEnd once, on shutdown.
      expect(logged.filter(line => line.includes("[hook] saved"))).toHaveLength(4);
      expect(logged.filter(line => line.includes("[hook] session ended (demo_complete)"))).toHaveLength(1);

      expect(stub.calls.some(c => c.endsWith("/messages/bulk"))).toBe(true);
      expect(stub.conversations.size).toBe(1);
      const stored = JSON.stringify([...stub.conversations.values()][0]!.messages);
      // PreMemoryWrite: the assistant's echo of the card number is redacted before storage.
      expect(stored).not.toContain(CARD);
      expect(stored).toContain("store card numbers like [redacted card]");
      // The persisted transcript records the denial and the retried tool's result.
      expect(stored).toContain("[tool-result] delete_account");
      expect(stored).toMatch(/\[tool-result\] delete_account: \{\\?"blocked\\?":true/);
      expect(stored).toContain("graph databases is well documented");
    } finally {
      logs.mockRestore();
      warnings.mockRestore();
      await stub.close();
    }
  });
});
