/**
 * The two things this agent talks to that a test cannot: the hosted Neo4j
 * Agent Memory Service and the live eve session-state slot.
 *
 * eve calls tools, hooks and memory handlers itself, so there is no call site
 * where a test could pass a fake in. This module is that seam: the test suite
 * assigns `deps.client` (a `MemoryClient` over `FakeNamsTransport`) and
 * `deps.cart` (a plain object), and the whole agent runs with no network and no
 * eve runtime. Production code never writes to `deps`.
 */

import { MemoryClient } from "@neo4j-labs/agent-memory";
import { sessionCart, type CartStore } from "./cart.js";

export interface Deps {
  client?: MemoryClient;
  cart?: CartStore;
}

export const deps: Deps = {};

/**
 * The memory client. A zero-arg `MemoryClient()` reads `MEMORY_API_KEY` and
 * `MEMORY_WORKSPACE_ID` from the environment and defaults to the hosted
 * endpoint; it is created once and cached on `deps`.
 */
export function memoryClient(): MemoryClient {
  if (deps.client === undefined) {
    if (process.env.MEMORY_API_KEY === undefined || process.env.MEMORY_API_KEY === "") {
      throw new Error("Set MEMORY_API_KEY — see .env.example");
    }
    deps.client = new MemoryClient();
  }
  return deps.client;
}

export function cartStore(): CartStore {
  return deps.cart ?? sessionCart;
}

/** Clears both overrides. Called from test `afterEach`. */
export function resetDeps(): void {
  delete deps.client;
  delete deps.cart;
}
