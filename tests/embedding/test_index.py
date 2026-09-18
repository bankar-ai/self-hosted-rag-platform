import uuid

import pytest

from app.embedding.index import FaissIndex, OwnerFaissIndexStore


def test_new_index_starts_empty(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    assert index.ntotal == 0


def test_add_increases_ntotal(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add([1, 2], [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]])
    assert index.ntotal == 2


def test_add_empty_is_a_noop(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add([], [])
    assert index.ntotal == 0


def test_save_and_reload_preserves_vectors(tmp_path):
    path = str(tmp_path / "nested" / "index.bin")
    index = FaissIndex(path, dimension=4)
    index.add([1, 2], [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]])
    index.save()

    reloaded = FaissIndex(path, dimension=4)
    assert reloaded.ntotal == 2


def test_search_on_empty_index_returns_empty_list(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    assert index.search([0.1, 0.2, 0.3, 0.4], k=5) == []


def test_search_returns_nearest_first(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add(
        [1, 2, 3],
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.9, 0.1, 0.0, 0.0],
        ],
    )
    results = index.search([1.0, 0.0, 0.0, 0.0], k=2)
    assert [vector_id for vector_id, _ in results] == [1, 3]
    assert results[0][1] < results[1][1]


def test_search_k_larger_than_ntotal_returns_all_available(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add([1, 2], [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])
    results = index.search([1.0, 0.0, 0.0, 0.0], k=10)
    assert len(results) == 2
    assert {vector_id for vector_id, _ in results} == {1, 2}


def test_reconstruct_returns_the_original_vector(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add([7], [[1.0, 2.0, 3.0, 4.0]])
    assert index.reconstruct(7) == pytest.approx([1.0, 2.0, 3.0, 4.0])


def test_owner_store_search_on_unknown_owner_returns_empty_list_without_creating_a_file(tmp_path):
    store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    owner_id = uuid.uuid4()

    assert store.search(owner_id, [1.0, 0.0, 0.0, 0.0], k=5) == []
    assert not (tmp_path / f"{owner_id}.bin").exists()


def test_owner_store_add_creates_a_separate_file_per_owner(tmp_path):
    store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    owner_a, owner_b = uuid.uuid4(), uuid.uuid4()

    store.add(owner_a, [1], [[1.0, 0.0, 0.0, 0.0]])
    store.add(owner_b, [1], [[0.0, 1.0, 0.0, 0.0]])

    assert (tmp_path / f"{owner_a}.bin").exists()
    assert (tmp_path / f"{owner_b}.bin").exists()


def test_owner_store_owners_never_see_each_others_vectors(tmp_path):
    """The core isolation guarantee: no filtering step is involved at all here.

    Owner B's vector is a near-perfect match for the query, and owner A's is a poor match --
    if owner A's search could see the shared underlying data at all, owner B's vector would
    win. It can't, because it was never added to owner A's index file.
    """
    store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    owner_a, owner_b = uuid.uuid4(), uuid.uuid4()

    store.add(owner_a, [1], [[0.0, 0.0, 0.0, 1.0]])  # poor match for the query below
    store.add(owner_b, [2], [[1.0, 0.0, 0.0, 0.0]])  # perfect match for the query below

    results = store.search(owner_a, [1.0, 0.0, 0.0, 0.0], k=5)

    assert [vector_id for vector_id, _ in results] == [1]


def test_owner_store_add_is_a_noop_for_empty_vector_ids(tmp_path):
    store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    owner_id = uuid.uuid4()

    store.add(owner_id, [], [])

    assert not (tmp_path / f"{owner_id}.bin").exists()


def test_owner_store_reloading_an_owners_index_preserves_previously_added_vectors(tmp_path):
    owner_id = uuid.uuid4()
    first_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    first_store.add(owner_id, [1], [[1.0, 0.0, 0.0, 0.0]])

    second_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    results = second_store.search(owner_id, [1.0, 0.0, 0.0, 0.0], k=5)

    assert [vector_id for vector_id, _ in results] == [1]


def test_owner_store_add_across_multiple_calls_accumulates_in_the_same_owner_index(tmp_path):
    store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    owner_id = uuid.uuid4()

    store.add(owner_id, [1], [[1.0, 0.0, 0.0, 0.0]])
    store.add(owner_id, [2], [[0.0, 1.0, 0.0, 0.0]])

    results = store.search(owner_id, [1.0, 0.0, 0.0, 0.0], k=5)
    assert {vector_id for vector_id, _ in results} == {1, 2}


def test_remove_deletes_a_vector_leaving_others_searchable(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add([1, 2], [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])

    index.remove([1])

    assert index.ntotal == 1
    results = index.search([1.0, 0.0, 0.0, 0.0], k=5)
    assert [vector_id for vector_id, _ in results] == [2]


def test_remove_empty_is_a_noop(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add([1], [[1.0, 0.0, 0.0, 0.0]])

    index.remove([])

    assert index.ntotal == 1


def test_remove_unknown_id_is_a_noop(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add([1], [[1.0, 0.0, 0.0, 0.0]])

    index.remove([999])

    assert index.ntotal == 1


def test_owner_store_remove_deletes_only_that_owners_vector(tmp_path):
    store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    owner_a, owner_b = uuid.uuid4(), uuid.uuid4()
    store.add(owner_a, [1], [[1.0, 0.0, 0.0, 0.0]])
    store.add(owner_b, [1], [[0.0, 1.0, 0.0, 0.0]])

    store.remove(owner_a, [1])

    assert store.search(owner_a, [1.0, 0.0, 0.0, 0.0], k=5) == []
    assert len(store.search(owner_b, [0.0, 1.0, 0.0, 0.0], k=5)) == 1


def test_owner_store_remove_persists_across_reload(tmp_path):
    owner_id = uuid.uuid4()
    first_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    first_store.add(owner_id, [1], [[1.0, 0.0, 0.0, 0.0]])
    first_store.remove(owner_id, [1])

    second_store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    assert second_store.search(owner_id, [1.0, 0.0, 0.0, 0.0], k=5) == []


def test_owner_store_remove_empty_is_a_noop_without_creating_a_file(tmp_path):
    store = OwnerFaissIndexStore(str(tmp_path), dimension=4)
    owner_id = uuid.uuid4()

    store.remove(owner_id, [])

    assert not (tmp_path / f"{owner_id}.bin").exists()
