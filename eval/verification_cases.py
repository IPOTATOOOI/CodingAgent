"""使用真实 LLM 运行并保存 Verification Completion Gate Cases A～D。"""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable

from coding_agent.agent import Agent, DEFAULT_MAX_STEPS, MAX_MAX_STEPS, MIN_MAX_STEPS
from coding_agent.cli import SYSTEM_PROMPT
from coding_agent.config import ConfigurationError, Settings
from coding_agent.conversation import Conversation
from coding_agent.llm import LLMClient
from coding_agent.tools.registry import create_tool_registry
from coding_agent.verification import VerificationTracker
if __package__:
    from eval.runner import (
        _atomic_write_json,
        _timestamp,
        _trace,
        _trace_callbacks,
        build_metadata,
        new_results_path,
    )
else:
    from runner import (
        _atomic_write_json,
        _timestamp,
        _trace,
        _trace_callbacks,
        build_metadata,
        new_results_path,
    )


CaseVerifier = Callable[[Path], None]


def build_cases(python_executable: str = sys.executable) -> list[dict[str, Any]]:
    """构造四种 Completion Gate 行为的隔离验收场景。"""
    add_command = json.dumps([python_executable, "test_calculator.py"])
    app_command = json.dumps([python_executable, "test_app.py"])
    return [
        {
            "case": "A",
            "description": "未验证 Final Answer 被拒绝，随后验证通过",
            "files": {
                "calculator.py": "def add(a, b):\n    return a - b\n",
                "test_calculator.py": (
                    "from calculator import add\n"
                    "assert add(2, 3) == 5\n"
                ),
            },
            "prompt": (
                "Fix calculator.py. Immediately after editing, try to finish without "
                "running a command. If the Runtime requires verification, run "
                f"{add_command} and finish after it passes."
            ),
            "verifier": _verify_add,
        },
        {
            "case": "B",
            "description": "一次修复后验证失败，继续修复并重新验证",
            "files": {
                "calculator.py": (
                    "def add(a, b):\n    return a - b\n\n"
                    "def multiply(a, b):\n    return a + b\n"
                ),
                "test_calculator.py": (
                    "from calculator import add, multiply\n"
                    "assert add(2, 3) == 5\n"
                    "assert multiply(2, 3) == 6\n"
                ),
            },
            "prompt": (
                "This project contains two arithmetic defects. First repair only add, "
                f"then run {add_command}; use the remaining failure to repair multiply, "
                "rerun the same test, and finish after it passes."
            ),
            "verifier": _verify_arithmetic,
        },
        {
            "case": "C",
            "description": "验证通过后再次修改，必须重新验证",
            "files": {
                "app.py": "def value():\n    return 1\n",
                "test_app.py": "from app import value\nassert value() == 2\n",
            },
            "prompt": (
                "Change app.py so value() returns 2 and run "
                f"{app_command}. After it passes, edit app.py again by adding a module "
                "docstring. Try to finish; if the Runtime requires fresh verification, "
                "rerun the same test and then finish."
            ),
            "verifier": _verify_value,
        },
        {
            "case": "D",
            "description": "纯文档修改不要求代码验证",
            "files": {"README.md": "# Demo\n"},
            "prompt": (
                "Add the exact sentence 'Stage 7 verification demo' to README.md. "
                "This is documentation-only; do not run commands and finish after "
                "the edit."
            ),
            "verifier": _verify_readme,
        },
    ]


