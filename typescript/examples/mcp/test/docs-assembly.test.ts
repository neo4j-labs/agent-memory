/**
 * Compile the tutorial's base server plus the how-to's custom/restricted recipes, and exercise real stdio against an offline REST fixture.
 *
 * The temporary project follows the page's install. By default it stays offline: the SDK is `npm pack`ed and unpacked into
 * `node_modules` the way a registry install lays it out (no nested `node_modules`, so it shares the project's single `zod`), and
 * the page's other packages are linked in from the SDK checkout. Set `DOCS_READER_INSTALL=1` to run the page's own `npm init` and
 * `npm install` commands against the registry instead, which checks the pinned published release exactly as a reader installs it.
 */
import { execFile } from "node:child_process";
import { mkdtemp, mkdir, readFile, readdir, rename, rm, symlink, writeFile, copyFile } from "node:fs/promises";
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
const readerInstall = process.env.DOCS_READER_INSTALL === "1";
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const names = ["memory_create_conversation", "memory_add_messages", "memory_get_context", "memory_search_messages", "memory_get_entity", "memory_add_entity"];

it("assembles base/custom/restricted lessons, records tool IDs immediately, restarts and verifies scoped cleanup", async () => {
  const blockPattern = /\[source,(typescript|json)\]\n----\n([\s\S]*?)\n----/g;
  const tutorialPage = await readFile(new URL("../../../../docs/modules/ROOT/pages/tutorials/mcp-server-typescript.adoc", import.meta.url), "utf8");
  const howToPage = await readFile(new URL("../../../../docs/modules/ROOT/pages/how-to/typescript/mcp.adoc", import.meta.url), "utf8");
  const tutorialBlocks = [...tutorialPage.matchAll(blockPattern)].map(match => match[2]!);
  const howToBlocks = [...howToPage.matchAll(blockPattern)].map(match => match[2]!);
  const source = tutorialBlocks.find(block => block.includes("async function main()"))!;
  const config = tutorialBlocks.find(block => block.includes('"compilerOptions"'))!;
  const customImports = howToBlocks.find(block => block.startsWith('import { isAbsolute }'))!;
  const custom = howToBlocks.find(block => block.includes('"memory_tutorial_graph"'))!;
  const restricted = howToBlocks.find(block => block.startsWith("const toolNames ="))!;
  const json = tutorialBlocks.filter(block => block.trim().startsWith("{")).map(block => JSON.parse(block));
  const packageFields = json.find(value => value.scripts?.build && value.type === "module");
  expect(packageFields).toBeDefined();
  // The page's setup block: it must install the published SDK (a `../typescript` link resolves a second `zod`
  // from the checkout and breaks `npm run build`), plus the MCP SDK and zod that share one copy with it.
  const setup = [...tutorialPage.matchAll(/\[source,bash\]\n----\n([\s\S]*?)\n----/g)].map(match => match[1]!).find(block => block.includes("npm init -y"))!;
  const installs = setup.split("\n").filter(line => /^npm (init|install)\b/.test(line));
  const runtimeInstall = installs.find(line => line.startsWith("npm install ") && !line.includes("--save-dev"))!;
  const sdkSpec = runtimeInstall.split(/\s+/).find(arg => arg.startsWith("@neo4j-labs/agent-memory@"));
  expect(sdkSpec, "the lesson installs a pinned published SDK release").toMatch(/^@neo4j-labs\/agent-memory@\d+\.\d+\.\d+$/);
  expect(runtimeInstall).not.toContain("../typescript");
  expect(runtimeInstall).toMatch(/@modelcontextprotocol\/sdk@/);
  expect(runtimeInstall).toMatch(/ zod@/);
  const conversationArgs = json.find(value => value.user_id === "<printed userId>");
  const entityArgs = json.find(value => value.name === "<printed entityName>");
  const messageArgs = json.find(value => value.conversation_id === "<returned conversation ID>");
  const receiptEnvelope = json.find(value => value.content?.[0]?.type === "text");
  const receiptObject = json.find(value => value.id === "entity-demo");
  expect(JSON.parse(receiptEnvelope.content[0].text)).toEqual(receiptObject);
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
    if (readerInstall) {
      for (const line of installs) await exec(npm, line.split(/\s+/).slice(1), { cwd: directory, timeout: 240_000 });
    } else {
      // What `npm install @neo4j-labs/agent-memory@x.y.z ...` lays out: the packed package with no nested node_modules.
      const packDir = join(directory, ".pack");
      await mkdir(packDir);
      const { stdout } = await exec(npm, ["pack", "--ignore-scripts", "--silent", "--pack-destination", packDir], { cwd: sdkRoot });
      const tarball = join(packDir, stdout.trim().split("\n").at(-1)!);
      await exec("tar", ["-xzf", tarball, "-C", packDir]);
      await mkdir(join(directory, "node_modules", "@neo4j-labs"), { recursive: true });
      await rename(join(packDir, "package"), join(directory, "node_modules", "@neo4j-labs", "agent-memory"));
      expect(await readdir(join(directory, "node_modules", "@neo4j-labs", "agent-memory"))).not.toContain("node_modules");
      for (const name of ["@modelcontextprotocol", "@types", "zod"]) await symlink(join(sdkRoot, "node_modules", name), join(directory, "node_modules", name));
      await writeFile(join(directory, "package.json"), JSON.stringify({ name: "my-memory-mcp", version: "1.0.0" }));
    }
    for (const name of ["tutorial-state", "tutorial-cleanup", "tutorial-mcp"]) await copyFile(join(sdkRoot, "examples", "shared", `${name}.ts`), join(directory, "src", `${name}.ts`));
    // Merge the page's package fields into the project's package.json, as the lesson tells the reader to.
    const manifest = JSON.parse(await readFile(join(directory, "package.json"), "utf8"));
    await writeFile(join(directory, "package.json"), JSON.stringify({ ...manifest, ...packageFields }, null, 2));
    await writeFile(join(directory, "tsconfig.json"), config);
    const tsc = readerInstall ? join(directory, "node_modules", "typescript", "bin", "tsc") : join(sdkRoot, "node_modules", "typescript", "bin", "tsc");
    for (const variant of ["base", "custom", "restricted"] as const) {
      const registration = /  const toolNames = registerMemoryTools\(server, memory, \{[\s\S]*?\n  \}\);/;
      const assembled = variant === "custom" ? customImports + "\n" + source.replace("  const transport =", custom + "\n  const transport =")
        : variant === "restricted" ? source.replace(registration, restricted) : source;
      expect(assembled).toContain(variant === "custom" ? "memory_tutorial_graph" : "registerMemoryTools");
      await writeFile(join(directory, "src", "server.ts"), assembled);
      await exec(process.execPath, [tsc, "-p", join(directory, "tsconfig.json")]);
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
          // Local startup and tools/list do not authenticate or contact NAMS.
          expect(stub.requests).toHaveLength(0);
          const substitute = (value: unknown) => JSON.parse(JSON.stringify(value)
            .replaceAll("<printed userId>", userId).replaceAll("<printed runId>", runId)
            .replaceAll("<printed entityName>", `Lantern Orchard ${runId}`)
            .replaceAll("<returned conversation ID>", conversationId));
          conversationId = (await call("memory_create_conversation", substitute(conversationArgs))).id;
          await cli("record", path, "conversation", conversationId);
          const createdEntity = await call("memory_add_entity", substitute(entityArgs));
          entityId = createdEntity.id;
          const receiptPath = join(directory, ".tutorial-state", "entity-result.json");
          await writeFile(receiptPath, JSON.stringify(createdEntity), { mode: 0o600 });
          await cli("record", path, "entity", entityId, receiptPath);
          messageId = (await call("memory_add_messages", substitute(messageArgs)))[0].id;
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
}, readerInstall ? 600_000 : 60_000);
