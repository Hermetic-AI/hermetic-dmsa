"""可选向量索引的 scope、恢复与故障契约测试。"""

from __future__ import annotations

from collections.abc import Sequence
from types import ModuleType, SimpleNamespace

import pytest

from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage
from dmsa.memory.coding.models import CodingMemoryError
from dmsa.memory.coding.vector_index import QdrantVectorIndex


class Embedder:
    """不依赖外部模型的确定性向量器。"""

    name = "test-vector-v1"
    dimension = 2

    def embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return [(1.0, float(len(text))) for text in texts]


class FakeIndex:
    """记录 scope 和 fingerprint 的内存索引。"""

    def __init__(self) -> None:
        self.points: dict[str, tuple[str, str, tuple[float, ...]]] = {}

    def upsert(
        self, user_id: str, fingerprint: str, ids: Sequence[str], vectors: Sequence[Sequence[float]]
    ) -> None:
        for point_id, vector in zip(ids, vectors, strict=True):
            self.points[point_id] = (user_id, fingerprint, tuple(vector))

    def search(
        self, user_id: str, fingerprint: str, vector: Sequence[float], limit: int
    ) -> list[str]:
        scoped = [
            (point_id, payload)
            for point_id, payload in self.points.items()
            if payload[:2] == (user_id, fingerprint)
        ]
        return [point_id for point_id, _ in sorted(scoped)[:limit]]

    def delete_scope(self, user_id: str) -> None:
        self.points = {key: value for key, value in self.points.items() if value[0] != user_id}

    def close(self) -> None:
        pass


def test_index_upsert_and_dense_search_are_scope_filtered(tmp_path) -> None:
    """外部索引只取同 scope 候选并由 SQLite 校验来源。"""
    index = FakeIndex()
    memory = CodingMemory(
        tmp_path / "memory.db",
        Embedder(),
        SearchPolicy(exact_weight=0, sparse_weight=0),
        vector_index=index,
    )
    memory.add("a", "alice", "s1", [SourceMessage("tool", "alpha evidence")])
    memory.add("b", "bob", "s2", [SourceMessage("tool", "secret evidence")])
    hits = memory.search("semantic query", "alice", 3)
    assert len(hits) == 1
    assert "alpha evidence" in hits[0].content
    assert "secret" not in hits[0].content
    memory.close()


def test_index_failure_does_not_commit_unindexed_sqlite_vector(tmp_path) -> None:
    """索引 upsert 失败时拒绝发布 SQL 记忆，避免静默降级。"""

    class BrokenIndex(FakeIndex):
        def upsert(self, user_id, fingerprint, ids, vectors) -> None:
            raise OSError("private endpoint detail")

    memory = CodingMemory(tmp_path / "memory.db", Embedder(), vector_index=BrokenIndex())
    with pytest.raises(CodingMemoryError, match="vector index"):
        memory.add("a", "alice", "s1", [SourceMessage("tool", "evidence")])
    assert memory.store.rows("alice") == []
    memory.close()


def test_delete_scope_removes_index_points(tmp_path) -> None:
    """删除用户 scope 时同步清除外部向量。"""
    index = FakeIndex()
    memory = CodingMemory(tmp_path / "memory.db", Embedder(), vector_index=index)
    memory.add("a", "alice", "s1", [SourceMessage("tool", "evidence")])
    assert index.points
    memory.delete_scope("alice")
    assert index.points == {}
    memory.close()


