/** Hook events and results. A handler returns nothing, or an object to block, rewrite, or add context. */

import type { NamsLogger } from './vercel-ai-provider-types';

/** Events, in lifecycle order. */
export type NamsHookEvent =
  | 'SessionStart'
  | 'UserPromptSubmit'
  | 'PreToolUse'
  | 'PostToolUse'
  | 'PostToolUseFailure'
  | 'PreMemoryWrite'
  | 'Stop'
  | 'SessionEnd';

/** One saved turn. Hook authors can read and rewrite these on `PreMemoryWrite`. */
export interface SessionTurn {
  role: 'user' | 'assistant';
  content: string;
  metadata?: Record<string, unknown>;
}

// Inputs
export interface NamsHookBase {
  readonly event: NamsHookEvent;
  readonly userId: string;
  readonly conversationId?: string;
  readonly timestamp: Date;
}

export interface SessionStartInput extends NamsHookBase {
  event: 'SessionStart';
  reason: 'created' | 'resumed';
}

export interface UserPromptSubmitInput extends NamsHookBase {
  event: 'UserPromptSubmit';
  prompt: string;
}

export interface PreToolUseInput extends NamsHookBase {
  event: 'PreToolUse';
  toolName: string;
  toolCallId?: string;
  input: unknown;
}

export interface PostToolUseInput extends NamsHookBase {
  event: 'PostToolUse';
  toolName: string;
  toolCallId?: string;
  input: unknown;
  output: unknown;
}

export interface PostToolUseFailureInput extends NamsHookBase {
  event: 'PostToolUseFailure';
  toolName: string;
  toolCallId?: string;
  input: unknown;
  error: unknown;
  attempt: number;
}

export interface PreMemoryWriteInput extends NamsHookBase {
  event: 'PreMemoryWrite';
  turns: SessionTurn[];
}

export interface StopInput extends NamsHookBase {
  event: 'Stop';
  text?: string;
  turns: SessionTurn[];
}

export interface SessionEndInput extends NamsHookBase {
  event: 'SessionEnd';
  reason: string;
}

export interface NamsHookInputs {
  SessionStart: SessionStartInput;
  UserPromptSubmit: UserPromptSubmitInput;
  PreToolUse: PreToolUseInput;
  PostToolUse: PostToolUseInput;
  PostToolUseFailure: PostToolUseFailureInput;
  PreMemoryWrite: PreMemoryWriteInput;
  Stop: StopInput;
  SessionEnd: SessionEndInput;
}

// Outputs
export interface NamsHookOutputBase {
  additionalContext?: string;
  systemMessage?: string;
}

export interface SessionStartOutput extends NamsHookOutputBase {}

export interface UserPromptSubmitOutput extends NamsHookOutputBase {
  decision?: 'block';
  blockReason?: string;
  updatedPrompt?: string;
}

export interface PreToolUseOutput extends NamsHookOutputBase {
  permissionDecision?: 'allow' | 'deny';
  permissionDecisionReason?: string;
  updatedInput?: unknown;
}

export interface PostToolUseOutput extends NamsHookOutputBase {
  updatedOutput?: unknown;
}

export interface PostToolUseFailureOutput extends NamsHookOutputBase {
  retry?: boolean;
}

export interface PreMemoryWriteOutput extends NamsHookOutputBase {
  decision?: 'block';
  blockReason?: string;
  updatedTurns?: SessionTurn[];
}

export interface StopOutput extends NamsHookOutputBase {}

export interface SessionEndOutput extends NamsHookOutputBase {}

export interface NamsHookOutputs {
  SessionStart: SessionStartOutput;
  UserPromptSubmit: UserPromptSubmitOutput;
  PreToolUse: PreToolUseOutput;
  PostToolUse: PostToolUseOutput;
  PostToolUseFailure: PostToolUseFailureOutput;
  PreMemoryWrite: PreMemoryWriteOutput;
  Stop: StopOutput;
  SessionEnd: SessionEndOutput;
}

