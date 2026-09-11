/** POST /api/conversation — create the conversation whose id lives in the URL. */

import { handleCreateConversation } from "@/lib/handlers";
import { memoryClient, userId } from "@/lib/memory";

export async function POST(): Promise<Response> {
  return handleCreateConversation({ client: memoryClient(), userId: userId() });
}
