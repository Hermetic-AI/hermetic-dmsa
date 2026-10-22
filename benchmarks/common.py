"""benchmark 公共工具：endpoint client、调用计数、结果落盘。

用法（key 只走环境变量，不落盘）：

    export DMSA_BENCH_API_KEY=sk-...
    export DMSA_BENCH_BASE_URL=https://.../compatible-mode/v1
    PYTHONPATH=src python benchmarks/bench_sga_conflict.py
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parent / "results"

BASE_URL = os.environ.get("DMSA_BENCH_BASE_URL", "")
API_KEY = os.environ.get("DMSA_BENCH_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
CHAT_MODEL = os.environ.get("DMSA_BENCH_CHAT_MODEL", "qwen3.5-flash")
EMBEDDING_MODEL = os.environ.get("DMSA_BENCH_EMBEDDING_MODEL", "text-embedding-v3")
EMBEDDING_DIM = int(os.environ.get("DMSA_BENCH_EMBEDDING_DIM", "1024"))


class LLMCaller:
    """带调用计数 + 指数退避重试的 chat 封装。

    Attributes:
        chat_calls: chat completions 调用次数（含重试）。
    """

    def __init__(self) -> None:
        if not API_KEY or not BASE_URL:
            raise SystemExit(
                "set DMSA_BENCH_API_KEY and DMSA_BENCH_BASE_URL first"
            )
        import openai

        self.client = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL)
        self.chat_calls = 0

    def chat(self, *, system: str, user: str, max_tokens: int = 1500) -> str:
        """调一次 chat completions，失败指数退避重试 4 次。"""
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                self.chat_calls += 1
                resp = self.client.chat.completions.create(
                    model=CHAT_MODEL,
                    temperature=0.0,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                return (resp.choices[0].message.content or "").strip()
            except Exception as exc:  # noqa: BLE001 - benchmark 重试任意瞬时错误
                last_exc = exc
                time.sleep(2**attempt)
        raise RuntimeError(f"chat failed after 4 attempts: {last_exc}")


def word_count(text: str) -> int:
    """英文 word count（benchmark 问题全用英文，保证判定确定）。"""
    return len(text.split())


def dump_results(name: str, payload: dict[str, Any]) -> Path:
    """结果写入 benchmarks/results/<name>.json。"""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{name}.json"
    payload = {"benchmark": name, "timestamp": time.time(), **payload}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
