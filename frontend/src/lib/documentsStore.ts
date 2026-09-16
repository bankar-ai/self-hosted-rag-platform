import { createRecentItemsStore } from "./recentItemsStore";

export interface RecentDocument {
  id: string;
  title: string;
  lastUpdated: number;
  status: "pending" | "processing" | "done" | "failed";
  error?: string;
}

export const documentsStore = createRecentItemsStore<RecentDocument>("rag-recent-documents", 5);
