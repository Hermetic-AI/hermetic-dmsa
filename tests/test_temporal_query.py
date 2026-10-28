"""时序 as-of 查询的行为测试."""

from __future__ import annotations

from pathlib import Path

import pytest

from dmsa.mcp_server.coding_memory import CodingMemoryMCPServer
from dmsa.mcp_server.protocol import parse_request
from dmsa.memory import AsOf, CodingMemory, SearchPolicy, SourceMessage
from dmsa.memory.coding.models import CodingMemoryValidationError

# 第一条是较早写入的证据, 后一条是同主题但更晚写入的证据.
OLD_EVIDENCE = "缓存失效导致重复加载, 修复方式是提交后递增版本号。"
NEW_EVIDENCE = "缓存失效导致连接重建, 根因是租约没有续期。"
NOISE = "顺手把日志格式统一成一行输出, 不影响逻辑。"

# 刻意写长的旧证据: 候选窗口有限时, 短的新证据会把它挤出窗口.
LONG_OLD = (
    "缓存失效导致重复加载。这条历史记录刻意写得很长, "
    "用来演示较旧的证据会在候选窗口里被更新的证据挤掉, "
    "因此时序过滤必须放在数据库查询层完成, 而不是召回之后再做筛选。"
)
SHORT_NEW = [
    "缓存失效重试策略缺少退避",
    "缓存失效告警阈值设置过低",
    "缓存失效日志缺少请求标识",
]

# 补丁与关联记录: 两者共享同一个文件, 因此建立工程关系.
PATCH = (
    "diff --git a/src/app.py b/src/app.py\n"
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -1,2 +1,5 @@\n"
    "+class Handler:\n"
    "+    def handle_request(self, payload):\n"
    "+        return payload\n"
)
RELATED = (
    "回滚记录: 上一次改动同一个文件时忘记清理中间件。\n"
    "```python path=src/app.py\n"
    "ROLLBACK_NOTE = 'missing cleanup'\n"
    "```\n"
)


def _memory(tmp_path: Path, policy: SearchPolicy | None = None) -> CodingMemory:
    """构造含三条证据的 coding memory."""
    memory = CodingMemory(tmp_path / "temporal.sqlite", policy=policy)
    memory.add("r1", "u", "s1", [SourceMessage("assistant", OLD_EVIDENCE)])
    memory.add("r2", "u", "s2", [SourceMessage("assistant", NEW_EVIDENCE)])
    memory.add("r3", "u", "s3", [SourceMessage("assistant", NOISE)])
    return memory


class _Embedder:
    """测试用确定性稠密编码器."""

    fingerprint = "unit-t1"
    dimension = 2

    def embed_batch(self, texts):
        """返回与查询同向的固定向量."""
        return [[1.0, 0.0] for _ in texts]


def _ids(memory: CodingMemory, scope: str = "u") -> list[str]:
    """按写入顺序返回作用域内的切片 ID."""
    rows = memory.store.db.execute(
        "SELECT c.id FROM chunks c JOIN requests r "
        "ON r.user_id=c.user_id AND r.request_id=c.request_id "
        "WHERE c.user_id=? ORDER BY r.revision, c.id",
        (scope,),
    ).fetchall()
    return [str(row["id"]) for row in rows]


def test_as_of_requires_at_least_one_coordinate() -> None:
    """两种坐标都为空时拒绝构造."""
    with pytest.raises(CodingMemoryValidationError):
        AsOf()


@pytest.mark.parametrize("value", [-1, True, 1.5, "1"])
def test_as_of_rejects_invalid_revision(value: object) -> None:
    """非整数或负值的写入序号被拒绝."""
    with pytest.raises(CodingMemoryValidationError):
        AsOf(revision=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [-1, True, 1.5, "1"])
def test_as_of_rejects_invalid_timestamp(value: object) -> None:
    """非整数或负值的写入时刻被拒绝."""
    with pytest.raises(CodingMemoryValidationError):
        AsOf(timestamp=value)  # type: ignore[arg-type]


def test_as_of_revision_hides_later_writes(tmp_path: Path) -> None:
    """按写入序号回看时, 更晚写入的证据不可见."""
    memory = _memory(tmp_path)
    old_id, new_id, _ = _ids(memory)

    result = memory.search_as_of("缓存失效", "u", 5, AsOf(revision=1))

    assert [hit.id for hit in result.hits] == [old_id]
    assert new_id not in [hit.id for hit in result.hits]
    memory.close()


