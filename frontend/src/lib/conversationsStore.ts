import { createRecentItemsStore } from "./recentItemsStore";

export interface RecentConversation {
  id: string;
  title: string;
  lastUpdated: number;
}

export const conversationsStore = createRecentItemsStore<RecentConversation>(
  "rag-recent-conversations",
  5
);
