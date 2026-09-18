import { beforeEach, describe, expect, it } from "vitest";
import { getConversationsStore } from "./conversationsStore";

describe("getConversationsStore", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("keeps at most the 5 most recent conversations", () => {
    const store = getConversationsStore("user-a");
    for (let i = 0; i < 7; i += 1) {
      store.upsert({ id: `conv-${i}`, title: `Conversation ${i}`, lastUpdated: i });
    }

    expect(store.list()).toHaveLength(5);
    expect(store.list()[0].id).toBe("conv-6");
  });

  it("scopes storage by user ID so two accounts don't see each other's conversations", () => {
    const storeA = getConversationsStore("user-a");
    const storeB = getConversationsStore("user-b");
    storeA.upsert({ id: "conv-a", title: "User A's chat", lastUpdated: 1 });

    expect(storeA.list().map((c) => c.id)).toEqual(["conv-a"]);
    expect(storeB.list()).toEqual([]);
  });
});
