import { beforeEach, describe, expect, it } from "vitest";
import { documentsStore } from "./documentsStore";

describe("documentsStore", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("upserts a document's status as it changes", () => {
    documentsStore.upsert({ id: "job-1", title: "a.pdf", lastUpdated: 1, status: "pending" });
    documentsStore.upsert({ id: "job-1", title: "a.pdf", lastUpdated: 2, status: "done" });

    const list = documentsStore.list();
    expect(list).toHaveLength(1);
    expect(list[0].status).toBe("done");
  });
});
