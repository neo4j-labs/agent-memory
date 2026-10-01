import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { MockLanguageModelV4 } from "ai/test";
import { afterEach, describe, expect, it, vi } from "vitest";
import { firstAgent } from "../src/tutorials/first-agent.js";
import { hostedQuickstart } from "../src/tutorials/hosted-quickstart.js";
import { inspectDocuments } from "../src/tutorials/knowledge-graph.js";
import { cleanupTutorial } from "../../shared/tutorial-cleanup.js";
import { TutorialRun } from "../../shared/tutorial-state.js";
import { startHostedStub } from "./docs-hosted-stub.js";

const exec = promisify(execFile);
const root = new URL("../../../../", import.meta.url);
const pairs = [["first-agent-memory-typescript", "first-agent"], ["conversation-memory-typescript", "conversation"],
  ["hosted-quickstart-typescript", "hosted-quickstart"], ["knowledge-graph-typescript", "knowledge-graph"]] as const;
afterEach(() => vi.restoreAllMocks());

describe("complete authored TypeScript lessons", () => {
  it.each(pairs)("keeps the %s program identical to its compiled executable", async (page, program) => {
    const doc = await readFile(new URL(`docs/modules/ROOT/pages/tutorials/${page}.adoc`, root), "utf8");
    const source = await readFile(new URL(`../src/tutorials/${program}.ts`, import.meta.url), "utf8");
    expect(doc).toContain(`[source,typescript]\n----\n${source}----`);
  });

  it("runs storage, scoped search, a verified model turn and cleanup without reasoning writes", async () => {
    const stub = await startHostedStub();
    const directory = await mkdtemp(join(tmpdir(), "tutorial-programs-"));
    vi.spyOn(console, "log").mockImplementation(() => {});
    const runs = ["hosted-quickstart", "first-agent"].map(lesson => TutorialRun.create(join(directory, `${lesson}.json`), lesson, { endpoint: stub.endpoint, apiKey: "nams_offline" }));
    try {
      const quickId = await hostedQuickstart(runs[0]!);
      expect(stub.conversations.get(quickId)?.messages).toHaveLength(1);
      const model = new MockLanguageModelV4({ doGenerate: async options => {
        const prompt = JSON.stringify(options.prompt);
        expect(prompt).toContain(runs[1]!.name);
        return { content: [{ type: "text", text: runs[1]!.name }], finishReason: { unified: "stop", raw: "stop" },
          usage: { inputTokens: { total: 10, noCache: 10, cacheRead: 0, cacheWrite: 0 }, outputTokens: { total: 10, text: 10, reasoning: 0 } }, warnings: [] };
      } });
      const result = await firstAgent(runs[1]!, model);
      expect(stub.conversations.get(result.conversationId)?.messages).toHaveLength(3);
      expect(stub.calls).toContain(`POST /conversations/${result.conversationId}/search`);
      for (const run of runs) expect((await cleanupTutorial(run)).complete).toBe(true);
      expect(stub.conversations.size).toBe(0); expect(stub.entities.size).toBe(0);
      expect(stub.calls.some(call => /reasoning|\/entities\/(graph|search)|preference|fact/.test(call))).toBe(false);
    } finally { for (const run of runs) await run.client.close(); await stub.close(); await rm(directory, { recursive: true, force: true }); }
  });

  it("teaches, recalls, independently verifies and cleans up in four new processes", async () => {
    const stub = await startHostedStub();
    const directory = await mkdtemp(join(tmpdir(), "tutorial-restart-"));
    const path = join(directory, "run.json");
    const child = fileURLToPath(new URL("docs-restart-child.ts", import.meta.url));
    const invoke = (mode: string) => exec(process.execPath, ["--import", "tsx", child, mode, stub.endpoint, path]);
    try {
      const taught = await invoke("teach");
      const id = taught.stdout.match(/CONVERSATION_ID=(\S+)/)![1]!;
      expect(stub.conversations.get(id)!.messages).toHaveLength(1);
      const recalled = await invoke("recall");
      expect(recalled.stdout).toContain("New user and assistant messages verified in storage.");
      const messages = stub.conversations.get(id)!.messages;
      expect(messages).toHaveLength(3);
      expect(String(messages[1]!.content)).not.toContain("Lantern Orchard");
      expect(String(messages[1]!.content)).not.toContain("10");
      const before = stub.requests.length;
      expect((await invoke("verify")).stdout).toContain('"messages":3');
      expect(stub.requests.slice(before).every(r => r.method === "GET")).toBe(true);
      expect((await invoke("cleanup")).stdout).toContain('"complete":true');
    } finally { await stub.close(); await rm(directory, { recursive: true, force: true }); }
  }, 20_000);

  it("queries only run message IDs and keeps IDs after a bounded extraction failure", async () => {
    const stub = await startHostedStub();
    const directory = await mkdtemp(join(tmpdir(), "tutorial-graph-"));
    const create = (name: string) => TutorialRun.create(join(directory, `${name}.json`), "knowledge-graph", { endpoint: stub.endpoint, apiKey: "nams_offline" });
    const ready = create("ready"); const pending = create("pending");
    vi.spyOn(console, "log").mockImplementation(() => {});
    try {
      const found = await inspectDocuments(ready, 1_000);
      expect(found.entityIds).toHaveLength(1);
      expect(stub.conversations.get(found.conversationId)?.messages).toHaveLength(2);
      expect(stub.requests.filter(r => r.path === "/query").every(r => JSON.stringify(r.body).includes("message-"))).toBe(true);
      stub.behavior.extraction = "pending";
      await expect(inspectDocuments(pending, 0)).rejects.toThrow("deadline expired");
      expect(pending.state.resources.filter(r => r.kind === "message")).toHaveLength(2);
      expect(stub.calls.some(call => /DELETE|\/entities\/(search|graph)/.test(call))).toBe(false);
    } finally { await ready.client.close(); await pending.client.close(); await stub.close(); await rm(directory, { recursive: true, force: true }); }
  });
});
