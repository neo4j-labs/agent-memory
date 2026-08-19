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

  return {
    sessionManager,
    conversationManager: baseManager as unknown as StrandsConversationManager,
  };
}
