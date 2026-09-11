import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Unit tests cover the pure logic a type error would not catch: which card a
// tool result maps to, and how each extractor reshapes the backend payload.
// No DOM environment needed, so no jsdom dependency.
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["test/**/*.test.ts"],
  },
});
