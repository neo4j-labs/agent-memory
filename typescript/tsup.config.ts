import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    index: "src/index.ts",
    "middleware/vercel-ai": "src/middleware/vercel-ai.ts",
    "mcp/index": "src/mcp/index.ts",
    "integrations/langchain": "src/integrations/langchain.ts",
    "integrations/mastra": "src/integrations/mastra.ts",
    "integrations/strands": "src/integrations/strands/index.ts",
    testing: "src/testing.ts",
  },
  format: ["esm"],
  dts: true,
  sourcemap: true,
  clean: true,
  target: "es2022",
  splitting: true,
  treeshake: true,
  // Never bundle the framework adapters' peer SDKs into our dist —
  // they live in user devDependencies (or peer dependencies of theirs).
  external: [
    "@strands-agents/sdk",
    "@opentelemetry/api",
    "@modelcontextprotocol/sdk",
    "@ai-sdk/provider",
  ],
});
