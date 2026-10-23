# ruff: noqa: D415
"""从 Qoder transcript 增量抽取单轮编码过程证据。"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

MAX_CONTENT_CHARS = 3000
MAX_PROMPT_CHARS = 700
MAX_ASSISTANT_CHARS = 1200
MAX_ERROR_CHARS = 240
MAX_KEYWORDS = 28

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)\b(?:bearer|token)\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|password|passwd|access[_-]?token)\b\s*[:=]\s*\S+"),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),
)
ASCII_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.\-/]{2,}\b")
STOP_TOKENS = frozenset(
    {
        "the", "and", "for", "with", "this", "that", "from", "have", "will",
        "true", "false", "null", "none", "type", "str", "int", "bool", "list",
        "dict", "self", "def", "class", "import", "return", "json", "text",
    }
)
NOISE_PREFIXES = ("<system-reminder>", "<command-name>", "<user-prompt-submit-hook>", "[system]")


@dataclass
class Turn:
    """一次 Stop 事件覆盖到的增量轮次。"""

    session_id: str
    cwd: str
    branch: str
    start_line: int
    end_line: int
    prompts: list[str] = field(default_factory=list)
    conclusions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)
    prompt_ids: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """没有用户意图也没有结论时不值得写入记忆。"""
        return not self.prompts and not self.conclusions and not self.errors

    def request_id(self, scope: str) -> str:
        """由会话与行区间派生稳定幂等键。"""
        digest = hashlib.sha1(
            f"{self.session_id}:{self.start_line}:{self.end_line}:{scope}".encode()
        ).hexdigest()[:12]
        return f"qoder-hook-{self.session_id[:8]}-L{self.start_line}-{digest}"


def redact(text: str) -> str:
    """替换凭据形态片段, 不改变其余内容。"""
    out = text
    for pattern in SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


def _blocks(message: Any, block_type: str) -> list[dict[str, Any]]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == block_type]


def _is_human_prompt(rec: dict[str, Any]) -> bool:
    origin = rec.get("origin")
    return isinstance(origin, dict) and origin.get("kind") == "human"


def _tool_target(block: dict[str, Any]) -> str | None:
    """从 tool_use 输入里取文件/路径标识, 供检索用 ASCII token。"""
    inp = block.get("input")
    if not isinstance(inp, dict):
        return None
    for key in ("file_path", "path", "notebook_path", "url"):
        value = inp.get(key)
        if isinstance(value, str) and value.strip():
            return f"{block.get('name', 'tool')}:{value.strip().splitlines()[0][:120]}"
    return None


def _error_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("text")]
        return " ".join(parts)
    return ""


def parse_turn(lines: list[str], start_line: int, session_hint: str) -> Turn:
    """从 start_line 起扫描 transcript, 抽取人类意图、结论、失败与改动面。"""
    turn = Turn(
        session_id=session_hint, cwd="", branch="", start_line=start_line, end_line=len(lines)
    )
    tool_names: dict[str, str] = {}
    for index in range(start_line, len(lines)):
        raw = lines[index].strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict) or rec.get("isSidechain"):
            continue
        rtype = rec.get("type")
        if rtype not in ("user", "assistant"):
            continue
        turn.session_id = str(rec.get("sessionId") or turn.session_id)
        turn.cwd = str(rec.get("cwd") or turn.cwd)
        turn.branch = str(rec.get("gitBranch") or turn.branch)
        pid = rec.get("promptId")
        if isinstance(pid, str) and pid and pid not in turn.prompt_ids:
            turn.prompt_ids.append(pid)
        message = rec.get("message")
        if rtype == "user":
            if _is_human_prompt(rec):
                for block in _blocks(message, "text"):
                    _absorb_prompt(turn, str(block.get("text", "")))
            for block in _blocks(message, "tool_result"):
                if block.get("is_error"):
                    _absorb_error(turn, _error_text(block))
            continue
        for block in _blocks(message, "tool_use"):
            target = _tool_target(block)
            if target:
                tool_names[str(block.get("id", index))] = target
        for block in _blocks(message, "text"):
            _absorb_conclusion(turn, str(block.get("text", "")))
    for target in tool_names.values():
        if target not in turn.touched:
            turn.touched.append(target)
    turn.touched = turn.touched[:12]
    return turn


def _absorb_prompt(turn: Turn, text: str) -> None:
    clean = text.strip()
    if not clean or any(clean.lower().startswith(p) for p in NOISE_PREFIXES):
        return
    trimmed = redact(clean[:MAX_PROMPT_CHARS])
    if trimmed not in turn.prompts:
        turn.prompts.append(trimmed)


def _absorb_conclusion(turn: Turn, text: str) -> None:
    clean = text.strip()
    if len(clean) < 24:
        return
    turn.conclusions.append(redact(clean[:MAX_ASSISTANT_CHARS]))


def _absorb_error(turn: Turn, text: str) -> None:
    clean = " ".join(text.split())
    if not clean:
        return
    snippet = redact(clean[:MAX_ERROR_CHARS])
    if snippet not in turn.errors:
        turn.errors.append(snippet)


def keywords(turn: Turn) -> str:
    """聚合 ASCII 标识符: unicode61 分词下中文整句成单 token, 靠这些词保证召回。"""
    corpus = " ".join(turn.prompts + turn.conclusions + turn.errors + turn.touched)
    seen: list[str] = []
    for token in ASCII_TOKEN_RE.findall(corpus):
        low = token.lower()
        if low in STOP_TOKENS or token in seen:
            continue
        seen.append(token)
        if len(seen) >= MAX_KEYWORDS:
            break
    return " ".join(seen)


def build_memory_text(turn: Turn, scope: str) -> str:
    """生成 CodingMemory 记录正文。"""
    parts: list[str] = [f"[Qoder 编码轮次] project={scope} branch={turn.branch or 'n/a'}"]
    if turn.prompts:
        parts.append("任务: " + " | ".join(turn.prompts[-3:]))
    if turn.touched:
        parts.append("改动面: " + "; ".join(turn.touched))
    if turn.errors:
        parts.append("失败: " + " | ".join(turn.errors[-3:]))
    if turn.conclusions:
        parts.append("结论: " + " | ".join(turn.conclusions[-2:]))
    kw = keywords(turn)
    if kw:
        parts.append("关键词: " + kw)
    text = "\n".join(parts)
    return text[:MAX_CONTENT_CHARS]
