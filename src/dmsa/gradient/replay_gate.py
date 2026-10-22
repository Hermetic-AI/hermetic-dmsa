"""离线策略回放的固定升级门禁."""

import math

from dmsa.gradient.replay_models import GATE_VERSION, CaseResult, ReplayReport


def _valid_result(item: CaseResult, report: ReplayReport) -> bool:
    """拒绝格式错误及超预算的单例结果."""
    if not isinstance(item, CaseResult):
        return False
    if (not isinstance(item.retrieved_ids, tuple) or not isinstance(item.relevant_ids, tuple)
            or not item.relevant_ids
            or any(not isinstance(x, str) or not x for x in (
                item.case_id, item.slice, *item.retrieved_ids, *item.relevant_ids))):
        return False
    return (
        len(item.retrieved_ids) <= report.top_k
        and len(set(item.retrieved_ids)) == len(item.retrieved_ids)
        and len(set(item.relevant_ids)) == len(item.relevant_ids)
        and type(item.used_chars) is int and 0 <= item.used_chars <= report.max_chars
        and type(item.used_tokens) is int and 0 <= item.used_tokens <= report.max_tokens
    )


def gate(report: ReplayReport) -> bool:
    """拒绝无效或回归证据, 并要求至少一例召回改善。."""
    if not isinstance(report, ReplayReport):
        return False
    if (
        report.gate_version != GATE_VERSION
        or not isinstance(report.before, tuple)
        or not isinstance(report.after, tuple)
        or not report.before
        or len(report.before) != len(report.after)
    ):
        return False
    if (
        not report.corpus_hash
        or not report.cases_hash
        or not report.baseline_hash
        or not report.candidate_hash
        or any(type(x) is not int or x < 1
               for x in (report.top_k, report.max_chars, report.max_tokens))
    ):
        return False
    if any(not _valid_result(item, report) for item in (*report.before, *report.after)):
        return False
    before = {x.case_id: x for x in report.before}
    after = {x.case_id: x for x in report.after}
    if before.keys() != after.keys():
        return False
    if len(before) != len(report.before) or any(not item.relevant_ids for item in report.before):
        return False
    improvement = False
    for key, old in before.items():
        new = after[key]
        if old.slice != new.slice or old.relevant_ids != new.relevant_ids:
            return False
        if not all(math.isfinite(x.recall) for x in (old, new)) or new.recall < old.recall:
            return False
        improvement |= new.recall > old.recall
    return improvement

