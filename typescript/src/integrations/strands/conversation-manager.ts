/**
 * {@link Neo4jConversationManager} — a `ConversationManager` subclass that
 * delegates `reduce()` to an inner manager (defaults to
 * `SlidingWindowConversationManager`) AND registers a `BeforeInvocationEvent`
 * hook that prepends three-tier context (reflections + observations from
 * `getContext()`) to every model call. Layered, not replacing —
 * recent-history trimming still behaves the way the inner manager defines.
 */

import type {
  BeforeInvocationEvent as BeforeInvocationEventType,
  LocalAgent,
  Message as StrandsMessage,
  ConversationManager as StrandsConversationManager,
  ConversationManagerReduceOptions,
  SlidingWindowConversationManager as SlidingWindowConversationManagerType,
} from "@strands-agents/sdk";

import type { MemoryClient } from "../../client.js";
import type { StrandsIntegrationOptions } from "./internal.js";
import { loadStrands } from "./internal.js";

/** Options for {@link Neo4jConversationManager}. */
export interface Neo4jConversationManagerOptions
  extends Pick<StrandsIntegrationOptions, "conversationId" | "includeReflections" | "includeObservations"> {
  /**
   * Inner `ConversationManager` to delegate `reduce()` to. When omitted,
   * defaults to `SlidingWindowConversationManager` (constructed lazily so
   * Strands' module is only loaded if the manager is actually used).
   */
  inner?: StrandsConversationManager;
}

/**
 * Layered ConversationManager: context-injection hook + inner manager.
 *
 * The inner manager (defaults to `SlidingWindowConversationManager`) owns
 * trimming and summarization. This manager registers a
 * `BeforeInvocationEvent` hook that prepends reflections + observations from
 * `getContext()` as system messages, BEFORE the inner manager's reduce
 * logic runs.
 *
 * Lazily constructs an inner manager on first `initAgent` invocation so
 * importing this module doesn't load Strands' runtime unless the manager
 * is actually used.
 */
export class Neo4jConversationManager {
  public readonly name = "neo4j:context-injection";
  /**
   * Mirrored from Strands' `ConversationManager` to satisfy duck-typing
   * at compile time. We never set it — context injection has no notion
   * of a compression threshold.
   */
  protected readonly _compressionThreshold: number | undefined = undefined;

  // We can't extend Strands' abstract class via a static `extends` clause
  // because Strands is a dynamic import — the base class identity isn't
  // known at module-load time. Instead we *delegate* to a lazily-built
  // inner manager and implement the abstract surface explicitly. Strands
  // duck-types on shape, not on instanceof, so this works.

  private inner: StrandsConversationManager | null = null;

  constructor(
    private readonly memory: MemoryClient,
    private readonly options: Neo4jConversationManagerOptions,
  ) {}

  async reduce(opts: ConversationManagerReduceOptions): Promise<boolean> {
    const inner = await this.ensureInner();
    return inner.reduce(opts);
  }

  async initAgent(agent: LocalAgent): Promise<void> {
    const inner = await this.ensureInner();
    inner.initAgent(agent);

    const strands = await loadStrands();
    // Register a hook to inject three-tier context BEFORE every model call.
    agent.addHook(
      strands.BeforeInvocationEvent,
      async (event: BeforeInvocationEventType) => {
        await this.injectContext(event);
      },
    );
  }

  private async ensureInner(): Promise<StrandsConversationManager> {
    if (this.inner) return this.inner;
    if (this.options.inner) {
      this.inner = this.options.inner;
      return this.inner;
    }
    const strands = await loadStrands();
    const Ctor =
      strands.SlidingWindowConversationManager as new () => SlidingWindowConversationManagerType;
    this.inner = new Ctor();
    return this.inner;
  }

  private async injectContext(event: BeforeInvocationEventType): Promise<void> {
    try {
      const ctx = await this.memory.shortTerm.getContext(this.options.conversationId);
      const prepend: StrandsMessage[] = [];
      const includeReflections = this.options.includeReflections ?? true;
      const includeObservations = this.options.includeObservations ?? true;

      if (includeReflections && ctx.reflections.length > 0) {
        for (const r of ctx.reflections) {
          prepend.push(contextInjectionMessage(`[reflection] ${r.content}`));
        }
      }
      if (includeObservations && ctx.observations.length > 0) {
        for (const o of ctx.observations) {
          prepend.push(contextInjectionMessage(`[observation] ${o.content}`));
        }
      }

      if (prepend.length === 0) return;

      // Prepend by mutating agent.messages in place. The order MUST be
      // [context...] + [existing messages...]. Strands' inner manager
      // may later trim from the head — that's intentional (these
      // injections aren't sacred; staleness > overflow).
      const agentLike = event.agent as unknown as { messages: StrandsMessage[] };
      agentLike.messages = [...prepend, ...agentLike.messages];
    } catch {
      // Context injection is best-effort. A failed getContext() (transient,
      // not-supported, etc.) must not break the agent run — we just fall
      // back to whatever the inner manager produces.
    }
  }
}

function contextInjectionMessage(text: string): StrandsMessage {
  return {
    role: "system" as StrandsMessage["role"],
    content: [{ text }] as unknown as StrandsMessage["content"],
  } as StrandsMessage;
}
