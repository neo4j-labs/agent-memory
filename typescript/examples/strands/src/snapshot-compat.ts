/**
 * Temporary compatibility shim — delete once the SDK fix lands.
 *
 * `Neo4jConversationManager` prepends its three-tier context by pushing plain
 * object literals onto `agent.messages`
 * (`typescript/src/integrations/strands/conversation-manager.ts`,
 * `contextInjectionMessage`). Strands 1.17's `takeSnapshot()` then calls
 * `msg.toJSON()` on every message, so the snapshot that `Neo4jSessionStorage`
 * persists after each turn throws `msg.toJSON is not a function` — which breaks
 * the run as soon as there is any context to inject. The two surfaces work on
 * their own and only collide through `connectMemoryToAgent`, which is why the
 * SDK's own unit tests (an agent stub holding a plain array) do not catch it.
 *
 * The fix belongs in the SDK: build the injected message with
 * `new Message({ role, content: [contentBlockFromData({ text })] })`. Until that
 * ships, this hook re-wraps anything plain that reached `agent.messages` before
 * the snapshot is taken. It is registered *after* `agent.initialize()` on
 * purpose: hooks fire in registration order, and the conversation manager
 * registers its injection during `initialize()`.
 */

import {
  BeforeInvocationEvent,
  Message,
  contentBlockFromData,
  type Agent,
  type ContentBlock,
  type ContentBlockData,
  type MessageData,
} from "@strands-agents/sdk";

export async function ensureSnapshotSafeMessages(agent: Agent): Promise<void> {
  await agent.initialize();
  agent.addHook(BeforeInvocationEvent, (event: BeforeInvocationEvent) => {
    const carrier = event.agent as unknown as { messages: unknown[] };
    carrier.messages = carrier.messages.map(toMessageInstance);
  });
}

function toMessageInstance(message: unknown): Message {
  if (message instanceof Message) return message;
  const data = message as MessageData;
  return new Message({
    role: data.role,
    content: (data.content ?? []).map(toContentBlockInstance),
  });
}

function toContentBlockInstance(block: unknown): ContentBlock {
  return isSerializable(block)
    ? (block as ContentBlock)
    : contentBlockFromData(block as ContentBlockData);
}

function isSerializable(value: unknown): boolean {
  return typeof (value as { toJSON?: unknown } | null)?.toJSON === "function";
}
