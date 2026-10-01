/**
 * Next.js App Router chat route with NAMS memory.
 *
 * Copy to `app/api/chat/route.ts` and use `useChat()` from `@ai-sdk/react`.
 * Memory is automatic for each user.
 */

import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, createAgentUIStreamResponse, stepCountIs, type UIMessage } from 'ai';
// In your app, import from the published package instead:
//   import { createNamsProvider } from '@neo4j-labs/nams-ai-provider';
import { createNamsProvider } from '../src/index';

export const maxDuration = 30;

const model = process.env.NAMS_DEMO_MODEL ?? 'gpt-5.4-mini';

export async function POST(req: Request): Promise<Response> {
  const { messages, userId }: { messages: UIMessage[]; userId: string } = await req.json();

  // One provider per request, for this user.
  const nams = createNamsProvider({
    apiKey: process.env.MEMORY_API_KEY!,
    baseProvider: openai,
    scope: { userId },
  });

  const agent = new ToolLoopAgent({
    model: nams.languageModel(model),
    instructions: 'You are a helpful assistant.',
    stopWhen: stepCountIs(1), // no tools needed in provider mode
  });

  return createAgentUIStreamResponse({ agent, uiMessages: messages });
}