def test_as_of_revision_includes_boundary_write(tmp_path: Path) -> None:
    """写入序号上界含该次写入本身."""
    memory = _memory(tmp_path)
    old_id, new_id, _ = _ids(memory)

    result = memory.search_as_of("缓存失效", "u", 5, AsOf(revision=2))

    assert old_id in [hit.id for hit in result.hits]
    assert new_id in [hit.id for hit in result.hits]
    memory.close()


def test_as_of_revision_zero_returns_nothing(tmp_path: Path) -> None:
    """写入序号从 1 起, 0 号坐标下没有任何证据."""
    memory = _memory(tmp_path)

    result = memory.search_as_of("缓存失效", "u", 5, AsOf(revision=0))

    assert result.hits == ()
    memory.close()


def test_as_of_timestamp_hides_later_writes(tmp_path: Path) -> None:
    """按写入时刻回看时, 时刻更晚的证据不可见."""
    memory = CodingMemory(tmp_path / "temporal.sqlite")
    memory.add("r1", "u", "s1", [SourceMessage("assistant", OLD_EVIDENCE, timestamp=1000)])
    memory.add("r2", "u", "s2", [SourceMessage("assistant", NEW_EVIDENCE, timestamp=2000)])
    old_id, new_id = _ids(memory)[:2]

    result = memory.search_as_of("缓存失效", "u", 5, AsOf(timestamp=1000))

    assert [hit.id for hit in result.hits] == [old_id]
    assert new_id not in [hit.id for hit in result.hits]
    memory.close()


def test_as_of_both_coordinates_are_conjunctive(tmp_path: Path) -> None:
    """两种坐标同时给出时取交集."""
    memory = CodingMemory(tmp_path / "temporal.sqlite")
    memory.add("r1", "u", "s1", [SourceMessage("assistant", OLD_EVIDENCE, timestamp=1000)])
    memory.add("r2", "u", "s2", [SourceMessage("assistant", NEW_EVIDENCE, timestamp=2000)])
    old_id, new_id = _ids(memory)[:2]

    result = memory.search_as_of("缓存失效", "u", 5, AsOf(revision=2, timestamp=1000))

    assert [hit.id for hit in result.hits] == [old_id]
    assert new_id not in [hit.id for hit in result.hits]
    memory.close()


def test_as_of_filters_in_query_layer_not_after_recall(tmp_path: Path) -> None:
    """过滤发生在候选查询层, 较旧的证据不会被较新的证据挤出候选窗口."""
    memory = CodingMemory(tmp_path / "temporal.sqlite", policy=SearchPolicy(candidate_limit=1))
    memory.add("r1", "u", "s1", [SourceMessage("assistant", LONG_OLD)])
    for index, text in enumerate(SHORT_NEW):
        memory.add(f"n{index}", "u", f"ns{index}", [SourceMessage("assistant", text)])
    old_id = _ids(memory)[0]

    plain = memory.search("缓存失效", "u", 5)
    as_of = memory.search_as_of("缓存失效", "u", 5, AsOf(revision=1))

    assert plain
    assert old_id not in [hit.id for hit in plain]
    assert [hit.id for hit in as_of.hits] == [old_id]
    memory.close()


def test_as_of_result_reports_scope_revision(tmp_path: Path) -> None:
    """时序检索结果同时给出作用域当前的写入序号."""
    memory = _memory(tmp_path)

    result = memory.search_as_of("缓存失效", "u", 5, AsOf(revision=1))

    assert result.scope_revision == 3
    assert result.as_of.to_dict() == {"revision": 1, "timestamp": None}
    memory.close()


def test_scope_revision_tracks_writes(tmp_path: Path) -> None:
    """作用域写入序号随成功写入递增."""
    memory = _memory(tmp_path)

    assert memory.temporal.scope_revision("u") == 3
    memory.close()


def test_as_of_filters_dense_channel(tmp_path: Path) -> None:
    """稠密通道同样按时序坐标收敛, 更晚写入的向量不参与召回."""
    memory = CodingMemory(tmp_path / "temporal.sqlite", embedder=_Embedder())
    memory.add("r1", "u", "s1", [SourceMessage("assistant", OLD_EVIDENCE)])
    memory.add("r2", "u", "s2", [SourceMessage("assistant", NEW_EVIDENCE)])
    old_id, new_id = _ids(memory)[:2]

    result = memory.search_as_of("缓存失效", "u", 5, AsOf(revision=1))

    assert [hit.id for hit in result.hits] == [old_id]
    assert new_id not in [hit.id for hit in result.hits]
    memory.close()


