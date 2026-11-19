// Next.js chat route with memory. Copy to `app/api/chat/route.ts` and use `useChat()` from `@ai-sdk/react`.

import { openai } from '@ai-sdk/openai';
import { ToolLoopAgent, createAgentUIStreamResponse, isStepCount, type UIMessage } from 'ai';
// In your app: import { createNamsProvider } from '@neo4j-labs/nams-ai-provider';
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
    stopWhen: isStepCount(1), // no tools needed in provider mode
  });

  return createAgentUIStreamResponse({ agent, uiMessages: messages });
}
