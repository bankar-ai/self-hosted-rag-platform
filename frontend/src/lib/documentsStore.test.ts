import { beforeEach, describe, expect, it } from "vitest";
import { getDocumentsStore } from "./documentsStore";

describe("getDocumentsStore", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("upserts a document's status as it changes", () => {
    const store = getDocumentsStore("user-a");
    store.upsert({ id: "job-1", title: "a.pdf", lastUpdated: 1, status: "pending" });
    store.upsert({ id: "job-1", title: "a.pdf", lastUpdated: 2, status: "done" });

    const list = store.list();
    expect(list).toHaveLength(1);
    expect(list[0].status).toBe("done");
  });

  it("scopes storage by user ID so two accounts don't see each other's documents", () => {
    const storeA = getDocumentsStore("user-a");
    const storeB = getDocumentsStore("user-b");
    storeA.upsert({ id: "job-a", title: "a.pdf", lastUpdated: 1, status: "done" });

    expect(storeA.list().map((d) => d.id)).toEqual(["job-a"]);
    expect(storeB.list()).toEqual([]);
  });
});
