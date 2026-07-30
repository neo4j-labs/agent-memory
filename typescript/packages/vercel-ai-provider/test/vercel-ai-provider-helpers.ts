/**
 * Shared test doubles: a controllable fake MemoryClient and a minimal
 * LanguageModelV4 stub that records the params it was called with.
 */

import { vi } from 'vitest';
import type { LanguageModelV4 } from '@ai-sdk/provider';

export interface FakeClient {
  shortTerm: {
    listConversations: ReturnType<typeof vi.fn>;
    createConversation: ReturnType<typeof vi.fn>;
    addMessage: ReturnType<typeof vi.fn>;
    searchMessages: ReturnType<typeof vi.fn>;
  };
  longTerm: {
    searchEntities: ReturnType<typeof vi.fn>;
    getEntityByName: ReturnType<typeof vi.fn>;
    addEntity: ReturnType<typeof vi.fn>;
    setEntityFeedback: ReturnType<typeof vi.fn>;
    addRelationship: ReturnType<typeof vi.fn>;
  };
  reasoning: {
    listSteps: ReturnType<typeof vi.fn>;
  };
}

export function makeFakeClient(): FakeClient {
  return {
    shortTerm: {
      listConversations: vi.fn(async () => []),
      createConversation: vi.fn(async ({ userId }: { userId: string }) => ({
        id: `conv-${userId}`,
      })),
      addMessage: vi.fn(async () => ({ id: 'msg-1' })),
      searchMessages: vi.fn(async () => []),
    },
    longTerm: {
      searchEntities: vi.fn(async () => []),
      getEntityByName: vi.fn(async () => null),
      addEntity: vi.fn(async (name: string, type: string) => ({ id: `ent-${name}`, name, type })),
      setEntityFeedback: vi.fn(async () => ({})),
      addRelationship: vi.fn(async () => ({})),
    },
    reasoning: {
      listSteps: vi.fn(async () => []),
    },
  };
}

export interface FakeModelResult {
  capturedParams: any[];
  model: LanguageModelV4;
}

/** A LanguageModelV4 stub whose doGenerate returns fixed text and records params. */
export function makeFakeModel(
  assistantText = 'Hello back',
  streamChunks?: any[],
): FakeModelResult {
  const capturedParams: any[] = [];

  const model = {
    specificationVersion: 'v4',
    provider: 'fake',
    modelId: 'fake-model',
    supportedUrls: {},
    doGenerate: vi.fn(async (params: any) => {
      capturedParams.push(params);
      return {
        content: [{ type: 'text', text: assistantText }],
        finishReason: 'stop',
        usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
        warnings: [],
      };
    }),
    doStream: vi.fn(async (params: any) => {
      capturedParams.push(params);
      // V3 spelling: text-delta chunks carry `delta`.
      const chunks = streamChunks ?? [
        { type: 'text-delta', id: '1', delta: assistantText.slice(0, 5) },
        { type: 'text-delta', id: '1', delta: assistantText.slice(5) },
        { type: 'finish', finishReason: 'stop', usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } },
      ];
      const stream = new ReadableStream({
        start(controller) {
          for (const chunk of chunks) controller.enqueue(chunk);
          controller.close();
        },
      });
      return { stream };
    }),
  } as unknown as LanguageModelV4;

  return { capturedParams, model };
}

/** Drain a ReadableStream so middleware flush() runs. */
export async function drainStream(stream: ReadableStream): Promise<any[]> {
  const reader = stream.getReader();
  const chunks: any[] = [];
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
  }
  return chunks;
}

/** Wait for fire-and-forget persistence promises scheduled inside flush(). */
export const settle = () => new Promise((r) => setTimeout(r, 0));
