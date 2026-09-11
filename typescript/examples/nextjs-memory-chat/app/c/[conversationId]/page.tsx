/**
 * `/c/<conversationId>` — the app.
 *
 * Next 16 hands `params` over as a promise; the synchronous form was removed.
 */

import { Workspace } from "@/components/Workspace";

export const dynamic = "force-dynamic";

export default async function ConversationPage({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const { conversationId } = await params;
  return <Workspace conversationId={conversationId} />;
}
