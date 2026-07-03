"""聚类后端（HDBSCAN 兼容 + 零依赖 fallback）。

默认实现 :class:`FallbackSingleLinkBackend`：单链接聚类（union-find）
+ 余弦距离阈值 ``min_cluster_size``。当 ``hdbscan`` 包已安装时自动切换
到 :class:`HDBSCANBackend`。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from dmsa.errors import ClusteringFailedError
from dmsa.gradient.embedding import cosine_distance
from dmsa.types import ClusterResult, GradientSample

__all__ = [
    "ClusteringBackend",
    "FallbackSingleLinkBackend",
    "HDBSCANBackend",
    "Outlier",
]


class ClusteringBackend(Protocol):
    """聚类后端接口。"""

    name: str

    def cluster(self, samples: list[GradientSample]) -> list[ClusterResult]:
        """对样本列表做密度聚类，返回 ClusterResult 列表。"""
        ...


# sentinel for outlier cluster
OUTLIER_CLUSTER_ID = -1


@dataclass(frozen=True, slots=True)
class Outlier:
    """outlier 标记（用于表示 -1 簇 ID）。"""

    pass


class FallbackSingleLinkBackend:
    """零依赖单链接聚类。

    算法：union-find 按余弦距离 < threshold 合并；
    小于 ``min_cluster_size`` 的组标记为 outlier。
    """

    name = "fallback-single-link"

    def __init__(
        self,
        *,
        min_cluster_size: int = 2,
        distance_threshold: float = 0.3,
    ) -> None:
        if min_cluster_size < 1:
            raise ValueError(f"min_cluster_size must be >= 1, got {min_cluster_size}")
        if not 0.0 <= distance_threshold <= 1.0:
            raise ValueError(
                f"distance_threshold must be in [0, 1], got {distance_threshold}"
            )
        self.min_cluster_size = min_cluster_size
        self.distance_threshold = distance_threshold

    def cluster(self, samples: list[GradientSample]) -> list[ClusterResult]:
        """聚类样本。返回的 ClusterResult 列表按 cluster_id 排序（outlier 在最后）。"""
        if not samples:
            return []
        # 确保所有样本都有 embedding
        unembedded = [i for i, s in enumerate(samples) if s.embedding is None]
        if unembedded:
            raise ClusteringFailedError(
                f"{len(unembedded)} samples lack embeddings; embed first",
                details={"unembedded_count": len(unembedded)},
            )

        # 1. 预计算距离矩阵
        n = len(samples)
        parent = list(range(n))
        rank = [0] * n

        def find(x: int) -> int:
            root = x
            while parent[root] != root:
                root = parent[root]
            # path compression
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            if rank[ra] < rank[rb]:
                ra, rb = rb, ra
            parent[rb] = ra
            if rank[ra] == rank[rb]:
                rank[ra] += 1

        for i in range(n):
            for j in range(i + 1, n):
                ei = samples[i].embedding
                ej = samples[j].embedding
                assert ei is not None and ej is not None
                if cosine_distance(ei, ej) <= self.distance_threshold:
                    union(i, j)

        # 2. 收集组
        groups: dict[int, list[int]] = {}
        for i in range(n):
            root = find(i)
            groups.setdefault(root, []).append(i)

        # 3. 生成 ClusterResult
        results: list[ClusterResult] = []
        next_cluster_id = 0
        outlier_ids: list[int] = []
        for indices in groups.values():
            if len(indices) < self.min_cluster_size:
                outlier_ids.extend(indices)
            else:
                cid = next_cluster_id
                next_cluster_id += 1
                support = tuple(samples[i] for i in indices)
                # 计算质心
                vectors = [list(samples[i].embedding) for i in indices]
                assert all(v is not None for v in vectors)
                dim = len(vectors[0])
                centroid = [sum(v[d] for v in vectors) / len(vectors) for d in range(dim)]
                # confidence: 1 - avg pairwise distance
                if len(indices) == 1:
                    confidence = 1.0
                else:
                    total_dist = 0.0
                    pair_count = 0
                    for i_idx in range(len(indices)):
                        for j_idx in range(i_idx + 1, len(indices)):
                            v_i = vectors[i_idx]
                            v_j = vectors[j_idx]
                            assert v_i is not None and v_j is not None
                            total_dist += cosine_distance(v_i, v_j)
                            pair_count += 1
                    avg_dist = total_dist / pair_count if pair_count else 0.0
                    confidence = max(0.0, 1.0 - avg_dist)
                results.append(
                    ClusterResult(
                        cluster_id=cid,
                        support_set=support,
                        centroid=tuple(centroid),
                        confidence=confidence,
                        is_outlier=False,
                    )
                )

        # 4. outlier 簇
        if outlier_ids:
            support = tuple(samples[i] for i in outlier_ids)
            results.append(
                ClusterResult(
                    cluster_id=OUTLIER_CLUSTER_ID,
                    support_set=support,
                    centroid=None,
                    confidence=0.0,
                    is_outlier=True,
                )
            )

        # 按 cluster_id 排序（outlier 在最后）
        results.sort(key=lambda r: (r.is_outlier, r.cluster_id))
        return results


class HDBSCANBackend:
    """hdbscan 库的薄包装（仅在安装了 ``hdbscan`` 时可用）。

    使用方法：``pip install hermetic-dmsa[clustering]``。
    """

    name = "hdbscan"

    def __init__(self, *, min_cluster_size: int = 2, min_samples: int = 1) -> None:
        try:
            import hdbscan  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ClusteringFailedError(
                "hdbscan not installed; install with `pip install hermetic-dmsa[clustering]`",
                details={"hint": "pip install hermetic-dmsa[clustering]"},
            ) from exc
        self._hdbscan = hdbscan
        self.min_cluster_size = min_cluster_size
        self.min_samples = min_samples

    def cluster(self, samples: list[GradientSample]) -> list[ClusterResult]:
        """聚类样本。"""
        if not samples:
            return []
        unembedded = [s for s in samples if s.embedding is None]
        if unembedded:
            raise ClusteringFailedError(
                f"{len(unembedded)} samples lack embeddings",
                details={"unembedded_count": len(unembedded)},
            )
        vectors = [list(s.embedding) for s in samples]
        clusterer = self._hdbscan.HDBSCAN(
            min_cluster_size=self.min_cluster_size,
            min_samples=self.min_samples,
            metric="euclidean",
        )
        labels = clusterer.fit_predict(vectors)

        # 按 label 分组
        groups: dict[int, list[int]] = {}
        for i, label in enumerate(labels):
            groups.setdefault(int(label), []).append(i)

        results: list[ClusterResult] = []
        next_cid = 0
        outlier_indices: list[int] = []
        for label, indices in groups.items():
            if label == OUTLIER_CLUSTER_ID or len(indices) < self.min_cluster_size:
                outlier_indices.extend(indices)
            else:
                cid = next_cid
                next_cid += 1
                support = tuple(samples[i] for i in indices)
                dim = len(vectors[0])
                centroid = [sum(vectors[i][d] for i in indices) / len(indices) for d in range(dim)]
                results.append(
                    ClusterResult(
                        cluster_id=cid,
                        support_set=support,
                        centroid=tuple(centroid),
                        confidence=0.5,  # HDBSCAN 不直接给 confidence；用 0.5 占位
                        is_outlier=False,
                    )
                )
        if outlier_indices:
            results.append(
                ClusterResult(
                    cluster_id=OUTLIER_CLUSTER_ID,
                    support_set=tuple(samples[i] for i in outlier_indices),
                    centroid=None,
                    confidence=0.0,
                    is_outlier=True,
                )
            )
        results.sort(key=lambda r: (r.is_outlier, r.cluster_id))
        return results


def get_default_backend(
    *, prefer: str = "fallback", **kwargs: object
) -> ClusteringBackend:
    """根据 ``prefer`` 返回默认聚类后端。

    ``prefer="fallback"``：零依赖单链接。
    ``prefer="hdbscan"``：尝试用 hdbscan（若未安装则降级到 fallback）。
    """
    if prefer == "hdbscan":
        try:
            return HDBSCANBackend(**kwargs)  # type: ignore[arg-type]
        except ClusteringFailedError:
            return FallbackSingleLinkBackend(**kwargs)  # type: ignore[arg-type]
    return FallbackSingleLinkBackend(**kwargs)  # type: ignore[arg-type]


# Re-export to keep tests stable
_ = math
