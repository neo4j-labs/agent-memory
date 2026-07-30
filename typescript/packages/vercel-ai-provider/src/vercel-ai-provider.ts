/**
 * Provider mode — ProviderV4-compatible NAMS provider.
 *
 * Wraps any base AI SDK provider (openai, anthropic, etc.) with NAMS memory —
 * retrieved automatically on every call, persisted after every response.
 */

import type { ProviderV4, LanguageModelV4, EmbeddingModelV4, ImageModelV4 } from '@ai-sdk/provider';
import { NoSuchModelError } from '@ai-sdk/provider';
import type { LanguageModel } from 'ai';
import { createNamsMemory } from './vercel-ai-provider-middleware';
import { NamsConfig, NamsScope } from './vercel-ai-provider-types';

export interface NamsProviderOptions extends NamsConfig {
  baseProvider: (modelId: string) => LanguageModelV4;
  /**
   * User/conversation scope for this provider instance.
   * Create one provider instance per user session.
   */
  scope: NamsScope;
  /** Max memories retrieved and injected into the prompt per turn (default: 6). Does not affect storage. */
  maxMemories?: number;
  /** Persist each turn to NAMS short-term memory (default: true). */
  persistInteractions?: boolean;
  /** When set, builds a real entity graph per stored turn (one extra model call). */
  extractionModel?: LanguageModel;
}

/**
 * Create a ProviderV4-compatible NAMS provider, registrable with the
 * Vercel AI SDK via `createProviderRegistry`.
 */
export function createNamsProvider(options: NamsProviderOptions): ProviderV4 {
  const { baseProvider, scope, ...memoryConfig } = options;
  const memory = createNamsMemory(memoryConfig);

  return {
    specificationVersion: 'v4',

    languageModel(modelId: string): LanguageModelV4 {
      const base = baseProvider(modelId);
      return memory.wrap(base, scope, 'nams');
    },

    embeddingModel(modelId: string): EmbeddingModelV4 {
      throw new NoSuchModelError({ modelId, modelType: 'embeddingModel' });
    },

    imageModel(modelId: string): ImageModelV4 {
      throw new NoSuchModelError({ modelId, modelType: 'imageModel' });
    },
  };
}
