import { createRecentItemsStore } from "./recentItemsStore";

export interface RecentDocument {
  id: string;
  title: string;
  lastUpdated: number;
  status: "pending" | "processing" | "done" | "failed";
  error?: string;
}

/** Scoped per-user, same reasoning as `getConversationsStore`. */
export function getDocumentsStore(userId: string) {
  return createRecentItemsStore<RecentDocument>(`rag-recent-documents:${userId}`, 5);
}
