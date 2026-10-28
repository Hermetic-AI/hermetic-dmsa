"""检索主流程: 库内召回 → 外部向量化 → 稠密通道 → 调分装包.

普通检索与时序检索共用这一条主流程, 差别只在候选召回与证据扩展时带上时序
坐标, 因此两条路径的排序口径必然一致.

外部调用 (查询向量化、远端向量检索) 始终在数据库锁之外执行, 避免慢上游阻塞
同进程的其他写入与检索.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from dmsa.memory.coding.models import CodingMemoryError, SearchHit, SearchPolicy
from dmsa.memory.coding.retrieval import CodingRetriever
from dmsa.memory.coding.storage import CodingStore
from dmsa.memory.coding.temporal import AsOf

# 时序检索时远端向量索引无法按时间过滤, 因此多取一些候选再按坐标收敛.
AS_OF_DENSE_WIDENING = 4


@contextmanager
def operation(store: CodingStore) -> Iterator[None]:
    """同一连接的读取与事务互斥, 防止观察到未提交的部分请求."""
    with store.lock:
        try:
            yield
        except (sqlite3.Error, OverflowError) as exc:
            raise CodingMemoryError("coding memory database operation failed") from exc


def run_search(
    store: CodingStore,
    retriever: CodingRetriever,
    policy: SearchPolicy,
    query: str,
    scope: str,
    top_k: int,
    as_of: AsOf | None = None,
) -> list[SearchHit]:
    """执行一次检索并返回装好包的证据."""
    with operation(store):
        ranks, features = retriever.collect_candidates(query, scope, policy, as_of)
    vector = retriever.query_vector(query)
    dense_rows: list[sqlite3.Row] = []
    if vector is not None and policy.dense_weight:
        if retriever.vector_index is not None:
            limit = policy.candidate_limit * (AS_OF_DENSE_WIDENING if as_of is not None else 1)
            ids = retriever.dense_index_ids(scope, vector, policy, limit)
            with operation(store):
                rows = store.rows_by_ids(scope, ids, as_of)
            by_id = {str(row["id"]): row for row in rows}
            dense_rows = [by_id[point_id] for point_id in ids if point_id in by_id]
        else:
            with operation(store):
                dense_rows = retriever.dense_scan_rows(scope, vector, policy, as_of)
    with operation(store):
        return retriever.assemble(query, scope, top_k, policy, ranks, features, dense_rows, as_of)
