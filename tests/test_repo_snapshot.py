"""仓库符号快照与失效标注测试."""

from __future__ import annotations

from pathlib import Path

from dmsa.memory import CodingMemory, SourceMessage
from dmsa.memory.coding.code_anchors import extract_anchors
from dmsa.memory.coding.snapshot import build_snapshot, stale_anchors


def _write_repo(root: Path) -> Path:
    """写出一个最小可解析的 Python 仓库."""
    package = root / "src"
    package.mkdir(parents=True)
    (package / "app.py").write_text(
        "class Handler:\n    def handle_request(self):\n        return 1\n", encoding="utf-8"
    )
    return package / "app.py"


def test_snapshot_collects_symbols_and_hashes(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    snapshot = build_snapshot(tmp_path)
    assert "src/app.py" in snapshot.file_paths
    assert "Handler" in snapshot.symbol_names
    assert "Handler.handle_request" in snapshot.symbol_names
    assert all(len(digest) == 16 for _, digest in snapshot.files)


def test_snapshot_skips_dependency_directories(tmp_path: Path) -> None:
    vendor = tmp_path / "node_modules" / "pkg"
    vendor.mkdir(parents=True)
    (vendor / "dep.py").write_text("def dep():\n    return 1\n", encoding="utf-8")
    snapshot = build_snapshot(tmp_path)
    assert not any("node_modules" in path for path in snapshot.file_paths)


def test_missing_directory_returns_empty_snapshot(tmp_path: Path) -> None:
    snapshot = build_snapshot(tmp_path / "absent")
    assert snapshot.files == ()


def test_stale_detection_marks_removed_file_and_renamed_symbol(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    snapshot = build_snapshot(tmp_path)
    anchors = extract_anchors(
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "+class Handler:\n"
        "+    def legacy_method(self):\n"
    )
    stale = stale_anchors(snapshot, anchors)
    stale_symbols = {item.symbol for item in stale if item.kind == "defines"}
    assert "legacy_method" in stale_symbols
    assert "Handler" not in stale_symbols

    removed = extract_anchors("diff --git a/src/gone.py b/src/gone.py\n")
    assert any(item.path == "src/gone.py" for item in stale_anchors(snapshot, removed))


def test_search_marks_stale_evidence_after_snapshot(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    memory = CodingMemory(tmp_path / "memory.sqlite")
    try:
        memory.add(
            "r1",
            "repo",
            "s1",
            [
                SourceMessage(
                    "assistant",
                    "diff --git a/src/gone.py b/src/gone.py\n+def legacy_method():\n    pass\n",
                )
            ],
        )
        plain = memory.search("legacy_method", "repo", 5)
        assert plain
        assert "[stale]" not in plain[0].content
        memory.save_repo_snapshot("repo", tmp_path)
        marked = memory.search("legacy_method", "repo", 5)
        assert "[stale]" in marked[0].content
    finally:
        memory.close()
