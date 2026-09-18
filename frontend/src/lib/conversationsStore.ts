import { createRecentItemsStore } from "./recentItemsStore";

export interface RecentConversation {
  id: string;
  title: string;
  lastUpdated: number;
}

/**
 * Scoped per-user (by `userId`) so switching accounts on the same browser doesn't leak the
 * previous account's conversation titles into the new account's sidebar -- these are tracked
 * client-side only (no backend "list mine" endpoint, see the design spec), so without this
 * scoping the raw localStorage key would be shared across every account on one browser.
 */
export function getConversationsStore(userId: string) {
  return createRecentItemsStore<RecentConversation>(`rag-recent-conversations:${userId}`, 5);
}
