import { defineConfig } from 'tsup';

export default defineConfig({
  // Separate eve entry, so apps without eve never load it.
  entry: { index: 'src/index.ts', eve: 'src/vercel-ai-provider-eve.ts' },
  // ESM-only: the @neo4j-labs/agent-memory peer is ESM-only (no `require`
  // export condition), so a CJS build of this package cannot work at runtime.
  format: ['esm'],
  dts: true,
  sourcemap: true,
  clean: true,
  treeshake: true,
  // Keep peer deps external to avoid bundling duplicates into dist.
  external: ['ai', '@neo4j-labs/agent-memory', 'zod', '@ai-sdk/mcp', 'eve'],
});
