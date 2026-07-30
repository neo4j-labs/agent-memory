/**
 * ProviderV4 surface — what the Vercel AI SDK (and createProviderRegistry)
 * expects from a community provider:
 *
 *  - specificationVersion 'v4'
 *  - languageModel(id) delegates to the base provider and wraps it with memory
 *  - embeddingModel / imageModel throw NoSuchModelError (memory-only provider)
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NoSuchModelError } from '@ai-sdk/provider';
import { makeFakeClient, makeFakeModel, type FakeClient } from './vercel-ai-provider-helpers';

const holder = vi.hoisted(() => ({ client: undefined as unknown }));

vi.mock('@neo4j-labs/agent-memory', () => ({
  MemoryClient: vi.fn().mockImplementation(() => holder.client),
}));

import { createNamsProvider } from '../src/vercel-ai-provider';

let fake: FakeClient;
let userCounter = 0;
const freshUser = () => `prov-user-${Date.now()}-${userCounter++}`;

beforeEach(() => {
  fake = makeFakeClient();
  holder.client = fake;
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

describe('createNamsProvider', () => {
  it('exposes specificationVersion v3', () => {
    const provider = createNamsProvider({
      apiKey: 'k',
      baseProvider: () => makeFakeModel().model,
      scope: { userId: freshUser() },
    });
    expect(provider.specificationVersion).toBe('v4');
  });

  it('languageModel(id) calls the base provider with the model id and wraps it', async () => {
    const { model, capturedParams } = makeFakeModel();
    const baseProvider = vi.fn(() => model);

    const provider = createNamsProvider({
      apiKey: 'k',
      baseProvider,
      scope: { userId: freshUser(), conversationId: 'conv-p' },
    });

    const wrapped = provider.languageModel('gpt-test');
    expect(baseProvider).toHaveBeenCalledWith('gpt-test');
    expect(wrapped.specificationVersion).toBe('v4');

    // The wrapped model still generates and persists through NAMS.
    await wrapped.doGenerate({
      prompt: [{ role: 'user', content: [{ type: 'text', text: 'ping' }] }],
    } as any);
    expect(capturedParams).toHaveLength(1);
    expect(fake.shortTerm.addMessage).toHaveBeenCalledWith('conv-p', 'user', 'ping');
  });

  it('throws NoSuchModelError for embedding and image models', () => {
    const provider = createNamsProvider({
      apiKey: 'k',
      baseProvider: () => makeFakeModel().model,
      scope: { userId: freshUser() },
    });

    expect(() => provider.embeddingModel('text-embedding-3-small')).toThrow(NoSuchModelError);
    expect(() => provider.imageModel('dall-e-3')).toThrow(NoSuchModelError);
  });
});
