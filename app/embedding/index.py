"""A FAISS vector index persisted to a local file, and a per-owner registry over it."""

import os
import threading
import uuid
from pathlib import Path
from typing import cast

import faiss
import numpy as np

from app.core.telemetry import get_tracer


class FaissIndex:
    """A flat L2 FAISS index, addressable by explicit int64 IDs, persisted to `path`."""

    def __init__(self, path: str, dimension: int) -> None:
        """Load the index at `path` if it exists, otherwise create an empty one."""
        self._path = path
        self._dimension = dimension
        self._index = self._load_or_create()

    def _load_or_create(self) -> faiss.IndexIDMap2:
        if os.path.exists(self._path):
            return cast(faiss.IndexIDMap2, faiss.read_index(self._path))
        # IndexIDMap2 (not the plain IndexIDMap) is required for `reconstruct()` to work:
        # it maintains an explicit id -> position direct map, whereas IndexIDMap only
        # supports add/search and cannot translate an external id back for reconstruction.
        return faiss.IndexIDMap2(faiss.IndexFlatL2(self._dimension))

    @property
    def ntotal(self) -> int:
        """Number of vectors currently in the index."""
        return int(self._index.ntotal)

    def add(self, vector_ids: list[int], vectors: list[list[float]]) -> None:
        """Add `vectors`, keyed by the parallel `vector_ids`. No-op if either is empty."""
        if not vector_ids:
            return
        ids = np.array(vector_ids, dtype="int64")
        matrix = np.array(vectors, dtype="float32")
        self._index.add_with_ids(matrix, ids)

    def remove(self, vector_ids: list[int]) -> None:
        """Remove `vector_ids` from the index. No-op if empty; unknown ids are silently ignored."""
        if not vector_ids:
            return
        ids = np.array(vector_ids, dtype="int64")
        self._index.remove_ids(ids)  # type: ignore[arg-type]

    def save(self) -> None:
        """Persist the index to `path`, creating parent directories if needed."""
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        faiss.write_index(self._index, self._path)

    def reconstruct(self, vector_id: int) -> list[float]:
        """Return the raw vector stored under `vector_id`.

        Supported because this index is always a flat (non-quantized) index, which stores
        vectors verbatim rather than a lossy compressed representation, and because the
        index is an `IndexIDMap2` (not the plain `IndexIDMap`), which maintains the explicit
        id -> position direct map `reconstruct` needs. Used by the per-owner migration
        script (`app.embedding.migrate_to_per_owner`) to read vectors back out of a legacy
        shared index for redistribution -- not used by normal add/search request paths.
        """
        vector = cast("np.ndarray[tuple[int], np.dtype[np.float32]]", self._index.reconstruct(vector_id))
        return vector.tolist()

    def search(self, vector: list[float], k: int) -> list[tuple[int, float]]:
        """Return up to `k` nearest `(vector_id, distance)` pairs, nearest-first.

        Empty list if the index has no vectors or `k <= 0`. Padding entries FAISS
        returns when the index has fewer than `k` vectors (`vector_id == -1`) are
        dropped.
        """
        with get_tracer().start_as_current_span("faiss.search") as span:
            span.set_attribute("faiss.k", k)
            if self._index.ntotal == 0 or k <= 0:
                span.set_attribute("faiss.hits", 0)
                return []
            query = np.array([vector], dtype="float32")
            distances, ids = self._index.search(query, k)
            hits = [
                (int(vector_id), float(distance))
                for vector_id, distance in zip(ids[0], distances[0], strict=True)
                if vector_id != -1
            ]
            span.set_attribute("faiss.hits", len(hits))
            return hits


class OwnerFaissIndexStore:
    """Lazily creates/loads one on-disk `FaissIndex` per owner, keyed by `owner_id`.

    Each owner's vectors live in a physically separate index file (`<index_dir>/<owner_id>.bin`),
    so a search scoped to one owner cannot -- even in principle -- return another owner's
    vectors; there is no shared index to leak across, unlike the pre-ERP-031 design where
    isolation depended on filtering a shared index's results after the fact. This also keeps
    the door open for future sharding: since every operation is already keyed by `owner_id`
    rather than assuming one shared filesystem, splitting owners across hosts/processes later
    only requires changing how a path is resolved for a given owner, not any caller of this
    class.

    A per-owner lock serializes that owner's `add`/`save` calls (ingestion runs on background
    threads, and two concurrent ingestions for the same owner must not race on the same
    in-memory FAISS object or interleave writes to the same file); different owners never
    contend with each other.
    """

    def __init__(self, index_dir: str, dimension: int) -> None:
        """Root new/loaded owner indexes at `index_dir`, each of dimension `dimension`."""
        self._index_dir = index_dir
        self._dimension = dimension
        self._indexes: dict[uuid.UUID, FaissIndex] = {}
        self._registry_lock = threading.Lock()
        self._owner_locks: dict[uuid.UUID, threading.Lock] = {}

    def path_for(self, owner_id: uuid.UUID) -> str:
        """Return the on-disk path `owner_id`'s index is (or would be) persisted at."""
        return str(Path(self._index_dir) / f"{owner_id}.bin")

    def _lock_for(self, owner_id: uuid.UUID) -> threading.Lock:
        with self._registry_lock:
            lock = self._owner_locks.get(owner_id)
            if lock is None:
                lock = threading.Lock()
                self._owner_locks[owner_id] = lock
            return lock

    def _index_for(self, owner_id: uuid.UUID) -> FaissIndex:
        with self._registry_lock:
            index = self._indexes.get(owner_id)
            if index is None:
                index = FaissIndex(self.path_for(owner_id), self._dimension)
                self._indexes[owner_id] = index
            return index

    def add(self, owner_id: uuid.UUID, vector_ids: list[int], vectors: list[list[float]]) -> None:
        """Add `vectors` to `owner_id`'s index and persist it. No-op if `vector_ids` is empty."""
        if not vector_ids:
            return
        with self._lock_for(owner_id):
            index = self._index_for(owner_id)
            index.add(vector_ids, vectors)
            index.save()

    def search(self, owner_id: uuid.UUID, vector: list[float], k: int) -> list[tuple[int, float]]:
        """Search only `owner_id`'s index. `[]` if that owner has no index yet."""
        with self._lock_for(owner_id):
            return self._index_for(owner_id).search(vector, k)

    def remove(self, owner_id: uuid.UUID, vector_ids: list[int]) -> None:
        """Remove `vector_ids` from `owner_id`'s index and persist it. No-op if `vector_ids` is empty."""
        if not vector_ids:
            return
        with self._lock_for(owner_id):
            index = self._index_for(owner_id)
            index.remove(vector_ids)
            index.save()
