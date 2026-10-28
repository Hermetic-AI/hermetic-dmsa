"""stdio MCP 的记忆合并与跨仓库迁移工具实现."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dmsa.memory import CodingMemory


@dataclass(frozen=True)
class ConsolidateArgs:
    """已校验的合并参数."""

    repo: str
    min_group: int
    limit: int


@dataclass(frozen=True)
class TransferArgs:
    """已校验的迁移参数."""

    source_repo: str
    target_repo: str
    limit: int


def parse_consolidate(arguments: dict[str, Any]) -> ConsolidateArgs:
    """校验合并参数; 最小重复次数至少为 2, 否则没有可合并的规则."""
    min_group = arguments.get("min_group", 2)
    if type(min_group) is not int or not 2 <= min_group <= 20:
        raise ValueError("min_group must be an integer between 2 and 20")
    limit = arguments.get("limit", 500)
    if type(limit) is not int or not 1 <= limit <= 2000:
        raise ValueError("limit must be an integer between 1 and 2000")
    repo = arguments.get("repo", "")
    if not isinstance(repo, str):
        raise ValueError("repo must be text")
    return ConsolidateArgs(repo=repo, min_group=min_group, limit=limit)


def parse_transfer(arguments: dict[str, Any]) -> TransferArgs:
    """校验迁移参数; 目标仓库必须显式给出, 且不能与源仓库相同."""
    source_repo = arguments.get("source_repo", "")
    target_repo = arguments.get("target_repo", "")
    if not isinstance(source_repo, str) or not isinstance(target_repo, str):
        raise ValueError("source_repo and target_repo must be text")
    if not target_repo:
        raise ValueError("target_repo is required")
    if source_repo == target_repo:
        raise ValueError("source_repo and target_repo must differ")
    limit = arguments.get("limit", 500)
    if type(limit) is not int or not 1 <= limit <= 2000:
        raise ValueError("limit must be an integer between 1 and 2000")
    return TransferArgs(source_repo=source_repo, target_repo=target_repo, limit=limit)


def consolidate_payload(memory: CodingMemory, scope: str, args: ConsolidateArgs) -> dict[str, Any]:
    """合并同一作用域内重复出现的经验, 返回合并报告."""
    report = memory.consolidate(scope, args.repo, min_group=args.min_group, limit=args.limit)
    return {"scope": scope, "repo": args.repo, **report.to_dict()}


def transfer_payload(memory: CodingMemory, scope: str, args: TransferArgs) -> dict[str, Any]:
    """把可复用的通用规则迁移到另一个仓库, 返回迁移报告."""
    report = memory.transfer(scope, args.source_repo, args.target_repo, limit=args.limit)
    return {
        "scope": scope,
        "source_repo": args.source_repo,
        "target_repo": args.target_repo,
        **report.to_dict(),
    }
