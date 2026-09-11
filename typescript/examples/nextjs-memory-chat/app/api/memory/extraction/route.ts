/** POST /api/memory/extraction — await background entity extraction. */

import { handleExtractionStatus } from "@/lib/handlers";
import { memoryClient, userId } from "@/lib/memory";

// Extraction can take a few seconds; keep the request alive for it.
export const maxDuration = 60;

export async function POST(request: Request): Promise<Response> {
  return handleExtractionStatus({ client: memoryClient(), userId: userId() }, request);
}