// Configuration

export type NamsHookHandler<E extends NamsHookEvent> = (
  input: NamsHookInputs[E],
) => NamsHookOutputs[E] | void | Promise<NamsHookOutputs[E] | void>;

export interface NamsHookEntry<E extends NamsHookEvent> {
  handler: NamsHookHandler<E>;
  timeout?: number;
  name?: string;
}

/** A matcher plus the handlers it applies to. */
export interface NamsHookGroup<E extends NamsHookEvent> {
  matcher?: string;
  hooks: Array<NamsHookHandler<E> | NamsHookEntry<E>>;
}

/** Hooks by event. A bare function is shorthand for a group matching everything. */
export type NamsHookConfig = {
  [E in NamsHookEvent]?: Array<NamsHookGroup<E> | NamsHookHandler<E>>;
};

export type NamsSystemMessageSink = (message: string, event: NamsHookEvent) => void;

// Matching

const DEFAULT_TIMEOUT_MS = 30_000;

const MATCHERLESS: ReadonlySet<NamsHookEvent> = new Set([
  'UserPromptSubmit',
  'PreMemoryWrite',
  'Stop',
]);

const PLAIN_MATCHER = /^[A-Za-z0-9_\-, |]*$/;

type Matcher = (subject?: string) => boolean;

const compileMatcher = (matcher: string | undefined, event: string, log: NamsLogger): Matcher => {
  if (!matcher || matcher === '*') return () => true;

  if (PLAIN_MATCHER.test(matcher)) {
    const names = matcher.split(/[|,]/).map(n => n.trim()).filter(Boolean);
    if (names.length === 0) return () => true;
    return subject => subject !== undefined && names.includes(subject);
  }

  try {
    const re = new RegExp(matcher);
    return subject => subject !== undefined && re.test(subject);
  } catch (err) {
    log.error(`invalid ${event} hook matcher ${matcher}, it will never match`, err);
    return () => false;
  }
};

const subjectOf = (input: NamsHookInputs[NamsHookEvent]): string | undefined => {
  switch (input.event) {
    case 'PreToolUse':
    case 'PostToolUse':
    case 'PostToolUseFailure':
      return input.toolName;
    case 'SessionStart':
    case 'SessionEnd':
      return input.reason;
    default:
      return undefined;
  }
};


const applyRewrite = (
  input: NamsHookInputs[NamsHookEvent],
  output: NamsHookOutputs[NamsHookEvent],
): NamsHookInputs[NamsHookEvent] => {
  const out = output as Record<string, unknown>;
  switch (input.event) {
    case 'UserPromptSubmit':
      return typeof out.updatedPrompt === 'string' ? { ...input, prompt: out.updatedPrompt } : input;
    case 'PreToolUse':
      return 'updatedInput' in out && out.updatedInput !== undefined
        ? { ...input, input: out.updatedInput }
        : input;
    case 'PostToolUse':
      return 'updatedOutput' in out && out.updatedOutput !== undefined
        ? { ...input, output: out.updatedOutput }
        : input;
    case 'PreMemoryWrite':
      return Array.isArray(out.updatedTurns)
        ? { ...input, turns: out.updatedTurns as SessionTurn[] }
        : input;
    default:
      return input;
  }
};

// Dispatch

export interface NamsHookResult<E extends NamsHookEvent> {
  input: NamsHookInputs[E];
  blocked: boolean;
  reason?: string;
  additionalContext: string[];
  systemMessages: string[];
  retry: boolean;
}

export interface NamsHookRegistry {
  has(event: NamsHookEvent): boolean;
  run<E extends NamsHookEvent>(event: E, input: NamsHookInputs[E]): Promise<NamsHookResult<E>>;
}

interface CompiledEntry {
  match: Matcher;
  handler: NamsHookHandler<NamsHookEvent>;
  timeout: number;
  name: string;
}

