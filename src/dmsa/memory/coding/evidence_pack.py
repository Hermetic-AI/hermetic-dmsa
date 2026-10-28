"""证据装包: 邻居与关系扩展、限额冗余、预算裁剪与结构化定位信息."""

from __future__ import annotations

from collections.abc import Mapping

from dmsa.memory.coding.budget import estimate_tokens
from dmsa.memory.coding.code_anchors import anchor_summary
from dmsa.memory.coding.episodes import SUPERSEDED_OUTCOME
from dmsa.memory.coding.models import SearchHit, SearchPolicy
from dmsa.memory.coding.packing import select_evidence
from dmsa.memory.coding.ranking import is_stale_write
from dmsa.memory.coding.skeleton import extract_skeleton
from dmsa.memory.coding.snapshot import RepoSnapshot, stale_anchors
from dmsa.memory.coding.storage import CodingStore
from dmsa.memory.coding.temporal import AsOf

# 截断后的正文短于该值时直接放弃, 避免只有来源头没有内容的噪声条目。
MIN_TRUNCATED_CHARS = 160
# 邻居与关系扩展的分数折扣, 保证扩展证据排在原始命中之后。
EXPANSION_DISCOUNT = 0.5
RELATION_DISCOUNT = 0.6
# 扩展分数与父命中之间的最小间隔, 用于在父分为负时仍保持严格递减。
EXPANSION_EPSILON = 1e-9


def fit_text(source: str, body: str, char_budget: int, token_budget: int) -> str | None:
    """把正文裁到剩余预算内; 预算不足时保留可辨识前缀并显式标记截断."""
    marker = "\n[truncated]"
    room = min(len(body), char_budget - len(source) - len(marker))
    if room < MIN_TRUNCATED_CHARS:
        return None
    if estimate_tokens(source + body[:room] + marker) <= token_budget:
        return source + body[:room] + marker
    low, high = 0, room
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(source + body[:middle] + marker) <= token_budget:
            low = middle
        else:
            high = middle - 1
    if low < MIN_TRUNCATED_CHARS:
        return None
    return source + body[:low] + marker


def _discount(base: float, factor: float) -> float:
    """给扩展证据打分, 保证结果严格低于它的父命中.

    父命中是调分后的最终分, 可能是负值; 直接乘折扣会把负分放大成更接近零的数,
    从而把扩展证据顶到父命中之前, 所以取两个候选里更小的那个。
    """
    return min(base * factor, base - EXPANSION_EPSILON)


def _expand(
    store: CodingStore,
    user_id: str,
    ordered: list[str],
    ranks: dict[str, float],
    policy: SearchPolicy,
    as_of: AsOf | None = None,
) -> list[str]:
    """按邻居窗口与工程关系扩展候选切片.

    邻居与原切片同属一条消息, 因此写入序号与写入时刻一致, 无需再过滤;
    关系邻居可能来自更晚的写入, 需要按传入的时序坐标收敛.
    """
    expanded = list(ordered)
    seen = set(expanded)
    for cid in ordered:
        for neighbor in store.neighbors(user_id, cid, policy.neighbor_window):
            neighbor_id = str(neighbor["id"])
            if neighbor_id not in seen:
                seen.add(neighbor_id)
                ranks[neighbor_id] = _discount(ranks[cid], EXPANSION_DISCOUNT)
                expanded.append(neighbor_id)
    if policy.relation_window:
        for cid in list(expanded)[: policy.relation_window]:
            for related in store.relation_neighbors(user_id, cid, policy.relation_window, as_of):
                if related not in seen:
                    seen.add(related)
                    ranks[related] = _discount(ranks[cid], RELATION_DISCOUNT)
                    expanded.append(related)
    return expanded


def _header(item: Mapping[str, object]) -> str:
    """构造来源引用头."""
    return (
        f"[request={item['request_id']} session={item['session_id']} "
        f"message={item['message_id']} chars={item['start']}:{item['end']} "
        f"role={item['role']} outcome={item['outcome']}]\n"
    )


def build_hits(
    store: CodingStore,
    user_id: str,
    ordered: list[str],
    ranks: dict[str, float],
    top_k: int,
    policy: SearchPolicy,
    snapshot: RepoSnapshot | None = None,
    as_of: AsOf | None = None,
) -> list[SearchHit]:
    """扩展后按限额、冗余与预算装包, 并附带结构化定位与骨架信息."""
    expanded = _expand(store, user_id, ordered, ranks, policy, as_of)
    rows = store.rows_by_ids(user_id, tuple(expanded))
    by_id = {str(row["id"]): row for row in rows}
    expanded.sort(key=lambda cid: (-ranks[cid], cid))
    ranked: list[tuple[str, float, Mapping[str, object]]] = []
    for cid in expanded:
        row = by_id.get(cid)
        if row is None:
            continue
        body = str(row["text"])
        # 历史整文件写入是旧快照, 直接注入会让当前仓库回退到过期版本。
        if policy.experience_weight and is_stale_write(body):
            continue
        if str(row["outcome"]) == SUPERSEDED_OUTCOME:
            continue
        ranked.append((cid, ranks[cid], dict(row)))
    selected = select_evidence(ranked, top_k, policy)
    anchors = store.anchors_by_ids(user_id, tuple(item[0] for item in selected))
    hits: list[SearchHit] = []
    char_budget, token_budget = policy.max_chars, policy.max_tokens
    for cid, score, item in selected:
        source = _header(item)
        chunk_anchors = anchors.get(cid, ())
        summary = anchor_summary(chunk_anchors)
        if summary:
            stale = stale_anchors(snapshot, chunk_anchors) if snapshot else ()
            marker = "[stale] " if stale else ""
            source += f"[anchors] {marker}{summary}\n"
        body = str(item["text"])
        if policy.skeleton_depth:
            skeleton = extract_skeleton(body, policy.skeleton_depth)
            if skeleton:
                source += f"[skeleton]\n{skeleton}\n"
        content = source + body
        size = estimate_tokens(content)
        if len(content) > char_budget or size > token_budget:
            truncated = fit_text(source, body, char_budget, token_budget)
            if truncated is None:
                continue
            content, size = truncated, estimate_tokens(truncated)
        hits.append(SearchHit(cid, content, score))
        char_budget -= len(content)
        token_budget -= size
        if char_budget <= 0 or token_budget <= 0:
            break
    return hits
