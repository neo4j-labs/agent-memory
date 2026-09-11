/**
 * A scripted Strands model provider, so the tests drive the real agent loop with
 * no model provider and no API key.
 *
 * It records every prompt it is handed, which is how the tests assert that the
 * three-tier context NAMS returned actually reached the model, and it asks for
 * the `lookup_fact` tool once so the reasoning hooks have a tool call to record.
 */

import {
  Model,
  type BaseModelConfig,
  type ContentBlock,
  type Message,
  type ModelStreamEvent,
  type StreamOptions,
} from "@strands-agents/sdk";

const ANSWER =
  "Neo4j stores relationships as first-class records, which suits a " +
  "recommendation engine.";

export class StubModel extends Model<BaseModelConfig> {
  /** Every message list the agent loop sent, in order. */
  readonly prompts: Message[][] = [];
  /** Every system prompt the agent loop sent, in order. */
  readonly systemPrompts: Array<string | undefined> = [];

  private config: BaseModelConfig = { modelId: "stub-model" };
  private toolUses = 0;

  updateConfig(modelConfig: BaseModelConfig): void {
    this.config = { ...this.config, ...modelConfig };
  }

  getConfig(): BaseModelConfig {
    return this.config;
  }

  /** Flattened text of every prompt, for substring assertions. */
  promptText(index: number): string {
    return (this.prompts[index] ?? [])
      .flatMap((message) => message.content.map(blockText))
      .join("\n");
  }

  async *stream(
    messages: Message[],
    options?: StreamOptions,
  ): AsyncIterable<ModelStreamEvent> {
    this.prompts.push(messages);
    this.systemPrompts.push(
      typeof options?.systemPrompt === "string" ? options.systemPrompt : undefined,
    );

    yield { type: "modelMessageStartEvent", role: "assistant" };

    // Ask for the tool the first time the user requests a lookup, and only when
    // the loop has not already fed a tool result back in.
    if (wantsLookup(messages) && !hasToolResult(messages)) {
      this.toolUses += 1;
      yield {
        type: "modelContentBlockStartEvent",
        start: {
          type: "toolUseStart",
          name: "lookup_fact",
          toolUseId: `stub-tool-${this.toolUses}`,
        },
      };
      yield {
        type: "modelContentBlockDeltaEvent",
        delta: { type: "toolUseInputDelta", input: JSON.stringify({ topic: "Neo4j" }) },
      };
      yield { type: "modelContentBlockStopEvent" };
      yield { type: "modelMessageStopEvent", stopReason: "toolUse" };
      return;
    }

    yield { type: "modelContentBlockStartEvent" };
    yield { type: "modelContentBlockDeltaEvent", delta: { type: "textDelta", text: ANSWER } };
    yield { type: "modelContentBlockStopEvent" };
    yield { type: "modelMessageStopEvent", stopReason: "endTurn" };
  }
}

export const STUB_ANSWER = ANSWER;

function blockText(block: ContentBlock): string {
  if ("text" in block && typeof block.text === "string") return block.text;
  if ("toolUse" in block) return JSON.stringify(block.toolUse);
  if ("toolResult" in block) return JSON.stringify(block.toolResult);
  return "";
}

function wantsLookup(messages: Message[]): boolean {
  const lastUser = [...messages].reverse().find((message) => message.role === "user");
  return (lastUser?.content ?? []).some((block) => blockText(block).includes("lookup_fact"));
}

function hasToolResult(messages: Message[]): boolean {
  return messages.some((message) => message.content.some((block) => "toolResult" in block));
}
