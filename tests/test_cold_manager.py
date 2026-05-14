"""Tests for the RAG Runtime Kernel COLD partition manager.

Coverage targets:
- Lazy loading (partitions not loaded until get())
- Cache behavior (second get() is a cache hit)
- Partition listing (all vs. loaded)
- Eviction (single, all)
- Token estimation (per-partition, total, unloaded = 0)
- Persistence (update_partition writes to disk atomically)
- Refresh (re-reads file, evicts cache)
- Error handling (missing file, corrupt JSON, missing partition)
- Thread safety (concurrent get() calls)
- Summary / repr
- Edge cases (empty file, non-dict root)
"""

import json
import threading

import pytest

from rag_kernel.cold_manager import (
    CHARS_PER_TOKEN,
    ColdError,
    ColdFileError,
    ColdManager,
    PartitionNotFoundError,
    estimate_tokens,
)


# ===== Helpers =====

SAMPLE_COLD = {
    "meta": {
        "type": "RAG_COLD",
        "schema_version": "5.1",
    },
    "documents_inventory": {
        "last_scan_utc": "2026-05-02T23:20:00Z",
        "files": [
            {"p": "README.md", "sz": 1024},
            {"p": "main.py", "sz": 2048},
        ],
    },
    "conflict_ledger": {
        "entries": [
            {"id": "C-001", "resolved": True},
        ],
    },
    "session_history": [
        {"session": "S1", "ts": "2026-05-01"},
        {"session": "S2", "ts": "2026-05-02"},
    ],
}


