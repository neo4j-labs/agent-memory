/** Provider mode: wraps any AI SDK provider so every model it returns has memory. */

// Import from `ai` so types match the caller's `ai` version.
import { NoSuchModelError, type createProviderRegistry } from 'ai';
import { createNamsMemory, type WrappableModel } from './vercel-ai-provider-middleware';
import { NamsConfig, NamsScope } from './vercel-ai-provider-types';

// `ai` doesn't export this type, so derive it.
type ProviderV4 = Extract<
  Parameters<typeof createProviderRegistry>[0][string],
  { specificationVersion: 'v4' }
>;

export interface NamsProviderOptions extends NamsConfig {
  baseProvider: (modelId: string) => WrappableModel;
  /** User and conversation. Create one provider per user session. */
  scope: NamsScope;
  /** Max memories added to the prompt per turn (default: 6). */
  maxMemories?: number;
  /** Save each turn to NAMS (default: true). */
  persistInteractions?: boolean;
}

/** Create a NAMS provider. Works with `createProviderRegistry`. */
export function createNamsProvider(options: NamsProviderOptions): ProviderV4 {
  const { baseProvider, scope, ...memoryConfig } = options;
  const memory = createNamsMemory(memoryConfig);

  return {
    specificationVersion: 'v4',

    languageModel(modelId: string) {
      const base = baseProvider(modelId);
      return memory.wrap(base, scope, 'nams');
    },

    embeddingModel(modelId: string): never {
      throw new NoSuchModelError({ modelId, modelType: 'embeddingModel' });
    },

    imageModel(modelId: string): never {
      throw new NoSuchModelError({ modelId, modelType: 'imageModel' });
    },
  };
}
