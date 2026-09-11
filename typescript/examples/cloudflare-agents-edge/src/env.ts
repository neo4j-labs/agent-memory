/**
 * The Worker's bindings, and the one edge-specific rule worth memorising.
 *
 * On Cloudflare Workers there is no `process.env` at module scope — secrets and
 * vars arrive as the second argument to `fetch(request, env, ctx)`. So every
 * client in this example is constructed *per request*, from `env`, and nothing
 * is read at module init. `MemoryClient` is built for this: it guards its own
 * `process.env` lookups, and both `apiKey` and `endpoint` are constructor
 * options.
 *
 * `wrangler types` can generate this interface for you from `wrangler.jsonc`
 * (`npm run types`); it is written out by hand here so the example is readable
 * without running a codegen step first.
 */

export interface Env {
  /** Secret. `nams_…` key from https://memory.neo4jlabs.com — `wrangler secret put MEMORY_API_KEY`. */
  MEMORY_API_KEY: string;

  /** Secret. `wrangler secret put OPENAI_API_KEY`. */
  OPENAI_API_KEY: string;

  /** Secret, optional. Only needed when your key spans several workspaces. */
  MEMORY_WORKSPACE_ID?: string;

  /** Var. Override for a private NAMS deployment. Defaults to the hosted v1 root. */
  MEMORY_ENDPOINT?: string;

  /** Var. OpenAI model id. Defaults to `gpt-5-mini`. */
  OPENAI_MODEL?: string;

  /** Var. Owner recorded on conversations this Worker creates. */
  DEMO_USER_ID?: string;
}

/** Default model id — override with the `OPENAI_MODEL` var, not by editing this. */
export const DEFAULT_MODEL = "gpt-5-mini";

/** Default conversation owner. A real app reads this from its session. */
export const DEFAULT_USER_ID = "edge-demo-user";

/**
 * Read a required binding, failing with a message that names the fix.
 *
 * A missing secret on Workers is silent — the binding is simply absent — so an
 * unguarded read shows up later as a NAMS 401 from a Worker you cannot attach a
 * debugger to. Check it at the top of the request instead.
 */
export function requireBinding(env: Env, name: "MEMORY_API_KEY" | "OPENAI_API_KEY"): string {
  const value = env[name];
  if (!value) {
    throw new Error(
      `${name} is not bound. Locally: copy .dev.vars.example to .dev.vars. ` +
        `Deployed: npx wrangler secret put ${name}`,
    );
  }
  return value;
}
