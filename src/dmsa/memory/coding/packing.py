"""证据挑选: 数量上限、分数下限、会话与路径多样性."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from dmsa.memory.coding.models import SearchPolicy

_TOKEN_RE = re.compile(r"[\w./-]+")


def token_set(text: str) -> set[str]:
    """产生预算统计及冗余比较用 token."""
    return set(_TOKEN_RE.findall(text.lower()))


def _path_key(paths: str) -> str:
    """取首个路径作为多样性维度."""
    for path in paths.split():
        return path
    return ""


def select_evidence(
    ranked: Sequence[tuple[str, float, Mapping[str, object]]],
    top_k: int,
    policy: SearchPolicy,
) -> list[tuple[str, float, Mapping[str, object]]]:
    """按排名挑选证据: 数量上限、分数下限、会话限额、路径限额与近重过滤."""
    limit = min(top_k, policy.evidence_limit)
    selected: list[tuple[str, float, Mapping[str, object]]] = []
    sessions: dict[str, int] = {}
    paths: dict[str, int] = {}
    token_sets: list[set[str]] = []
    best = ranked[0][1] if ranked else 0.0
    for chunk_id, score, row in ranked:
        if len(selected) >= limit:
            break
        if (
            best > 0
            and len(selected) >= policy.min_evidence
            and score < best * policy.score_floor_ratio
        ):
            break
        body = str(row["text"])
        session = str(row["session_id"])
        if sessions.get(session, 0) >= policy.per_session_limit:
            continue
        path_key = _path_key(str(row["paths"]))
        if path_key and paths.get(path_key, 0) >= policy.per_path_limit:
            continue
        terms = token_set(body)
        if token_sets and any(
            len(terms & existing) / max(1, len(terms | existing)) >= policy.redundancy_threshold
            for existing in token_sets
        ):
            continue
        selected.append((chunk_id, score, row))
        token_sets.append(terms)
        sessions[session] = sessions.get(session, 0) + 1
        if path_key:
            paths[path_key] = paths.get(path_key, 0) + 1
    return selected
