/** GET /api/memory/context — the three context tiers for the rail. */

import { handleContext } from "@/lib/handlers";
import { memoryClient, userId } from "@/lib/memory";

export async function GET(request: Request): Promise<Response> {
  return handleContext({ client: memoryClient(), userId: userId() }, request);
}
