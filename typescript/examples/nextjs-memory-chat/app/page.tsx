/**
 * `/` mints a conversation and redirects to `/c/<id>`.
 *
 * The conversation id lives in the URL, not in React state or `localStorage`:
 * that URL is the only handle on the thread, and it is shareable, reloadable and
 * resumable because every message behind it is in NAMS.
 *
 * `force-dynamic` keeps this off the build-time prerender path — `npm run build`
 * must succeed on a machine with no `MEMORY_API_KEY`.
 */

import { redirect } from "next/navigation";

import { SetupNotice } from "@/components/SetupNotice";
import { memoryClient, userId } from "@/lib/memory";

export const dynamic = "force-dynamic";

export default async function Home() {
  let conversationId: string | undefined;
  let failure: string | undefined;

  try {
    const conversation = await memoryClient().shortTerm.createConversation({
      userId: userId(),
      metadata: { source: "nextjs-memory-chat" },
    });
    conversationId = conversation.id;
  } catch (error) {
    // `redirect()` signals by throwing, so it must stay outside this try.
    failure = error instanceof Error ? error.message : String(error);
  }

  if (conversationId) redirect(`/c/${conversationId}`);

  return <SetupNotice message={failure ?? "Could not create a conversation."} />;
}
