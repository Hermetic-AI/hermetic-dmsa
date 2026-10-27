# ruff: noqa: RUF001
"""本地自出题: 合成仓库、带噪历史与可判分修复."""

from __future__ import annotations

import random
from dataclasses import dataclass

NOISE_LEVELS = ("relevant_only", "L1", "L2", "L3", "L4")
NOISE_COUNTS = {"relevant_only": 0, "L1": 20, "L2": 100, "L3": 400, "L4": 1000}
_MAX_NOISE = max(NOISE_COUNTS.values())


_FILLERS = (
    "该结论来自旧版本，升级后默认值和边界行为可能已经变化，需重新核对。",
    "当时还排查了缓存命中率、日志采样和配置加载顺序，未发现直接关联。",
    "运行环境为独立临时目录，命令输出包含若干无关栈帧和依赖警告。",
    "相关讨论里出现过两种相反做法，其中一种在回归测试中被证伪。",
    "该会话没有留下最终验证记录，只保留了中间尝试和失败输出。",
    "另一个模块的相似缺陷采用不同修复方式，不能直接套用到当前路径。",
    "历史记录中的版本号、超时参数和测试范围都与当前任务不完全一致。",
    "维护者建议先补失败用例，再改动实现，避免只修复表象。",
    "同目录下还有自动生成文件，修改后容易被构建流程覆盖。",
    "审查意见提到兼容旧调用方，但未给出对应的验收测试。",
)


@dataclass(frozen=True)
class Case:
    """一个可独立判分的本地任务."""

    case_id: str
    kind: str
    query: str
    files: dict[str, str]
    test_file: str
    target_path: str
    sessions: tuple[tuple[tuple[str, str], ...], ...]
    noise: tuple[str, ...]
    gold_text: str


def _block(path: str, body: str) -> str:
    """生成带路径标记的闭合代码块; 截断或缺失闭合围栏的块不会被求解器采用."""
    return f"```python path={path}\n{body}```"


_TOPICS = (
    ("审查 {t} 的 {s}: 参数命名与调用方文档不一致，先核对接口契约。", "契约"),
    ("运行 {t} 相关用例时日志出现采样丢失，怀疑与并发写入和缓存命中率有关。", "日志"),
    ("类型检查 {t} 中 {s} 的返回值标注不完整，需要补充边界用例。", "类型"),
    ("构建流程会重新生成 {t}，确认修改是否会被模板覆盖，并检查格式化顺序。", "构建"),
    ("历史提交曾调整 {t} 的默认参数顺序，回滚记录显示回归测试覆盖不足。", "历史"),
    ("依赖升级后 {t} 的异常栈发生变化，需要确认是否影响 {s} 的失败分支。", "依赖"),
    ("监控看板显示 {t} 调用量在夜间激增，可能与批量任务的重试风暴相关。", "监控"),
    ("文档示例中的 {t} 与当前 {s} 签名不一致，需要确认参数是否可选。", "文档"),
)


def _debug_session(
    target: str, symbol: str, wrong_body: str, gold: str
) -> tuple[tuple[str, str], ...]:
    """生成同会话多轮排查: 最终补丁位于会话后段, 覆盖平台每会话命中数受限的真实症状."""
    messages: list[tuple[str, str]] = []
    for index, (topic, filler) in enumerate(_TOPICS):
        body = topic.format(t=target, s=symbol) + "补充排查说明：" + filler * 100
        if index % 3 == 1:
            body += f"\n{_block(target, wrong_body)}"
        messages.append(("assistant", body))
    messages.append(("assistant", f"最终修复如下：\n{gold}\n验证：pytest 全部通过。"))
    return tuple(messages)



def _noise(
    seed: int,
    label: str,
    target: str,
    symbol: str,
    wrong_bodies: tuple[str, ...],
    alt_paths: tuple[str, ...],
) -> tuple[str, ...]:
    """确定性地生成同主题噪声历史: 错补丁、旧尝试、失败日志与无关讨论."""
    rng = random.Random(f"{seed}:{label}")
    messages: list[str] = []
    for index in range(_MAX_NOISE):
        context = "".join(rng.choice(_FILLERS) for _ in range(6))
        style = index % 5
        # 约三成噪声命中当前任务, 其余是相似但不同的模块, 避免人为制造全库同题。
        related = rng.random() < 0.3
        decoy = f"alt_{label}_{index}"
        path = target if related else alt_paths[index % len(alt_paths)]
        name = symbol if related else f"{decoy}_helper"
        if style in {0, 1}:
            body = wrong_bodies[index % len(wrong_bodies)] if related else (
                f"def {name}(value):\n    return value\n"
            )
            if style == 1:
                body = f"# 旧会话第 {index} 次尝试，未通过验证\n{body}"
            messages.append(f"历史修复尝试 {index}:\n{_block(path, body)}\n补充记录：{context}")
        elif style == 2:
            messages.append(
                f"另一个模块的改动 {index}:\n"
                f"```python path={path}\ndef helper_{index}(value):\n    return value\n```\n"
                f"排查记录：{context}"
            )
        elif style == 3:
            messages.append(
                f"运行记录 {index}: {path} 相关测试 FAILED，"
                f"AssertionError 出现在 {name}，需要重新核对边界。{context}"
            )
        else:
            messages.append(
                f"讨论 {index}: {name} 的兼容性说明与默认值，"
                f"涉及 {path}，历史结论未经验证，仅供参考。{context}"
            )
    rng.shuffle(messages)
    return tuple(messages)