def test_qdrant_adapter_filters_scope_and_rechecks_sql(tmp_path, monkeypatch) -> None:
    """Qdrant adapter 将 scope filter 下推，且按来源 ID 返回确定顺序。"""

    class MatchValue:
        def __init__(self, value):
            self.value = value

    class FieldCondition:
        def __init__(self, key, match):
            self.key, self.match = key, match

    class Filter:
        def __init__(self, must):
            self.must = must

    class FilterSelector:
        def __init__(self, filter):
            self.filter = filter

    class PointStruct:
        def __init__(self, id, vector, payload):
            self.id, self.vector, self.payload = id, vector, payload

    class VectorParams:
        def __init__(self, size, distance):
            self.size, self.distance = size, distance

    models = ModuleType("qdrant_client.http.models")
    models.Distance = SimpleNamespace(COSINE="cosine")
    models.PayloadSchemaType = SimpleNamespace(KEYWORD="keyword")
    models.MatchValue = MatchValue
    models.FieldCondition = FieldCondition
    models.Filter = Filter
    models.FilterSelector = FilterSelector
    models.PointStruct = PointStruct
    models.VectorParams = VectorParams
    qdrant_package = ModuleType("qdrant_client")
    qdrant_package.__path__ = []
    http_package = ModuleType("qdrant_client.http")
    http_package.__path__ = []
    monkeypatch.setitem(__import__("sys").modules, "qdrant_client", qdrant_package)
    monkeypatch.setitem(__import__("sys").modules, "qdrant_client.http", http_package)
    monkeypatch.setitem(__import__("sys").modules, "qdrant_client.http.models", models)

    class FakeQdrant:
        def __init__(self):
            self.collections = set()
            self.schemas = {}
            self.points = {}

        def collection_exists(self, name):
            return name in self.collections

        def create_collection(self, collection_name, vectors_config):
            self.collections.add(collection_name)
            self.schemas[collection_name] = {}

        def create_payload_index(self, collection_name, field_name, field_schema, wait):
            self.schemas[collection_name][field_name] = field_schema

        def get_collection(self, collection_name):
            return SimpleNamespace(payload_schema=self.schemas[collection_name])

        def upsert(self, collection_name, points, wait):
            for point in points:
                self.points[point.id] = (collection_name, point.vector, point.payload)

        def query_points(self, collection_name, query, query_filter, limit, with_payload):
            conditions = {condition.key: condition.match.value for condition in query_filter.must}
            matches = [
                (point_id, vector, payload)
                for point_id, (name, vector, payload) in self.points.items()
                if name == collection_name
                and all(payload.get(key) == value for key, value in conditions.items())
            ]
            points = [
                SimpleNamespace(id=point_id, payload=payload, score=1.0)
                for point_id, _, payload in matches
            ]
            return SimpleNamespace(points=points[:limit])

        def get_collections(self):
            return SimpleNamespace(
                collections=[SimpleNamespace(name=name) for name in self.collections]
            )

        def delete(self, collection_name, points_selector, wait):
            conditions = {
                condition.key: condition.match.value for condition in points_selector.filter.must
            }
            self.points = {
                point_id: value
                for point_id, value in self.points.items()
                if value[0] != collection_name
                or not all(value[2].get(key) == wanted for key, wanted in conditions.items())
            }

        def close(self):
            pass

    remote = FakeQdrant()
    index = QdrantVectorIndex("http://unused", client=remote)
    memory = CodingMemory(
        tmp_path / "memory.db",
        Embedder(),
        SearchPolicy(exact_weight=0, sparse_weight=0),
        vector_index=index,
    )
    memory.add("a", "alice", "s1", [SourceMessage("tool", "alpha evidence")])
    memory.add("b", "bob", "s2", [SourceMessage("tool", "secret evidence")])
    hits = memory.search("semantic query", "alice", 3)
    assert len(hits) == 1
    assert "alpha evidence" in hits[0].content
    memory.delete_scope("alice")
    assert len(remote.points) == 1
    memory.close()


def test_hybrid_search_uses_remote_index_candidates(tmp_path) -> None:
    """配置远端向量索引时, 混合检索按远端候选复算相似度并参与融合."""
    index = FakeIndex()
    memory = CodingMemory(
        tmp_path / "memory.db",
        Embedder(),
        SearchPolicy(exact_weight=0, sparse_weight=0, neighbor_window=0),
        vector_index=index,
    )
    memory.add("a", "alice", "s1", [SourceMessage("tool", "alpha evidence")])

    result = memory.search_hybrid("semantic query", "alice", 3)

    assert result.dense_available is True
    assert "dense" in result.channels
    assert result.hits
    memory.close()


def test_qdrant_sdk_local_mode_round_trip(tmp_path) -> None:
    """官方 Qdrant SDK local mode 覆盖真实 query/filter/delete 合同。"""
    qdrant = pytest.importorskip("qdrant_client")
    remote_path = tmp_path / "qdrant-data"
    remote = qdrant.QdrantClient(path=str(remote_path))
    index = QdrantVectorIndex("http://unused", client=remote)
    with pytest.warns(UserWarning, match="Payload indexes have no effect"):
        index.upsert("alice", "fp-v1", ["chunk-a"], [(1.0, 0.0)])
    index.upsert("bob", "fp-v1", ["chunk-b"], [(1.0, 0.0)])
    assert index.search("alice", "fp-v1", (1.0, 0.0), 5) == ["chunk-a"]
    index.close()
    reopened = QdrantVectorIndex("http://unused", client=qdrant.QdrantClient(path=str(remote_path)))
    assert reopened.search("bob", "fp-v1", (1.0, 0.0), 5) == ["chunk-b"]
    reopened.delete_scope("alice")
    assert reopened.search("alice", "fp-v1", (1.0, 0.0), 5) == []
    assert reopened.search("bob", "fp-v1", (1.0, 0.0), 5) == ["chunk-b"]
    reopened.close()
