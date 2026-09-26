import { createRecentItemsStore } from "./recentItemsStore";

export interface RecentDocument {
  id: string;
  title: string;
  lastUpdated: number;
  status: "pending" | "processing" | "done" | "failed";
  error?: string;
  /** Epoch ms this job was first seen by this browser (ERP-091) -- unlike `lastUpdated`, never
   * overwritten by a later poll tick, so elapsed-time-based UI (e.g. a cold-start hint) has a
   * stable reference point instead of one that resets to "now" every 2 seconds. */
  startedAt?: number;
}

/** Scoped per-user, same reasoning as `getConversationsStore`. */
export function getDocumentsStore(userId: string) {
  return createRecentItemsStore<RecentDocument>(`rag-recent-documents:${userId}`, 5);
}
