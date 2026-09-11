import { createContext, useContext } from "react";

/**
 * The chat session id that the backend uses as the `session_id` for
 * `Conversation` / `Message` / `ReasoningTrace` nodes in Neo4j.
 *
 * It is shared across routes so the Context Graph view can scope itself to the
 * conversation the user just had, and so reopening the chat replays the
 * reasoning traces stored for that session.
 */
export interface ChatSessionValue {
  sessionId: string | null;
  setSessionId: (sessionId: string | null) => void;
}

export const ChatSessionContext = createContext<ChatSessionValue>({
  sessionId: null,
  setSessionId: () => {},
});

export function useChatSession(): ChatSessionValue {
  return useContext(ChatSessionContext);
}

export const SESSION_STORAGE_KEY = "fsa-gcp.sessionId";

/** Read the last session id from localStorage (SSR/private-mode safe). */
export function loadStoredSessionId(): string | null {
  try {
    return window.localStorage.getItem(SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function storeSessionId(sessionId: string | null): void {
  try {
    if (sessionId) window.localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
    else window.localStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    // Ignore storage failures (private browsing, blocked site data).
  }
}
