"""本地自出题台与官方 HTTP 契约的端到端联测."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.coding.selftest.analogy import build_analogies  # noqa: E402
from benchmarks.coding.selftest.cases import build_cases  # noqa: E402
from benchmarks.coding.selftest.runner import run_selftest  # noqa: E402
from benchmarks.coding.selftest.solver import solve  # noqa: E402
from dmsa.mcp_server.http.app import create_app  # noqa: E402
from dmsa.memory import CodingMemory, SearchPolicy  # noqa: E402


def test_http_selftest_solves_from_retrieved_evidence(tmp_path) -> None:
    """走真实 /add /search 合同, 用检索证据打补丁并通过 pytest."""
    case = build_cases(0)[0]
    memory = CodingMemory(tmp_path / "selftest.sqlite", policy=SearchPolicy(dense_weight=0.0))
    client = TestClient(create_app(memory, "key"))
    headers = {"Authorization": "Bearer key"}
    for index, session in enumerate(case.sessions):
        response = client.post(
            "/add",
            headers=headers,
            json={
                "request_id": f"rel-{index}",
                "user_id": "selftest",
                "session_id": f"rel-{index}",
                "messages": [
                    {"role": role, "content": content} for role, content in session
                ],
            },
        )
        assert response.status_code == 200
    for index, text in enumerate(case.noise[:8]):
        response = client.post(
            "/add",
            headers=headers,
            json={
                "request_id": f"noise-{index}",
                "user_id": "selftest",
                "session_id": f"noise-{index}",
                "messages": [{"role": "assistant", "content": text}],
            },
        )
        assert response.status_code == 200
    reply = client.post(
        "/search",
        headers=headers,
        json={"query": case.query, "user_id": "selftest", "top_k": 100},
    )
    assert reply.status_code == 200
    hits = reply.json()["data"]
    assert len(hits) > 4
    assert any(case.gold_text in hit["content"] for hit in hits)
    repo = tmp_path / "repo"
    for path, content in case.files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    result = solve([hit["content"] for hit in hits], repo, case.test_file, 3)
    assert result.resolved
    assert result.localized_path == case.target_path
    memory.close()


def test_analogy_history_never_contains_the_target_answer() -> None:
    """同族迁移素材必须是异标识符的已解任务, 不能泄漏目标答案."""
    cases = build_cases(17)
    analogies = build_analogies(17, cases)
    assert set(analogies) == {case.case_id for case in cases}
    for case in cases:
        analogy = analogies[case.case_id]
        history = "".join(
            content for session in analogy.sessions for _, content in session
        ) + "".join(analogy.noise)
        assert case.gold_text not in history
        assert analogy.gold_text != case.gold_text
        assert analogy.substitutions


def test_no_memory_mode_produces_no_evidence() -> None:
    """无历史对照必须真的检索不到东西, 才能作为解题率下限."""
    report = run_selftest(
        seed=17,
        arms=("tuned",),
        levels=("relevant_only",),
        history_mode="none",
    )
    runs = report["runs"]
    assert runs
    assert all(run["hits"] == 0 for run in runs)
    assert all(not run["resolved"] for run in runs)
    assert report["summary"]["tuned"]["overall"]["resolved_rate"] == 0.0


def test_analogy_mode_keeps_answer_out_of_evidence() -> None:
    """迁移模式下证据不含目标答案, 且能度量是否给到可迁移经验."""
    report = run_selftest(
        seed=17,
        arms=("tuned",),
        levels=("relevant_only",),
        history_mode="analogy",
    )
    runs = report["runs"]
    assert runs
    assert all(not run["answer_present"] for run in runs)
    assert all(run["analogy_present"] is not None for run in runs)


def test_agent_adapter_is_judged_by_local_pytest(tmp_path) -> None:
    """外部 Agent 通道只负责改仓库, 是否解决由本地 pytest 判定."""
    agent = tmp_path / "agent.py"
    agent.write_text(
        "import json, re, subprocess, sys\n"
        "from pathlib import Path\n"
        "payload = json.loads(sys.stdin.read())\n"
        "repo = Path(payload['repo_path'])\n"
        "pattern = r'```[A-Za-z0-9_+.-]*[ \\t]+path=([^\\s`]+)\\n(.*?)```'\n"
        "blocks = []\n"
        "for item in payload['evidence']:\n"
        "    blocks.extend(re.findall(pattern, item, re.S))\n"
        "resolved = False\n"
        "for path, body in blocks:\n"
        "    target = repo / path\n"
        "    if not target.exists():\n"
        "        continue\n"
        "    target.write_text(body, encoding='utf-8')\n"
        "    done = subprocess.run([sys.executable, '-m', 'pytest', '-q', payload['test_file']],\n"
        "                         cwd=repo, capture_output=True)\n"
        "    if done.returncode == 0:\n"
        "        resolved = True\n"
        "        break\n"
        "claim = {'resolved': resolved, 'localized_path': payload['target_path']}\n"
        "sys.stdout.write(json.dumps(claim))\n",
        encoding="utf-8",
    )
    report = run_selftest(
        seed=17,
        arms=("tuned",),
        levels=("relevant_only",),
        agent_argv=[sys.executable, str(agent)],
        agent_timeout=120.0,
    )
    runs = report["runs"]
    assert runs
    assert all(run["resolved"] for run in runs)
    assert all(run["agent_claimed_resolved"] is True for run in runs)
