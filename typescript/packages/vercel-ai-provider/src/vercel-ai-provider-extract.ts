import { generateText, Output, zodSchema } from 'ai';
import type { LanguageModel } from 'ai';
import { z } from 'zod';
import type { MemoryClient } from '@neo4j-labs/agent-memory';
import { GraphExtractor, StoreInput } from './vercel-ai-provider-types';
import { findEntity, getLogger, reportRelationshipFailure } from './vercel-ai-provider-client';

const graphSchema = z.object({
  entities: z
    .array(
      z.object({
        name: z.string().describe('Canonical entity name, e.g. "Alex", "Neovim", "TechCorp"'),
        type: z.string().describe('person | organization | tool | place | concept | preference | event'),
        description: z
          .string()
          .describe('Short description of the entity, or "" if the memory does not state one'),
      }),
    )
    .describe('Distinct, real, named entities only. Do not invent.'),
  relationships: z
    .array(
      z.object({
        from: z.string().describe('Source entity name (must match an entity above)'),
        to: z.string().describe('Target entity name (must match an entity above)'),
        type: z.string().describe('Relationship label, e.g. PREFERS, WORKS_AT, USES, LOCATED_IN'),
      }),
    )
    .describe('Relationships between the entities above, or [] if none are stated'),
});

const normalize = (s: string): string => s.trim().toLowerCase().replace(/\s+/g, ' ');

// Rough singular: "memories" -> "memory", "details" -> "detail".
const singular = (s: string): string =>
  s.endsWith('ies') ? `${s.slice(0, -3)}y` : s.endsWith('s') ? s.slice(0, -1) : s;

/** True for an all-lowercase word like "editor", i.e. not a proper name. */
const isCommonNoun = (name: string): boolean =>
  name === name.toLowerCase() && name !== name.toUpperCase();

/** True when an entity should be skipped: empty, a common noun, or just its type name. */
function isSelfReferential(name: string, type: string): boolean {
  const n = normalize(name);
  if (!n) return true;
  if (isCommonNoun(name.trim())) return true;
  return singular(n) === singular(normalize(type));
}

export interface GraphExtractorOptions {
  skipEntity?: (entity: { name: string; type: string; description: string }) => boolean;
}

/** An extractor that pulls entities and relationships out of a memory. One model call per memory. */
export function createGraphExtractor(
  model: LanguageModel,
  options: GraphExtractorOptions = {},
): GraphExtractor {
  const skipEntity = options.skipEntity ?? ((e) => isSelfReferential(e.name, e.type));

  return async function extractAndStore(client: MemoryClient, input: StoreInput): Promise<void> {
    const { output: object } = await generateText({
      model,
      output: Output.object({ schema: zodSchema(graphSchema) }),
      prompt:
        `Extract entities and the relationships between them from the memory below. ` +
        `Be conservative: only real, named entities; skip filler. Do not extract ` +
        `entities about memory, recall, or stored profiles — only the subjects ` +
        `they refer to.\n\n` +
        `Memory (${input.type}): ${input.content}`,
    });

    const log = getLogger(client);
    const nameToId = new Map<string, string>();

    for (const e of object.entities) {
      if (skipEntity(e)) {
        log.warn(`skipped self-referential entity "${e.name}" [${e.type}]`);
        continue;
      }
      // Reuse the existing entity so its relationships stay connected.
      const entity = await findEntity(client, e.name, e.type) ?? await client.longTerm.addEntity(e.name, e.type, {
        description: e.description?.trim() || input.content,
      });
      if (entity?.id) nameToId.set(e.name, entity.id);
      if (entity?.id && input.confidence !== undefined) {
        await client.longTerm
          .setEntityFeedback(entity.id, { userScore: input.confidence, confirmed: input.confidence >= 0.8 })
          .catch((e: unknown) => log.warn('setEntityFeedback failed', e));
      }
    }

    for (const r of object.relationships ?? []) {
      const from = nameToId.get(r.from);
      const to = nameToId.get(r.to);
      if (from && to) {
        await client.longTerm
          .addRelationship(from, to, r.type)
          .catch((e: unknown) => reportRelationshipFailure(client, e));
      }
    }
  };
}