def test_as_of_excludes_newer_relation_neighbors(tmp_path: Path) -> None:
    """关系扩展同样受时序坐标约束, 更晚写入的关联证据不会被扩展进来."""
    memory = CodingMemory(tmp_path / "temporal.sqlite")
    memory.add("r1", "u", "s1", [SourceMessage("assistant", PATCH)])
    memory.add("r2", "u", "s2", [SourceMessage("assistant", RELATED)])
    policy = SearchPolicy(dense_weight=0.0, relation_window=2)

    plain = memory.search("handle_request", "u", 5, policy)
    as_of = memory.search_as_of("handle_request", "u", 5, AsOf(revision=1), policy=policy)

    assert any("ROLLBACK_NOTE" in hit.content for hit in plain)
    assert all("ROLLBACK_NOTE" not in hit.content for hit in as_of.hits)
    memory.close()


def test_as_of_respects_repo_scope(tmp_path: Path) -> None:
    """仓库子域隔离在时序检索里同样成立."""
    memory = CodingMemory(tmp_path / "temporal.sqlite")
    memory.add("r1", "u", "s1", [SourceMessage("assistant", OLD_EVIDENCE)], repo="alpha")
    memory.add("r2", "u", "s2", [SourceMessage("assistant", NEW_EVIDENCE)], repo="beta")

    result = memory.search_as_of("缓存失效", "u", 5, AsOf(revision=2), repo="alpha")

    assert len(result.hits) == 1
    assert "连接重建" not in result.hits[0].content
    memory.close()


@pytest.mark.parametrize("top_k", [0, -1, 51, True])
def test_as_of_rejects_invalid_top_k(tmp_path: Path, top_k: object) -> None:
    """越界或非整数的 top_k 被拒绝."""
    memory = _memory(tmp_path)

    with pytest.raises(CodingMemoryValidationError):
        memory.search_as_of("缓存失效", "u", top_k, AsOf(revision=1))  # type: ignore[arg-type]
    memory.close()


def test_as_of_requires_as_of_object(tmp_path: Path) -> None:
    """缺少时序坐标时明确报错."""
    memory = _memory(tmp_path)

    with pytest.raises(CodingMemoryValidationError):
        memory.search_as_of("缓存失效", "u", 5, None)  # type: ignore[arg-type]
    memory.close()


def test_search_without_as_of_is_unchanged(tmp_path: Path) -> None:
    """普通检索不受时序能力影响, 仍返回全部可见证据."""
    memory = _memory(tmp_path)

    plain = memory.search("缓存失效", "u", 5)

    assert len(plain) == 2
    memory.close()


def test_coding_memory_mcp_search_as_of_reports_revision(tmp_path: Path) -> None:
    """MCP 时序检索工具返回命中与作用域当前写入序号."""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "u", path)
    memory.add("r1", "u", "s1", [SourceMessage("assistant", OLD_EVIDENCE)])
    memory.add("r2", "u", "s2", [SourceMessage("assistant", NEW_EVIDENCE)])
    request = parse_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "dmsa_coding_memory_search_as_of",
                "arguments": {"query": "缓存失效", "as_of_revision": 1},
            },
        }
    )

    result = server._call(request)["result"]
    text = result["content"][0]["text"]

    assert '"scope_revision": 2' in text
    assert '"revision": 1' in text
    assert "连接重建" not in text
    memory.close()


def test_coding_memory_mcp_search_as_of_requires_coordinate(tmp_path: Path) -> None:
    """两种坐标都没给出时以工具错误返回."""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "u", path)
    request = parse_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "dmsa_coding_memory_search_as_of",
                "arguments": {"query": "缓存失效"},
            },
        }
    )

    result = server._call(request)["result"]

    assert result["isError"] is True
    memory.close()


def test_coding_memory_mcp_search_as_of_rejects_invalid_revision(tmp_path: Path) -> None:
    """非法的写入序号以工具错误返回."""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "u", path)
    request = parse_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "dmsa_coding_memory_search_as_of",
                "arguments": {"query": "缓存失效", "as_of_revision": -3},
            },
        }
    )

    result = server._call(request)["result"]

    assert result["isError"] is True
    memory.close()