def run_cases(
    client: LLMClient,
    cases: list[dict[str, Any]],
    max_steps: int,
    checkpoint_path: Path | None = None,
    metadata: dict[str, Any] | None = None,
    trace: bool = True,
) -> dict[str, Any]:
    """顺序运行真实 Verification Cases，并逐项写入 checkpoint。"""
    results: list[dict[str, Any]] = []
    report_metadata = dict(metadata or {})
    report_metadata.setdefault("started_at", _timestamp())
    report_metadata["kind"] = "verification_cases"
    for definition in cases:
        case_id = definition["case"]
        _trace(trace, f"[verification-case] running {case_id}")
        started_at = time.monotonic()
        case_result: dict[str, Any] = {
            "case": case_id,
            "description": definition["description"],
            "success": False,
            "stop_reason": "exception",
            "steps": 0,
            "tool_calls": 0,
            "verification_status": "not_required",
            "verification_reminders": 0,
            "llm_retries": 0,
            "mutation_generations": 0,
            "duration_seconds": 0.0,
        }
        try:
            with tempfile.TemporaryDirectory(
                prefix=f"stage7-case-{case_id.lower()}-"
            ) as directory:
                workspace = Path(directory)
                for relative_path, content in definition["files"].items():
                    target = workspace / relative_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("w", encoding="utf-8", newline="") as file:
                        file.write(content)
                tracker = VerificationTracker()
                callbacks = _trace_callbacks(
                    f"case-{case_id}", 1, tracker, trace
                )
                agent = Agent(
                    llm_client=client,
                    conversation=Conversation(SYSTEM_PROMPT),
                    tool_registry=create_tool_registry(workspace),
                    max_steps=max_steps,
                    verification_tracker=tracker,
                    on_tool_call=callbacks[0],
                    on_tool_result=callbacks[1],
                    on_llm_retry=callbacks[2],
                )
                agent_result = agent.run(definition["prompt"])
                case_result.update(
                    {
                        "stop_reason": agent_result.stop_reason,
                        "steps": agent_result.steps,
                        "tool_calls": agent_result.tool_calls,
                        "verification_status": agent_result.verification_status,
                        "verification_reminders": (
                            agent_result.verification_reminders
                        ),
                        "llm_retries": agent_result.llm_retries,
                        "mutation_generations": (
                            agent_result.mutation_generations
                        ),
                    }
                )
                definition["verifier"](workspace)
                case_result["success"] = True
        except Exception as error:
            case_result["error"] = f"{type(error).__name__}: {error}"
        finally:
            case_result["duration_seconds"] = round(
                time.monotonic() - started_at, 3
            )
            results.append(case_result)
            _trace(
                trace,
                f"[verification-case] {case_id}: "
                f"{'PASS' if case_result['success'] else 'FAIL'}",
            )
            report = _case_report(report_metadata, results, complete=False)
            if checkpoint_path is not None:
                _atomic_write_json(checkpoint_path, report)

    report_metadata["completed_at"] = _timestamp()
    report = _case_report(report_metadata, results, complete=True)
    if checkpoint_path is not None:
        _atomic_write_json(checkpoint_path, report)
    return report


def _case_report(
    metadata: dict[str, Any],
    results: list[dict[str, Any]],
    complete: bool,
) -> dict[str, Any]:
    """构造 Verification Cases checkpoint。"""
    passed = sum(bool(result["success"]) for result in results)
    return {
        "metadata": dict(metadata),
        "complete": complete,
        "summary": {
            "cases": len(results),
            "passed": passed,
            "success_rate": round(passed / (len(results) or 1), 4),
            "stop_reason_distribution": dict(
                sorted(Counter(result["stop_reason"] for result in results).items())
            ),
        },
        "cases": list(results),
    }


def _verify_add(workspace: Path) -> None:
    """独立检查单个加法修复。"""
    namespace: dict[str, Any] = {}
    exec((workspace / "calculator.py").read_text(encoding="utf-8"), namespace)
    assert namespace["add"](2, 3) == 5


def _verify_arithmetic(workspace: Path) -> None:
    """独立检查失败验证后的双缺陷修复。"""
    namespace: dict[str, Any] = {}
    exec((workspace / "calculator.py").read_text(encoding="utf-8"), namespace)
    assert namespace["add"](2, 3) == 5
    assert namespace["multiply"](2, 3) == 6


def _verify_value(workspace: Path) -> None:
    """独立检查二次 mutation 后的行为。"""
    namespace: dict[str, Any] = {}
    exec((workspace / "app.py").read_text(encoding="utf-8"), namespace)
    assert namespace["value"]() == 2


def _verify_readme(workspace: Path) -> None:
    """独立检查文档修改。"""
    assert "Stage 7 verification demo" in (
        workspace / "README.md"
    ).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """解析参数并运行真实 Completion Gate 验收。"""
    parser = argparse.ArgumentParser(description="Run Stage 7 verification cases")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=DEFAULT_MAX_STEPS,
        help=f"agent step limit ({MIN_MAX_STEPS}-{MAX_MAX_STEPS})",
    )
    parser.add_argument(
        "--case",
        choices=("A", "B", "C", "D"),
        help="run one verification case",
    )
    parser.add_argument("--quiet-trace", action="store_true")
    arguments = parser.parse_args(argv)
    if not MIN_MAX_STEPS <= arguments.max_steps <= MAX_MAX_STEPS:
        parser.error(
            f"max-steps must be between {MIN_MAX_STEPS} and {MAX_MAX_STEPS}"
        )
    try:
        settings = Settings.from_env()
        client = LLMClient(settings)
    except ConfigurationError as error:
        parser.error(str(error))

    target = new_results_path("verification_cases")
    metadata = build_metadata(settings, arguments.max_steps, 1, None)
    cases = build_cases()
    if arguments.case is not None:
        cases = [case for case in cases if case["case"] == arguments.case]
    report = run_cases(
        client,
        cases,
        arguments.max_steps,
        checkpoint_path=target,
        metadata=metadata,
        trace=not arguments.quiet_trace,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Results: {target}")
    return 0 if report["summary"]["passed"] == report["summary"]["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