def _clamp_case(seed: int) -> Case:
    """缺陷场景: clamp 只处理上界, 修复需要同时保证下界."""
    module = f"ops_{seed}_clamp"
    symbol = f"clamp_{seed}"
    target = f"{module}.py"
    stub = f"def {symbol}(value, lower, upper):\n    return min(upper, value)\n"
    fixed = f"def {symbol}(value, lower, upper):\n    return max(lower, min(upper, value))\n"
    test = (
        f"from {module} import {symbol}\n\n\n"
        "def test_lower():\n"
        f"    assert {symbol}(-5, 0, 10) == 0\n\n\n"
        "def test_upper():\n"
        f"    assert {symbol}(15, 0, 10) == 10\n\n\n"
        "def test_inside():\n"
        f"    assert {symbol}(5, 0, 10) == 5\n"
    )
    gold = _block(target, fixed)
    sessions = (
        (
            ("user", f"{target} 的 {symbol} 在负数和越界输入上结果错误，需要修复。"),
            ("assistant", "先复现边界测试，再定位是上界还是下界缺失。"),
        ),
        (("tool", "FAILED test_lower\nAssertionError: assert 0 == -5"),),
        _debug_session(target, symbol, stub, gold),
    )
    alt = {f"alt_{module}_{index}.py": f"def helper_{index}(value):\n    return value\n"
           for index in range(3)}
    return Case(
        case_id=f"clamp-{seed}",
        kind="bug",
        query=f"{target} 的 {symbol} 对负数输入返回错误，下界没有生效，需要修复边界判断",
        files={target: stub, f"test_{module}.py": test, **alt},
        test_file=f"test_{module}.py",
        target_path=target,
        sessions=sessions,
        noise=_noise(
            seed,
            "clamp",
            target,
            symbol,
            (stub, f"def {symbol}(value, lower, upper):\n    return value\n"),
            tuple(alt),
        ),
        gold_text=gold,
    )


def _slug_case(seed: int) -> Case:
    """功能场景: 按历史规格实现 slugify."""
    module = f"ops_{seed}_slug"
    target = f"{module}.py"
    stub = "def slugify(text):\n    raise NotImplementedError\n"
    fixed = 'def slugify(text):\n    return "-".join(text.lower().split())\n'
    test = (
        f"from {module} import slugify\n\n\n"
        "def test_basic():\n"
        '    assert slugify("Hello World") == "hello-world"\n\n\n'
        "def test_spaces():\n"
        '    assert slugify(" A  B ") == "a-b"\n'
    )
    gold = _block(target, fixed)
    sessions = (
        (
            ("user", f"请实现 {target} 的 slugify，要求小写并用连字符连接单词。"),
            ("assistant", "先写下验收用例，再按历史规格实现。"),
        ),
        (("tool", "FAILED test_basic\nAssertionError: 'hello world' != 'hello-world'"),),
        _debug_session(target, "slugify", stub, gold),
    )
    alt = {f"alt_{module}_{index}.py": f"def helper_{index}(value):\n    return value\n"
           for index in range(3)}
    return Case(
        case_id=f"slug-{seed}",
        kind="feature",
        query=f"实现 {target} 的 slugify：小写、按空白合并、用连字符连接，验收见测试",
        files={target: stub, f"test_{module}.py": test, **alt},
        test_file=f"test_{module}.py",
        target_path=target,
        sessions=sessions,
        noise=_noise(
            seed,
            "slug",
            target,
            "slugify",
            (stub, 'def slugify(text):\n    return text.lower()\n'),
            tuple(alt),
        ),
        gold_text=gold,
    )


def _retry_case(seed: int) -> Case:
    """缺陷场景: 指数退避缺少上限, 修复需要封顶."""
    module = f"ops_{seed}_retry"
    symbol = f"backoff_{seed}"
    target = f"{module}.py"
    stub = f"def {symbol}(attempt):\n    return 0.1 * (2 ** attempt)\n"
    fixed = f"def {symbol}(attempt):\n    return min(1.6, 0.1 * (2 ** attempt))\n"
    test = (
        f"from {module} import {symbol}\n\n\n"
        "def test_short():\n"
        f"    assert round({symbol}(3), 2) == 0.8\n\n\n"
        "def test_cap():\n"
        f"    assert round({symbol}(10), 2) == 1.6\n"
    )
    gold = _block(target, fixed)
    sessions = (
        (
            ("user", f"{target} 的 {symbol} 在多次重试后时间不可控，需要设置上限。"),
            ("assistant", "先跑高重试次数的用例，确认是否属于缺少封顶。"),
        ),
        (("tool", "FAILED test_cap\nAssertionError: 102.4 != 1.6"),),
        _debug_session(target, symbol, stub, gold),
    )
    alt = {f"alt_{module}_{index}.py": f"def helper_{index}(value):\n    return value\n"
           for index in range(3)}
    return Case(
        case_id=f"retry-{seed}",
        kind="bug",
        query=f"{target} 的 {symbol} 在高重试次数下没有上限，退避时间异常增大，需要封顶",
        files={target: stub, f"test_{module}.py": test, **alt},
        test_file=f"test_{module}.py",
        target_path=target,
        sessions=sessions,
        noise=_noise(
            seed,
            "retry",
            target,
            symbol,
            (stub, f"def {symbol}(attempt):\n    return 0.1 * attempt\n"),
            tuple(alt),
        ),
        gold_text=gold,
    )


def build_cases(seed: int) -> tuple[Case, ...]:
    """按 seed 生成固定任务集; 每次运行可复现, 但模块名与常量随 seed 变化."""
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    return (_clamp_case(seed), _slug_case(seed), _retry_case(seed))
