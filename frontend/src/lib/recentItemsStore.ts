interface HasId {
  id: string;
}

/**
 * A small localStorage-backed "most recent N items" list, most-recently-upserted first.
 * Used for both the chat sidebar's conversation list and the documents page's upload list --
 * the backend has no "list mine" endpoint for either (conversation_id/document identity are
 * client-known, not server-enumerable), so the frontend tracks the last `limit` itself.
 */
export function createRecentItemsStore<T extends HasId>(storageKey: string, limit: number) {
  function readAll(): T[] {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return [];
    try {
      return JSON.parse(raw) as T[];
    } catch {
      return [];
    }
  }

  function writeAll(items: T[]): void {
    localStorage.setItem(storageKey, JSON.stringify(items));
  }

  return {
    list(): T[] {
      return readAll();
    },
    upsert(item: T): void {
      const withoutExisting = readAll().filter((existing) => existing.id !== item.id);
      const next = [item, ...withoutExisting].slice(0, limit);
      writeAll(next);
    },
  };
}
