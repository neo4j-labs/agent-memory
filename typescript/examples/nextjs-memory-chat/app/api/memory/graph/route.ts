/**
 * GET  /api/memory/graph — the whole entity graph.
 * POST /api/memory/graph — one hop out from a node, minus what is on screen.
 */

import { handleExpandGraph, handleGraph } from "@/lib/handlers";
import { memoryClient, userId } from "@/lib/memory";

export async function GET(): Promise<Response> {
  return handleGraph({ client: memoryClient(), userId: userId() });
}

export async function POST(request: Request): Promise<Response> {
  return handleExpandGraph({ client: memoryClient(), userId: userId() }, request);
}
