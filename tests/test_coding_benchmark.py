"""Coding benchmark 合同与指标测试。"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.coding.adapters import (  # noqa: E402
    AgentResult,
    run_command_agent,
    run_openai_compatible_agent,
)
from benchmarks.coding.cli import _markdown  # noqa: E402
from benchmarks.coding.dataset import load_manifest, ndcg  # noqa: E402
from benchmarks.coding.evaluator import evaluate_fixture  # noqa: E402


def test_ndcg_and_validation() -> None:
    assert ndcg(["good", "bad"], {"good"}) == pytest.approx(1.0)
    assert ndcg([], {"good"}) == 0.0


def test_fixture_evaluation_emits_proxy_only_and_four_noise_levels(tmp_path: Path) -> None:
    manifest = load_manifest(ROOT / "benchmarks/coding/fixtures/mini.json")
    checkpoint = tmp_path / "checkpoint.json"
    report = evaluate_fixture(
        manifest, arms=["no_memory", "sparse"], seed=7, checkpoint_path=checkpoint
    )
    resumed = evaluate_fixture(
        manifest, arms=["no_memory", "sparse"], seed=7, checkpoint_path=checkpoint
    )

    assert report["official_score"] is None
    assert set(report["noise_levels"]) == {"low", "medium_low", "medium_high", "high"}
    assert report["run_metadata"]["seed"] == 7
    assert report["metrics"]["agent_reported_resolved_rate"] is None
    assert report["metrics"]["evidence_recall"] is not None
    markdown = _markdown(report)
    assert "| no_memory | low |" in markdown
    assert "| sparse | high |" in markdown
    assert report == resumed
    json.dumps(report)


def test_manifest_rejects_duplicate_task_ids_and_bad_levels(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tasks": [
                    {
                        "id": "x",
                        "kind": "bug",
                        "query": "q",
                        "target_path": "a.py",
                        "evidence": "e",
                        "distractors": [
                            {"kind": "irrelevant", "text": f"noise {index}"} for index in range(10)
                        ],
                    },
                    {
                        "id": "x",
                        "kind": "bug",
                        "query": "q",
                        "target_path": "a.py",
                        "evidence": "e",
                        "distractors": [
                            {"kind": "irrelevant", "text": f"noise {index}"} for index in range(10)
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unique"):
        load_manifest(path)


def test_checkpoint_rejects_changed_seed(tmp_path: Path) -> None:
    manifest = load_manifest(ROOT / "benchmarks/coding/fixtures/mini.json")
    checkpoint = tmp_path / "checkpoint.json"
    evaluate_fixture(manifest, arms=["sparse"], seed=1, checkpoint_path=checkpoint)
    with pytest.raises(ValueError, match="configuration"):
        evaluate_fixture(manifest, arms=["sparse"], seed=2, checkpoint_path=checkpoint)


def test_agent_results_resume_without_repeating_calls_and_hide_gold(tmp_path: Path) -> None:
    manifest = load_manifest(ROOT / "benchmarks/coding/fixtures/mini.json")
    checkpoint = tmp_path / "agent-checkpoint.json"
    calls = []

    def fake_agent(payload: dict) -> AgentResult:
        assert "target_path" not in payload["task"]
        calls.append(payload["task"]["id"])
        return AgentResult(True, False, "unverified.py", 0, 0.01, 12, None)

    report = evaluate_fixture(
        manifest,
        arms=["no_memory"],
        seed=9,
        agent=fake_agent,
        agent_name="test-agent",
        checkpoint_path=checkpoint,
    )
    assert len(calls) == 8
    assert report["metrics"]["agent_reported_resolved_rate"] == 1.0
    assert report["metrics"]["agent_reported_test_pass_rate"] == 0.0
    evaluate_fixture(
        manifest,
        arms=["no_memory"],
        seed=9,
        agent=fake_agent,
        agent_name="test-agent",
        checkpoint_path=checkpoint,
    )
    assert len(calls) == 8


def test_command_agent_uses_argv_and_validates_json_contract(tmp_path: Path) -> None:
    executable = Path(sys.executable)
    script = (
        "import json,sys; x=json.load(sys.stdin); "
        "print(json.dumps({'resolved': True, 'test_passed': True, "
        "'localized_path': 'src/a.py'}))"
    )
    result = run_command_agent(
        [str(executable), "-c", script],
        {"task": {"target_path": "src/a.py"}, "evidence": []},
        timeout=5,
    )
    assert result.resolved is True
    assert result.test_passed is True
    assert result.localized_path == "src/a.py"
    assert result.return_code == 0
    assert result.latency_seconds > 0


def test_command_agent_rejects_malformed_or_overbudget_result() -> None:
    with pytest.raises(ValueError, match="contract"):
        run_command_agent([sys.executable, "-c", "print('not json')"], {}, timeout=5)


def test_openai_compatible_adapter_uses_environment_credential(monkeypatch) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            assert self.headers["Authorization"] == "Bearer test-secret"
            response = json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "resolved": True,
                                        "test_passed": True,
                                        "localized_path": "src/a.py",
                                    }
                                )
                            }
                        }
                    ],
                    "usage": {"total_tokens": 12},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("TEST_BENCH_KEY", "test-secret")
    try:
        result = run_openai_compatible_agent(
            {"task": {"target_path": "src/a.py"}},
            model="mock-model",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key_env="TEST_BENCH_KEY",
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert result.resolved
    assert result.test_passed
    assert result.token_count == 12


def test_openai_adapter_fails_closed_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("MISSING_BENCH_KEY", raising=False)
    with pytest.raises(ValueError, match="credential"):
        run_openai_compatible_agent(
            {},
            model="model",
            base_url="https://example.invalid/v1",
            api_key_env="MISSING_BENCH_KEY",
        )
