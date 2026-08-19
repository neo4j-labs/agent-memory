/**
 * Shared internals for the Strands integration split: the lazy SDK loader
 * and the options type shared across every public entrypoint.
 *
 * Strands lives in `devDependencies` only — every import below is a
 * type-only import, erased at compile time. The only runtime reference to
 * `@strands-agents/sdk` in this integration is the dynamic `import()` inside
 * {@link loadStrands}, so users without Strands installed pay zero bundle
 * cost.
 */

// ---------------------------------------------------------------------------
// Strands runtime imports are deferred to a small loader so callers who use
// only types still pay zero runtime cost. Callers who instantiate the
// classes in this integration MUST have @strands-agents/sdk installed in
// their own dependencies — same contract as every other duck-typed
// integration.
// ---------------------------------------------------------------------------

export type StrandsModule = typeof import("@strands-agents/sdk");

let _strandsModule: StrandsModule | null = null;

export async function loadStrands(): Promise<StrandsModule> {
  if (!_strandsModule) {
    // Dynamic import keeps the static export graph free of @strands-agents/sdk.
    _strandsModule = (await import("@strands-agents/sdk")) as StrandsModule;
  }
  return _strandsModule;
}

// ---------------------------------------------------------------------------
// Public options
// ---------------------------------------------------------------------------

/** Options shared by every public entrypoint in this module. */
export interface StrandsIntegrationOptions {
  /**
   * NAMS Conversation id to wire to. Required by the convenience factory and
   * by individual exports that need correlation across invocations.
   */
  conversationId: string;

  /** Include reflections from `getContext()` in prompt injection. Default: true. */
  includeReflections?: boolean;

  /** Include observations from `getContext()` in prompt injection. Default: true. */
  includeObservations?: boolean;
}
