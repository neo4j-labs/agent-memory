import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// The tests exercise the route handlers in `lib/handlers.ts` against a mocked
// NAMS (msw) and the AI SDK's own mock model — no DOM, so no jsdom dependency.
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["test/**/*.test.ts"],
  },
});
