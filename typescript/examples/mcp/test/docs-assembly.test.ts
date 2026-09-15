/** Compile the complete authored server and exercise real stdio against an offline REST fixture. */
import { execFile } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile, copyFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { expect, it } from "vitest";
import { startHostedStub } from "../../vercel-ai/test/docs-hosted-stub.js";

const exec = promisify(execFile);
const sdkRoot = fileURLToPath(new URL("../../../", import.meta.url));
const names = ["memory_create_conversation", "memory_add_messages", "memory_get_context", "memory_search_messages", "memory_get_entity", "memory_add_entity"];

it("assembles base/custom/restricted lessons, records tool IDs immediately, restarts and verifies scoped cleanup", async () => {
  const page = await readFile(new URL("../../../../docs/modules/ROOT/pages/tutorials/mcp-server-typescript.adoc", import.meta.url), "utf8");
  const blocks = [...page.matchAll(/\[source,(typescript|json)\]\n----\n([\s\S]*?)\n----/g)].map(match => match[2]!);
  const source = blocks.find(block => block.includes("async function main()"))!;
  const config = blocks.find(block => block.includes('"compilerOptions"'))!;
  const customImports = blocks.find(block => block.startsWith('import { isAbsolute }'))!;
  const custom = blocks.find(block => block.includes('"memory_tutorial_graph"'))!;
  const restricted = blocks.find(block => block.startsWith("const toolNames ="))!;
  expect(source).toBeDefined(); expect(config).toBeDefined(); expect(custom).toBeDefined();
  const directory = await mkdtemp(join(tmpdir(), "agent-memory-mcp-lesson-"));
  const stub = await startHostedStub();
  const path = join(directory, ".tutorial-state", "mcp.json");
  const env = { MEMORY_API_KEY: "nams_offline_docs", MEMORY_ENDPOINT: stub.endpoint, MEMORY_WORKSPACE_ID: "offline-workspace", TUTORIAL_STATE_PATH: path };
  const cli = (...args: string[]) => exec(process.execPath, [join(directory, "dist", "tutorial-mcp.js"), ...args], { env });
  let conversationId = "";
  let messageId = "";
  let entityId = "";
  let userId = "";
  let runId = "";
  try {
    await mkdir(join(directory, "src"));
    await mkdir(join(directory, "node_modules", "@neo4j-labs"), { recursive: true });
    await symlink(sdkRoot, join(directory, "node_modules", "@neo4j-labs", "agent-memory"));
    for (const name of ["@modelcontextprotocol", "@types", "zod"]) await symlink(join(sdkRoot, "node_modules", name), join(directory, "node_modules", name));
    for (const name of ["tutorial-state", "tutorial-cleanup", "tutorial-mcp"]) await copyFile(join(sdkRoot, "examples", "shared", `${name}.ts`), join(directory, "src", `${name}.ts`));
    await writeFile(join(directory, "package.json"), JSON.stringify({ type: "module" }));
    await writeFile(join(directory, "tsconfig.json"), config);
    for (const variant of ["base", "custom", "restricted"] as const) {
      const registration = /  const toolNames = registerMemoryTools\(server, memory, \{[\s\S]*?\n  \}\);/;
      const assembled = variant === "custom" ? customImports + "\n" + source.replace("  const transport =", custom + "\n  const transport =")
        : variant === "restricted" ? source.replace(registration, restricted) : source;
      expect(assembled).toContain(variant === "custom" ? "memory_tutorial_graph" : "registerMemoryTools");
      await writeFile(join(directory, "src", "server.ts"), assembled);
      await exec(process.execPath, [join(sdkRoot, "node_modules", "typescript", "bin", "tsc"), "-p", join(directory, "tsconfig.json")]);
      if (variant === "base") {
        const initialized = JSON.parse((await cli("init", path)).stdout);
        userId = initialized.userId; runId = initialized.runId;
      }
      const client = new Client({ name: "docs-check", version: "1.0.0" });
      const transport = new StdioClientTransport({ command: process.execPath, args: [join(directory, "dist", "server.js")], env, stderr: "pipe" });
      const call = async (name: string, args: Record<string, unknown>) => {
        const result = await client.callTool({ name, arguments: args });
        expect(result.isError).not.toBe(true);
        const content = result.content as Array<{ type: string; text: string }>;
        return JSON.parse(content[0]!.text);
      };
      try {
        await client.connect(transport);
        const { tools } = await client.listTools();
        const expected = variant === "base" ? names : variant === "custom" ? [...names, "memory_tutorial_graph"] : ["memory_get_context", "memory_search_messages"];
        expect(tools.map(tool => tool.name).sort()).toEqual([...expected].sort());
        if (variant === "base") {
          conversationId = (await call("memory_create_conversation", { user_id: userId, metadata: { run: runId } })).id;
          await cli("record", path, "conversation", conversationId);
          const createdEntity = await call("memory_add_entity", { name: `Lantern Orchard ${runId}`, type: "organization" });
          entityId = createdEntity.id;
          const receiptPath = join(directory, ".tutorial-state", "entity-result.json");
          await writeFile(receiptPath, JSON.stringify(createdEntity), { mode: 0o600 });
          await cli("record", path, "entity", entityId, receiptPath);
          messageId = (await call("memory_add_messages", { conversation_id: conversationId, messages: [{ role: "user", content: `Fictional project Lantern Orchard ${runId}.` }] }))[0].id;
          await cli("record", path, "message", messageId, conversationId);
          expect((await cli("verify", path)).stdout).toContain('"messages":1');
        } else {
          const context = await call("memory_get_context", { conversation_id: conversationId });
          expect(context.recentMessages.map((m: { id: string }) => m.id)).toContain(messageId);
          const matches = await call("memory_search_messages", { conversation_id: conversationId, query: "project", limit: 5 });
          expect(matches.map((m: { id: string }) => m.id)).toEqual([messageId]);
          if (variant === "custom") {
            await call("memory_tutorial_graph", {});
            const query = stub.requests.filter(r => r.path === "/query").at(-1)!;
            expect((query.body.params as { ids: string[] }).ids).toEqual([messageId]);
          } else {
            const rejected = await client.callTool({ name: "memory_create_conversation", arguments: { user_id: userId } });
            expect(rejected.isError).toBe(true);
          }
        }
      } finally { await client.close(); }
    }
    expect((await cli("cleanup", path)).stdout).toContain('"complete":true');
    expect(stub.conversations.size).toBe(0); expect(stub.entities.size).toBe(0);
    expect(stub.calls.some(call => /reasoning|\/entities\/(graph|search)/.test(call))).toBe(false);
    expect(stub.requests.every(r => r.authorization === "Bearer nams_offline_docs" && r.workspace === "offline-workspace")).toBe(true);
    expect((await cli("cleanup", path)).stdout).toContain('"complete":true');
  } finally { await stub.close(); await rm(directory, { recursive: true, force: true }); }
}, 30_000);
