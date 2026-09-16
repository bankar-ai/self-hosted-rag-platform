import { beforeEach, describe, expect, it } from "vitest";
import { conversationsStore } from "./conversationsStore";

describe("conversationsStore", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("keeps at most the 5 most recent conversations", () => {
    for (let i = 0; i < 7; i += 1) {
      conversationsStore.upsert({ id: `conv-${i}`, title: `Conversation ${i}`, lastUpdated: i });
    }

    expect(conversationsStore.list()).toHaveLength(5);
    expect(conversationsStore.list()[0].id).toBe("conv-6");
  });
});
