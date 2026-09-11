/**
 * POST /api/chat — the memory-augmented chat endpoint `useChat` talks to.
 *
 * All behaviour lives in `lib/handlers.ts`; this file only supplies the
 * dependencies. `maxDuration` gives the stream room on Vercel's default plan.
 */

import { chatModel, memoryClient, userId } from "@/lib/memory";
import { handleChat } from "@/lib/handlers";

export const maxDuration = 60;

export async function POST(request: Request): Promise<Response> {
  return handleChat(
    { client: memoryClient(), model: chatModel(), userId: userId() },
    request,
  );
}
