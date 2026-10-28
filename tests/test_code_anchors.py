"""代码锚点抽取的正常路径、异常路径与边界条件测试."""

from __future__ import annotations

from dmsa.memory.coding.code_anchors import (
    KIND_DEFINES,
    KIND_FRAME,
    KIND_MENTIONS,
    KIND_MODIFIES,
    KIND_TEST,
    anchor_summary,
    anchor_terms,
    extract_anchors,
)


def test_extracts_diff_path_and_definition():
    text = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,3 +1,4 @@\n"
        "+class Handler:\n"
        "+    def handle(self):\n"
        "+        pass\n"
    )
    anchors = extract_anchors(text)
    assert any(a.path == "src/app.py" and a.kind == KIND_MODIFIES for a in anchors)
    assert any(a.symbol == "Handler.handle" and a.kind == KIND_DEFINES for a in anchors)
    assert any(a.symbol == "Handler" and a.kind == KIND_DEFINES for a in anchors)


def test_extracts_test_id_and_traceback_frame():
    text = (
        "FAILED tests/test_app.py::test_handler - AssertionError\n"
        '  File "src/app.py", line 12, in handle_request\n'
    )
    anchors = extract_anchors(text)
    assert any(
        a.kind == KIND_TEST and a.symbol == "tests/test_app.py::test_handler" for a in anchors
    )
    assert any(
        a.kind == KIND_FRAME and a.path == "src/app.py" and a.symbol == "handle_request"
        for a in anchors
    )


def test_extracts_code_fence_path_as_mention():
    anchors = extract_anchors("```python path=src/cache.py\nx = 1\n```")
    assert any(a.path == "src/cache.py" and a.kind == KIND_MENTIONS for a in anchors)


def test_empty_and_non_string_input_return_empty():
    assert extract_anchors("") == ()
    assert extract_anchors("no code here") == ()


def test_nested_class_methods_are_qualified():
    source = "class Outer:\n    class Inner:\n        def run(self):\n            pass\n"
    anchors = extract_anchors(source)
    qualified = {a.symbol for a in anchors if a.kind == KIND_DEFINES}
    assert "Outer.Inner.run" in qualified
    assert "Inner.run" in qualified


def test_anchor_terms_are_lowercase_and_deduplicated():
    anchors = extract_anchors("diff --git a/src/App.py b/src/App.py\n")
    terms = anchor_terms(anchors)
    assert "src/app.py" in terms
    assert "app.py" in terms
    assert len(terms) == len(set(terms))


def test_anchor_summary_limits_entries():
    text = "\n".join(f"diff --git a/src/f{i}.py b/src/f{i}.py" for i in range(10))
    summary = anchor_summary(extract_anchors(text), limit=3)
    assert summary.count(";") >= 2
    assert "+7" in summary


def test_anchor_summary_of_empty_is_empty_string():
    assert anchor_summary(()) == ""