@pytest.fixture
def cold_file(tmp_path):
    """Create a sample COLD file and return its path."""
    path = tmp_path / "RAG_COLD.json"
    path.write_text(
        json.dumps(SAMPLE_COLD, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def cold(cold_file):
    """ColdManager instance with sample data."""
    return ColdManager(cold_file)


# ===== estimate_tokens =====

class TestEstimateTokens:
    def test_empty_dict(self):
        assert estimate_tokens({}) == len("{}") // CHARS_PER_TOKEN

    def test_simple_string(self):
        data = "hello world"
        expected = len(json.dumps(data, separators=(",", ":"))) // CHARS_PER_TOKEN
        assert estimate_tokens(data) == expected

    def test_nested_structure(self):
        data = {"a": [1, 2, 3], "b": {"c": "d"}}
        result = estimate_tokens(data)
        assert result > 0
        assert isinstance(result, int)

    def test_consistent(self):
        data = {"key": "value"}
        assert estimate_tokens(data) == estimate_tokens(data)


# ===== Construction and index =====

class TestConstruction:
    def test_builds_index_on_init(self, cold):
        assert set(cold.partitions) == set(SAMPLE_COLD.keys())

    def test_nothing_loaded_on_init(self, cold):
        assert cold.loaded_partitions == []

    def test_partition_count(self, cold):
        assert len(cold.partitions) == 4

    def test_has_partition(self, cold):
        assert cold.has_partition("meta")
        assert cold.has_partition("documents_inventory")
        assert not cold.has_partition("nonexistent")

    def test_is_loaded_false_initially(self, cold):
        assert not cold.is_loaded("meta")

    def test_path_property(self, cold, cold_file):
        assert cold.path == cold_file


# ===== Missing / corrupt file =====

class TestFileErrors:
    def test_missing_file_empty_index(self, tmp_path):
        path = tmp_path / "does_not_exist.json"
        cold = ColdManager(path)
        assert cold.partitions == []
        assert cold.loaded_partitions == []

    def test_corrupt_json_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{bad json", encoding="utf-8")
        with pytest.raises(ColdFileError, match="not valid JSON"):
            ColdManager(path)

    def test_non_dict_root_raises(self, tmp_path):
        path = tmp_path / "array.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ColdFileError, match="JSON object"):
            ColdManager(path)

    def test_empty_json_object(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("{}", encoding="utf-8")
        cold = ColdManager(path)
        assert cold.partitions == []

    def test_get_from_missing_file(self, tmp_path):
        path = tmp_path / "nope.json"
        cold = ColdManager(path)
        with pytest.raises(PartitionNotFoundError):
            cold.get("anything")


# ===== Lazy loading =====

class TestLazyLoading:
    def test_get_loads_partition(self, cold):
        result = cold.get("meta")
        assert result == SAMPLE_COLD["meta"]
        assert cold.is_loaded("meta")

    def test_other_partitions_not_loaded(self, cold):
        cold.get("meta")
        assert not cold.is_loaded("documents_inventory")
        assert not cold.is_loaded("conflict_ledger")
        assert not cold.is_loaded("session_history")

    def test_second_get_is_cache_hit(self, cold, cold_file):
        first = cold.get("meta")
        # Modify the file — cache should still return old value
        data = json.loads(cold_file.read_text(encoding="utf-8"))
        data["meta"]["schema_version"] = "99.0"
        cold_file.write_text(json.dumps(data), encoding="utf-8")
        second = cold.get("meta")
        assert second is first  # same object, not re-read

    def test_get_nonexistent_raises(self, cold):
        with pytest.raises(PartitionNotFoundError) as exc_info:
            cold.get("nonexistent")
        assert "nonexistent" in str(exc_info.value)
        assert "meta" in exc_info.value.available

    def test_get_all_loads_everything(self, cold):
        result = cold.get_all()
        assert set(result.keys()) == set(SAMPLE_COLD.keys())
        assert len(cold.loaded_partitions) == 4

    def test_get_returns_correct_data(self, cold):
        inv = cold.get("documents_inventory")
        assert inv["last_scan_utc"] == "2026-05-02T23:20:00Z"
        assert len(inv["files"]) == 2

    def test_get_session_history(self, cold):
        hist = cold.get("session_history")
        assert isinstance(hist, list)
        assert len(hist) == 2
        assert hist[0]["session"] == "S1"


# ===== Eviction =====

class TestEviction:
    def test_evict_loaded_partition(self, cold):
        cold.get("meta")
        assert cold.is_loaded("meta")
        result = cold.evict("meta")
        assert result is True
        assert not cold.is_loaded("meta")

    def test_evict_unloaded_partition(self, cold):
        result = cold.evict("meta")
        assert result is False

    def test_evict_then_reload(self, cold):
        cold.get("meta")
        cold.evict("meta")
        # Should reload from file
        result = cold.get("meta")
        assert result == SAMPLE_COLD["meta"]
        assert cold.is_loaded("meta")

    def test_evict_all(self, cold):
        cold.get_all()
        assert len(cold.loaded_partitions) == 4
        count = cold.evict_all()
        assert count == 4
        assert cold.loaded_partitions == []

    def test_evict_all_empty(self, cold):
        assert cold.evict_all() == 0

    def test_partition_still_in_index_after_evict(self, cold):
        cold.get("meta")
        cold.evict("meta")
        assert cold.has_partition("meta")
        assert "meta" in cold.partitions


# ===== Token estimation =====

class TestTokenEstimation:
    def test_no_loaded_zero_tokens(self, cold):
        assert cold.token_estimate() == 0

    def test_single_partition_tokens(self, cold):
        cold.get("meta")
        tokens = cold.token_estimate("meta")
        assert tokens > 0
        expected = estimate_tokens(SAMPLE_COLD["meta"])
        assert tokens == expected

    def test_total_tokens(self, cold):
        cold.get("meta")
        cold.get("documents_inventory")
        total = cold.token_estimate()
        meta_tokens = cold.token_estimate("meta")
        inv_tokens = cold.token_estimate("documents_inventory")
        assert total == meta_tokens + inv_tokens

    def test_unloaded_partition_zero_tokens(self, cold):
        tokens = cold.token_estimate("meta")
        assert tokens == 0

    def test_nonexistent_partition_raises(self, cold):
        with pytest.raises(PartitionNotFoundError):
            cold.token_estimate("nonexistent")

    def test_tokens_decrease_after_evict(self, cold):
        cold.get_all()
        before = cold.token_estimate()
        cold.evict("documents_inventory")
        after = cold.token_estimate()
        assert after < before


# ===== Update partition (persistence) =====

class TestUpdatePartition:
    def test_update_existing_partition(self, cold, cold_file):
        new_meta = {"type": "RAG_COLD", "schema_version": "6.0"}
        cold.update_partition("meta", new_meta)

        # In-memory cache updated
        assert cold.get("meta") == new_meta

        # On-disk file updated
        on_disk = json.loads(cold_file.read_text(encoding="utf-8"))
        assert on_disk["meta"] == new_meta

        # Other partitions preserved
        assert on_disk["documents_inventory"] == SAMPLE_COLD["documents_inventory"]

    def test_create_new_partition(self, cold, cold_file):
        cold.update_partition("new_section", {"data": [1, 2, 3]})
        assert cold.has_partition("new_section")
        assert cold.get("new_section") == {"data": [1, 2, 3]}

        on_disk = json.loads(cold_file.read_text(encoding="utf-8"))
        assert "new_section" in on_disk

    def test_update_creates_backup(self, cold, cold_file):
        cold.update_partition("meta", {"updated": True})
        bak_path = cold_file.with_suffix(".json.bak")
        assert bak_path.exists()

    def test_update_nonexistent_file(self, tmp_path):
        path = tmp_path / "new_cold.json"
        cold = ColdManager(path)
        cold.update_partition("first", {"hello": "world"})
        assert path.exists()
        assert cold.get("first") == {"hello": "world"}


# ===== Refresh =====

class TestRefresh:
    def test_refresh_evicts_cache(self, cold):
        cold.get("meta")
        cold.refresh()
        assert not cold.is_loaded("meta")

    def test_refresh_picks_up_new_partitions(self, cold, cold_file):
        # Modify file externally
        data = json.loads(cold_file.read_text(encoding="utf-8"))
        data["new_partition"] = {"added": True}
        cold_file.write_text(json.dumps(data), encoding="utf-8")

        cold.refresh()
        assert cold.has_partition("new_partition")
        assert cold.get("new_partition") == {"added": True}

    def test_refresh_removes_deleted_partitions(self, cold, cold_file):
        assert cold.has_partition("conflict_ledger")

        data = json.loads(cold_file.read_text(encoding="utf-8"))
        del data["conflict_ledger"]
        cold_file.write_text(json.dumps(data), encoding="utf-8")

        cold.refresh()
        assert not cold.has_partition("conflict_ledger")


# ===== Summary and repr =====

class TestSummaryRepr:
    def test_summary_structure(self, cold):
        cold.get("meta")
        s = cold.summary()
        assert s["total_partitions"] == 4
        assert s["loaded_partitions"] == 1
        assert "meta" in s["loaded_names"]
        assert s["estimated_tokens"] > 0
        assert "path" in s

    def test_summary_empty(self, tmp_path):
        path = tmp_path / "nope.json"
        cold = ColdManager(path)
        s = cold.summary()
        assert s["total_partitions"] == 0
        assert s["loaded_partitions"] == 0
        assert s["estimated_tokens"] == 0

    def test_repr(self, cold):
        r = repr(cold)
        assert "ColdManager" in r
        assert "partitions=4" in r
        assert "loaded=0" in r

    def test_repr_after_load(self, cold):
        cold.get("meta")
        r = repr(cold)
        assert "loaded=1" in r


# ===== Thread safety =====

class TestThreadSafety:
    def test_concurrent_gets(self, cold):
        """Multiple threads loading different partitions simultaneously."""
        results = {}
        barrier = threading.Barrier(4)

        def load_partition(name):
            barrier.wait()
            results[name] = cold.get(name)

        threads = []
        for name in SAMPLE_COLD.keys():
            t = threading.Thread(target=load_partition, args=(name,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 4
        assert results["meta"] == SAMPLE_COLD["meta"]
        assert len(cold.loaded_partitions) == 4

    def test_concurrent_get_same_partition(self, cold):
        """Multiple threads loading the same partition — no crashes, same data."""
        results = []
        barrier = threading.Barrier(10)

        def load_meta():
            barrier.wait()
            results.append(cold.get("meta"))

        threads = [threading.Thread(target=load_meta) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        # All should be the same object (cache hit after first load)
        assert all(r == SAMPLE_COLD["meta"] for r in results)

    def test_concurrent_reads_safe(self, cold):
        """Reading partitions and token_estimate concurrently."""
        cold.get_all()
        results = []

        def read_tokens():
            for _ in range(50):
                results.append(cold.token_estimate())

        threads = [threading.Thread(target=read_tokens) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 250
        assert all(t > 0 for t in results)


# ===== Edge cases =====

class TestEdgeCases:
    def test_partition_with_none_value(self, tmp_path):
        path = tmp_path / "cold.json"
        path.write_text('{"nullpart": null}', encoding="utf-8")
        cold = ColdManager(path)
        assert cold.has_partition("nullpart")
        result = cold.get("nullpart")
        assert result is None

    def test_partition_with_empty_list(self, tmp_path):
        path = tmp_path / "cold.json"
        path.write_text('{"empty": []}', encoding="utf-8")
        cold = ColdManager(path)
        assert cold.get("empty") == []

    def test_large_partition(self, tmp_path):
        path = tmp_path / "cold.json"
        big = {"items": [{"id": i, "data": "x" * 100} for i in range(1000)]}
        data = {"big_partition": big}
        path.write_text(json.dumps(data), encoding="utf-8")
        cold = ColdManager(path)
        result = cold.get("big_partition")
        assert len(result["items"]) == 1000
        assert cold.token_estimate() > 100

    def test_unicode_partition(self, tmp_path):
        path = tmp_path / "cold.json"
        data = {"données": {"contenu": "café résumé"}}
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        cold = ColdManager(path)
        result = cold.get("données")
        assert result["contenu"] == "café résumé"
