/**
 * Tests run inside `workerd`, not Node.
 *
 * `@cloudflare/vitest-pool-workers` boots the real Workers runtime via Miniflare
 * using this example's `wrangler.jsonc`, so the suite proves what a Node-hosted
 * test cannot: the SDK and the AI SDK both load and run in an isolate with no
 * Node built-ins and no `nodejs_compat` flag.
 *
 * Two version notes, both load-bearing:
 *  - the pool requires `vitest` 4.x (the other examples in this repo are on 5.x;
 *    each example has its own `node_modules`, so they do not collide);
 *  - since 0.22 the config API is the `cloudflareTest()` Vite plugin — the older
 *    `defineWorkersConfig` from `@cloudflare/vitest-pool-workers/config` is gone.
 */

import { cloudflareTest } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [cloudflareTest({ wrangler: { configPath: "./wrangler.jsonc" } })],
});
