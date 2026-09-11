/** GET /api/memory/trace — reasoning steps recorded for this conversation. */

import { handleTrace } from "@/lib/handlers";
import { memoryClient, userId } from "@/lib/memory";

export async function GET(request: Request): Promise<Response> {
  return handleTrace({ client: memoryClient(), userId: userId() }, request);
}
