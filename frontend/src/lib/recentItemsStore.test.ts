import { beforeEach, describe, expect, it } from "vitest";
import { createRecentItemsStore } from "./recentItemsStore";

interface Item {
  id: string;
  title: string;
  lastUpdated: number;
}

describe("createRecentItemsStore", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("upserts a new item to the front of the list", () => {
    const store = createRecentItemsStore<Item>("test-items", 5);
    store.upsert({ id: "a", title: "First", lastUpdated: 1 });
    store.upsert({ id: "b", title: "Second", lastUpdated: 2 });

    expect(store.list().map((i) => i.id)).toEqual(["b", "a"]);
  });

  it("updates an existing item in place rather than duplicating it", () => {
    const store = createRecentItemsStore<Item>("test-items", 5);
    store.upsert({ id: "a", title: "First", lastUpdated: 1 });
    store.upsert({ id: "a", title: "First (renamed)", lastUpdated: 2 });

    const list = store.list();
    expect(list).toHaveLength(1);
    expect(list[0].title).toBe("First (renamed)");
  });

  it("trims to the configured limit, dropping the oldest", () => {
    const store = createRecentItemsStore<Item>("test-items", 2);
    store.upsert({ id: "a", title: "A", lastUpdated: 1 });
    store.upsert({ id: "b", title: "B", lastUpdated: 2 });
    store.upsert({ id: "c", title: "C", lastUpdated: 3 });

    expect(store.list().map((i) => i.id)).toEqual(["c", "b"]);
  });

  it("persists across store instances via localStorage", () => {
    createRecentItemsStore<Item>("test-items", 5).upsert({ id: "a", title: "A", lastUpdated: 1 });

    const secondInstance = createRecentItemsStore<Item>("test-items", 5);
    expect(secondInstance.list().map((i) => i.id)).toEqual(["a"]);
  });

  it("removes an item by id, leaving the rest untouched", () => {
    const store = createRecentItemsStore<Item>("test-items", 5);
    store.upsert({ id: "a", title: "A", lastUpdated: 1 });
    store.upsert({ id: "b", title: "B", lastUpdated: 2 });

    store.remove("a");

    expect(store.list().map((i) => i.id)).toEqual(["b"]);
  });

  it("removing an id that isn't present is a no-op", () => {
    const store = createRecentItemsStore<Item>("test-items", 5);
    store.upsert({ id: "a", title: "A", lastUpdated: 1 });

    store.remove("does-not-exist");

    expect(store.list().map((i) => i.id)).toEqual(["a"]);
  });
});
