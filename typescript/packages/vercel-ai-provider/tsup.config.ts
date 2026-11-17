import { defineConfig } from 'tsup';

export default defineConfig({
  // Separate eve entry, so apps without eve never load it.
  entry: { index: 'src/index.ts', eve: 'src/vercel-ai-provider-eve.ts' },
  // ESM-only, because the @neo4j-labs/agent-memory peer is ESM-only.
  format: ['esm'],
  dts: true,
  sourcemap: true,
  clean: true,
  treeshake: true,
  // Peer deps stay external.
  external: ['ai', '@neo4j-labs/agent-memory', 'zod', '@ai-sdk/mcp', 'eve'],
});
