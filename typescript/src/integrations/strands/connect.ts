/**
 * {@link connectMemoryToAgent} bundles session storage, the conversation
 * manager, and (lazily) the reasoning hooks for the common case.
 */

import type {
  LocalAgent,
  ConversationManager as StrandsConversationManager,
  SessionManager as SessionManagerType,
  SessionManagerConfig,
} from "@strands-agents/sdk";

import type { MemoryClient } from "../../client.js";
import type { StrandsIntegrationOptions } from "./internal.js";
import { loadStrands } from "./internal.js";
import { Neo4jSessionStorage } from "./session-storage.js";
import { Neo4jConversationManager } from "./conversation-manager.js";
import { registerReasoningHooksOnAgent } from "./reasoning-hooks.js";

/** Result of {@link connectMemoryToAgent} — spread directly into `new Agent({ ... })`. */
export interface ConnectMemoryToAgentResult {
  sessionManager: SessionManagerType;
  /**
   * Typed as `StrandsConversationManager` (the abstract base) so callers
   * can spread the result straight into `new Agent({ ... })` without
   * casts. At runtime this is a {@link Neo4jConversationManager}.
   */
  conversationManager: StrandsConversationManager;
}

/**
 * One-shot helper that wires the SessionStorage, the ConversationManager, and
 * (lazily) the reasoning hooks against a NAMS `MemoryClient`. Spread the
 * return value into `new Agent({ ... })`.
 *
 * Reasoning hooks attach themselves automatically when the conversation
 * manager's `initAgent` runs — no separate registration step required.
 */
export async function connectMemoryToAgent(
  memory: MemoryClient,
  options: StrandsIntegrationOptions,
): Promise<ConnectMemoryToAgentResult> {
  const strands = await loadStrands();
  // `{ snapshot: SnapshotStorage }` is the snapshot-shaped storage form.
  // Strands 1.16+ marks it deprecated in favour of a unified `Storage`
  // (`write`/`read`/`delete`/`list` over opaque `Uint8Array` keys) — which we
  // deliberately do not use: NAMS stores a conversation as graph nodes, and a
  // byte-blob backend would reduce it to an opaque value with no entity
  // extraction, search or graph traversal. `SessionManagerConfig.storage`
  // still accepts this form; see `Neo4jSessionStorage` for the mapping.
  const sessionManager = new strands.SessionManager({
    sessionId: options.conversationId,
    storage: { snapshot: new Neo4jSessionStorage(memory) },
  } satisfies SessionManagerConfig);

  // Wrap Neo4jConversationManager so its initAgent ALSO registers the
  // reasoning hooks. Cleaner than asking the caller to do two things.
  const baseManager = new Neo4jConversationManager(memory, options);
  const originalInit = baseManager.initAgent.bind(baseManager);
  baseManager.initAgent = async (agent: LocalAgent) => {
    await originalInit(agent);
    await registerReasoningHooksOnAgent(memory, agent, {
      conversationId: options.conversationId,
    });
  };

  // Strands' `ConversationManager` declares a `protected` member, which makes
  // structural assignment from a non-derived class impossible — and we cannot
  // derive from it, because the base class arrives through a dynamic import.
  // Asserting the *public* surface first means a rename or reshape of `name` /
  // `reduce` / `initAgent` still fails compilation, instead of being swallowed
  // by the cast below.
  const publicSurface: Pick<StrandsConversationManager, "name" | "reduce" | "initAgent"> =
    baseManager;

  return {
    sessionManager,
    conversationManager: publicSurface as unknown as StrandsConversationManager,
  };
}
