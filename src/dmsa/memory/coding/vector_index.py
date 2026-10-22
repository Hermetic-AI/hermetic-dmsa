# ruff: noqa: D415, RUF002
"""向量索引协议与 Qdrant 实现。"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Sequence
from typing import Any, Protocol, cast

from dmsa.memory.coding.models import CodingMemoryError


class VectorIndex(Protocol):
    """提供带 scope 过滤的持久向量候选检索。"""

    def upsert(
        self, user_id: str, fingerprint: str, ids: Sequence[str], vectors: Sequence[Sequence[float]]
    ) -> None:
        """幂等写入同一 scope 的 ID 与向量。"""

    def search(
        self, user_id: str, fingerprint: str, vector: Sequence[float], limit: int
    ) -> list[str]:
        """返回同 scope 内按相似度降序的来源 ID。"""

    def delete_scope(self, user_id: str) -> None:
        """删除所有 collections 中匹配的 scope 点。"""

    def close(self) -> None:
        """释放外部客户端资源。"""


class QdrantVectorIndex:
    """按 embedding fingerprint 与维度分 collection 的 Qdrant ANN 后端。"""

    def __init__(
        self,
        url: str,
        timeout: int = 5,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        """连接 Qdrant；SDK 在实际启用后才加载。"""
        if type(timeout) is not int or timeout < 1:
            raise CodingMemoryError("Qdrant timeout must be a positive integer")
        if client is None:
            try:
                from qdrant_client import QdrantClient
            except ImportError as exc:
                raise CodingMemoryError("install hermetic-dmsa[vector] for Qdrant support") from exc
            client = QdrantClient(url=url, timeout=timeout, api_key=api_key)
        self.client = client

    @staticmethod
    def _collection(fingerprint: str, dimension: int) -> str:
        """生成不含外部输入的稳定 collection 名称。"""
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
        return f"dmsa_{digest}_{dimension}"

    @staticmethod
    def _point_id(chunk_id: str) -> str:
        """将任意稳定 chunk hash 转为 Qdrant 接受的 UUID。"""
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"dmsa-chunk:{chunk_id}"))

    def _retry(self, operation: Any) -> Any:
        """对幂等的网络操作做有限指数退避并隐藏供应商详情。"""
        error: Exception | None = None
        for attempt in range(3):
            try:
                return operation()
            except Exception as exc:
                error = exc
                if attempt < 2:
                    time.sleep(0.1 * (2**attempt))
        raise CodingMemoryError("Qdrant vector index operation failed") from error

    def _ensure_collection(self, fingerprint: str, dimension: int) -> str:
        """确保存储模型空间存在，维度绑定在 collection 名称内。"""
        from qdrant_client.http.models import Distance, PayloadSchemaType, VectorParams

        name = self._collection(fingerprint, dimension)

        def ensure() -> str:
            exists = self.client.collection_exists(name)
            if not exists:
                self.client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
                )
                payload_schema: dict[str, Any] = {}
            else:
                payload_schema = self.client.get_collection(name).payload_schema
            for field in ("user_id", "fingerprint"):
                if field not in payload_schema:
                    self.client.create_payload_index(
                        collection_name=name,
                        field_name=field,
                        field_schema=PayloadSchemaType.KEYWORD,
                        wait=True,
                    )
            return name

        return cast(str, self._retry(ensure))

    def upsert(
        self, user_id: str, fingerprint: str, ids: Sequence[str], vectors: Sequence[Sequence[float]]
    ) -> None:
        """稳定 UUID + scope payload 让重试写入幂等。"""
        from qdrant_client.http.models import PointStruct

        if not ids:
            return
        dimension = len(vectors[0])
        collection = self._ensure_collection(fingerprint, dimension)
        points = [
            PointStruct(
                id=self._point_id(point_id),
                vector=list(vector),
                payload={"user_id": user_id, "fingerprint": fingerprint, "chunk_id": point_id},
            )
            for point_id, vector in zip(ids, vectors, strict=True)
        ]
        for offset in range(0, len(points), 256):
            batch = points[offset : offset + 256]
            self._retry(
                lambda batch=batch: self.client.upsert(
                    collection_name=collection, points=batch, wait=True
                )
            )

    def search(
        self, user_id: str, fingerprint: str, vector: Sequence[float], limit: int
    ) -> list[str]:
        """原生 Qdrant filter 在 ANN 查询前隔离 scope 和模型。"""
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        collection = self._collection(fingerprint, len(vector))
        if not self._retry(lambda: self.client.collection_exists(collection)):
            return []
        query_filter = Filter(
            must=[
                FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                FieldCondition(key="fingerprint", match=MatchValue(value=fingerprint)),
            ]
        )
        result = self._retry(
            lambda: self.client.query_points(
                collection_name=collection,
                query=list(vector),
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        )
        points = [
            point for point in result.points if point.payload and point.payload.get("chunk_id")
        ]
        points.sort(key=lambda point: (-float(point.score), str(point.payload["chunk_id"])))
        return [str(point.payload["chunk_id"]) for point in points]

    def delete_scope(self, user_id: str) -> None:
        """删除所有 DMSA collections 中指定 scope 的向量。"""
        from qdrant_client.http.models import FieldCondition, Filter, FilterSelector, MatchValue

        collections = self._retry(lambda: self.client.get_collections().collections)
        query_filter = FilterSelector(
            filter=Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))])
        )
        for collection in collections:
            if collection.name.startswith("dmsa_"):
                self._retry(
                    lambda name=collection.name: self.client.delete(
                        collection_name=name, points_selector=query_filter, wait=True
                    )
                )

    def close(self) -> None:
        """关闭 SDK 客户端。"""
        self.client.close()
