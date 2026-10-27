"""同族异标识符的历史素材: 用于不泄漏答案的迁移评测."""

from __future__ import annotations

from dataclasses import dataclass

from benchmarks.coding.selftest.cases import Case, build_cases


@dataclass(frozen=True)
class Analogy:
    """与目标任务同族但标识符不同的已解决历史."""

    source_case_id: str
    sessions: tuple[tuple[tuple[str, str], ...], ...]
    noise: tuple[str, ...]
    gold_text: str
    substitutions: tuple[tuple[str, str], ...]


def family_of(case_id: str) -> str:
    """从任务 ID 取出任务家族名."""
    return case_id.rsplit("-", 1)[0]


def _substitutions(source: Case, target: Case) -> tuple[tuple[str, str], ...]:
    """构造源标识符到目标标识符的替换规则, 长串优先避免误替换."""
    pairs: set[tuple[str, str]] = set()
    for source_name, target_name in (
        (source.module, target.module),
        (source.symbol, target.symbol),
    ):
        if source_name and target_name and source_name != target_name:
            pairs.add((source_name, target_name))
    return tuple(sorted(pairs, key=lambda pair: (-len(pair[0]), pair[0])))


def build_analogies(seed: int, cases: tuple[Case, ...]) -> dict[str, Analogy]:
    """为每个任务准备同族但不同 seed 的历史; 历史里不含目标任务的标准答案."""
    siblings = {case.case_id: case for case in build_cases(seed + 1)}
    analogies: dict[str, Analogy] = {}
    for case in cases:
        sibling = siblings.get(f"{family_of(case.case_id)}-{seed + 1}")
        if sibling is None:
            continue
        analogies[case.case_id] = Analogy(
            source_case_id=sibling.case_id,
            sessions=sibling.sessions,
            noise=sibling.noise,
            gold_text=sibling.gold_text,
            substitutions=_substitutions(sibling, case),
        )
    return analogies