/** Race a handler against its timeout. A late result is dropped, not awaited. */
const withTimeout = <T>(work: Promise<T>, ms: number, onTimeout: () => void): Promise<T | undefined> => {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const expiry = new Promise<undefined>(resolve => {
    timer = setTimeout(() => { onTimeout(); resolve(undefined); }, ms);
  });
  return Promise.race([work, expiry]).finally(() => clearTimeout(timer));
};

const isEntry = <E extends NamsHookEvent>(
  hook: NamsHookHandler<E> | NamsHookEntry<E>,
): hook is NamsHookEntry<E> => typeof hook !== 'function';

/** Compile the hook config. Bad matchers are reported here, at startup. */
export function compileHooks(
  config: NamsHookConfig | undefined,
  log: NamsLogger,
  onSystemMessage?: NamsSystemMessageSink,
): NamsHookRegistry {
  const byEvent = new Map<NamsHookEvent, CompiledEntry[]>();

  for (const [event, groups] of Object.entries(config ?? {}) as Array<
    [NamsHookEvent, Array<NamsHookGroup<NamsHookEvent> | NamsHookHandler<NamsHookEvent>>]
  >) {
    const entries: CompiledEntry[] = [];

    for (const group of groups ?? []) {
      const normalised: NamsHookGroup<NamsHookEvent> =
        typeof group === 'function' ? { hooks: [group] } : group;

      if (normalised.matcher && normalised.matcher !== '*' && MATCHERLESS.has(event)) {
        log.warn(`${event} has nothing to match on, so its matcher ${normalised.matcher} is ignored`);
      }
      const match = MATCHERLESS.has(event)
        ? () => true
        : compileMatcher(normalised.matcher, event, log);

      for (const hook of normalised.hooks ?? []) {
        const entry = isEntry(hook) ? hook : { handler: hook };
        entries.push({
          match,
          handler: entry.handler,
          timeout: entry.timeout ?? DEFAULT_TIMEOUT_MS,
          name: entry.name ?? entry.handler.name ?? 'anonymous',
        });
      }
    }

    if (entries.length) byEvent.set(event, entries);
  }

  return {
    has: event => byEvent.has(event),

    async run<E extends NamsHookEvent>(event: E, input: NamsHookInputs[E]): Promise<NamsHookResult<E>> {
      const result: NamsHookResult<E> = {
        input,
        blocked: false,
        additionalContext: [],
        systemMessages: [],
        retry: false,
      };

      const entries = byEvent.get(event);
      if (!entries) return result;

      const subject = subjectOf(input);

      for (const entry of entries) {
        if (!entry.match(subject)) continue;

        let output: NamsHookOutputs[E] | void | undefined;
        try {
          output = await withTimeout(
            Promise.resolve(entry.handler(result.input as NamsHookInputs[NamsHookEvent])),
            entry.timeout,
            () => log.warn(`${event} hook ${entry.name} timed out after ${entry.timeout}ms, ignoring it`),
          ) as NamsHookOutputs[E] | undefined;
        } catch (err) {
          log.error(`${event} hook ${entry.name} threw, ignoring it`, err);
          continue;
        }
        if (!output) continue;

        const out = output as NamsHookOutputBase & Record<string, unknown>;
        if (out.additionalContext) result.additionalContext.push(out.additionalContext);
        if (out.systemMessage) {
          result.systemMessages.push(out.systemMessage);
          onSystemMessage?.(out.systemMessage, event);
        }
        if (out.retry === true) result.retry = true;

        result.input = applyRewrite(
          result.input as NamsHookInputs[NamsHookEvent],
          output as NamsHookOutputs[NamsHookEvent],
        ) as NamsHookInputs[E];

        if (out.permissionDecision === 'deny' || out.decision === 'block') {
          result.blocked = true;
          result.reason =
            (out.permissionDecisionReason as string | undefined) ??
            (out.blockReason as string | undefined) ??
            `blocked by a ${event} hook`;
          break;
        }
      }

      return result;
    },
  };
}
