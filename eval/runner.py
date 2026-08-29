"""使用真实 LLM 和独立 verifier 顺序运行 Coding Agent 评测。"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

from coding_agent import __version__
from coding_agent.agent import Agent, DEFAULT_MAX_STEPS, MAX_MAX_STEPS, MIN_MAX_STEPS
from coding_agent.cli import SYSTEM_PROMPT
from coding_agent.config import ConfigurationError, Settings
from coding_agent.conversation import Conversation
from coding_agent.llm import LLMClient, ToolCall
from coding_agent.tools.registry import create_tool_registry
from coding_agent.verification import VerificationTracker


EVAL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_ROOT.parent
TASKS_FILE = EVAL_ROOT / "tasks.json"
RESULTS_DIR = EVAL_ROOT / "results"
VERIFIER_TIMEOUT_SECONDS = 30
MAX_EVAL_RUNS = 20


def load_tasks(task_id: str | None = None) -> list[dict[str, Any]]:
    """读取任务清单，并按可选 ID 选择单个任务。"""
    tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))["tasks"]
    if task_id is None:
        return tasks
    selected = [task for task in tasks if task["task_id"] == task_id]
    if not selected:
        raise ValueError(f"Unknown evaluation task: {task_id}")
    return selected


def run_evaluation(
    tasks: list[dict[str, Any]],
    client: LLMClient,
    max_steps: int = DEFAULT_MAX_STEPS,
    runs: int = 1,
    checkpoint_path: Path | None = None,
    metadata: dict[str, Any] | None = None,
    trace: bool = True,
) -> dict[str, Any]:
    """在隔离临时工作区中顺序运行任务，并逐项保存 checkpoint。"""
    if runs < 1 or runs > MAX_EVAL_RUNS:
        raise ValueError(f"runs must be between 1 and {MAX_EVAL_RUNS}")
    task_results: list[dict[str, Any]] = []
    report_metadata = dict(metadata or {})
    report_metadata.setdefault("started_at", _timestamp())
    report_metadata.setdefault("max_steps", max_steps)
    report_metadata.setdefault("requested_runs", runs)

    for run_number in range(1, runs + 1):
        for task in tasks:
            task_id = task["task_id"]
            _trace(trace, f"[eval][run {run_number}] running {task_id}")
            started_at = time.monotonic()
            result: dict[str, Any] = {
                "run": run_number,
                "task_id": task_id,
                "success": False,
                "agent_stop_reason": "exception",
                "steps": 0,
                "tool_calls": 0,
                "verification_status": "not_required",
                "verification_reminders": 0,
                "llm_retries": 0,
                "mutation_generations": 0,
                "agent_duration_seconds": 0.0,
                "verifier_duration_seconds": 0.0,
                "duration_seconds": 0.0,
                "fixture_integrity": True,
                "verifier_integrity": True,
            }
            try:
                with tempfile.TemporaryDirectory(
                    prefix="coding-agent-eval-"
                ) as directory:
                    temporary_root = Path(directory)
                    workspace = temporary_root / "workspace"
                    fixture = EVAL_ROOT / task["fixture"]
                    verifier = EVAL_ROOT / task["verifier"]
                    fixture_digest = _tree_digest(fixture)
                    verifier_source = verifier.read_bytes()
                    verifier_digest = hashlib.sha256(verifier_source).hexdigest()
                    shutil.copytree(fixture, workspace)

                    tracker = VerificationTracker()
                    callbacks = _trace_callbacks(task_id, run_number, tracker, trace)
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
                    agent_started_at = time.monotonic()
                    agent_result = agent.run(task["prompt"])
                    agent_duration = time.monotonic() - agent_started_at

                    result["fixture_integrity"] = _tree_digest(fixture) == fixture_digest
                    result["verifier_integrity"] = (
                        verifier.is_file()
                        and hashlib.sha256(verifier.read_bytes()).hexdigest()
                        == verifier_digest
                    )

                    verifier_directory = temporary_root / "independent-verifier"
                    verifier_directory.mkdir()
                    verifier_snapshot = verifier_directory / "verify.py"
                    verifier_snapshot.write_bytes(verifier_source)
                    verifier_started_at = time.monotonic()
                    verifier_result = subprocess.run(
                        [sys.executable, str(verifier_snapshot), str(workspace)],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=VERIFIER_TIMEOUT_SECONDS,
                        shell=False,
                        stdin=subprocess.DEVNULL,
                        check=False,
                    )
                    verifier_duration = time.monotonic() - verifier_started_at
                    integrity_ok = bool(
                        result["fixture_integrity"] and result["verifier_integrity"]
                    )
                    result.update(
                        {
                            "success": verifier_result.returncode == 0
                            and integrity_ok,
                            "agent_stop_reason": agent_result.stop_reason,
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
                            "agent_duration_seconds": round(agent_duration, 3),
                            "verifier_duration_seconds": round(
                                verifier_duration, 3
                            ),
                            "verifier_exit_code": verifier_result.returncode,
                            "verifier_output": _bounded_verifier_output(
                                verifier_result
                            ),
                            "verifier_sha256": verifier_digest,
                        }
                    )
            except Exception as error:
                result["error"] = f"{type(error).__name__}: {error}"
            finally:
                result["duration_seconds"] = round(
                    time.monotonic() - started_at, 3
                )
                task_results.append(result)
                _trace(
                    trace,
                    f"[eval][run {run_number}] {task_id}: "
                    f"{'PASS' if result['success'] else 'FAIL'}",
                )
                report = _build_report(
                    report_metadata,
                    task_results,
                    complete=False,
                )
                if checkpoint_path is not None:
                    _atomic_write_json(checkpoint_path, report)

    report_metadata["completed_at"] = _timestamp()
    report = _build_report(report_metadata, task_results, complete=True)
    if checkpoint_path is not None:
        _atomic_write_json(checkpoint_path, report)
    return report


def summarize(task_results: list[dict[str, Any]]) -> dict[str, Any]:
    """根据独立 verifier 结果计算多轮总体指标。"""
    count = len(task_results)
    passed = sum(bool(result["success"]) for result in task_results)
    denominator = count or 1
    steps = [int(result["steps"]) for result in task_results]
    tool_calls = [int(result["tool_calls"]) for result in task_results]
    durations = [float(result["duration_seconds"]) for result in task_results]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    grouped_runs: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for result in task_results:
        grouped[result["task_id"]].append(result)
        grouped_runs[int(result.get("run", 1))].append(result)

    per_task = {
        task_id: {
            "attempts": len(results),
            "passed": sum(bool(result["success"]) for result in results),
            "pass_rate": round(
                sum(bool(result["success"]) for result in results) / len(results),
                4,
            ),
        }
        for task_id, results in sorted(grouped.items())
    }
    suite_runs_passed = sum(
        bool(results) and all(result["success"] for result in results)
        for results in grouped_runs.values()
    )
    run_count = len(grouped_runs)
    task_passed_once = sum(
        any(result["success"] for result in results)
        for results in grouped.values()
    )
    unique_tasks = len(grouped)
    completed = sum(
        result["agent_stop_reason"] == "completed" for result in task_results
    )
    max_steps = sum(
        result["agent_stop_reason"] == "max_steps" for result in task_results
    )
    return {
        "tasks": count,
        "unique_tasks": unique_tasks,
        "runs": run_count,
        "passed": passed,
        "verified_success_rate": round(passed / denominator, 4),
        "task_pass_at_least_once_rate": round(
            task_passed_once / (unique_tasks or 1), 4
        ),
        "suite_runs_passed": suite_runs_passed,
        "suite_run_success_rate": round(
            suite_runs_passed / (run_count or 1), 4
        ),
        "agent_completion_rate": round(completed / denominator, 4),
        "max_steps_rate": round(max_steps / denominator, 4),
        "average_steps": _average(steps),
        "p50_steps": _percentile(steps, 0.50),
        "p95_steps": _percentile(steps, 0.95),
        "average_tool_calls": _average(tool_calls),
        "p50_tool_calls": _percentile(tool_calls, 0.50),
        "p95_tool_calls": _percentile(tool_calls, 0.95),
        "average_duration_seconds": _average(durations),
        "p50_duration_seconds": _percentile(durations, 0.50),
        "p95_duration_seconds": _percentile(durations, 0.95),
        "stop_reason_distribution": dict(
            sorted(
                Counter(
                    result["agent_stop_reason"] for result in task_results
                ).items()
            )
        ),
        "per_task": per_task,
    }


def build_metadata(
    settings: Settings,
    max_steps: int,
    runs: int,
    task_filter: str | None,
) -> dict[str, Any]:
    """构造不包含 API Key 和 Base URL 内容的可复现元数据。"""
    return {
        "schema_version": 2,
        "coding_agent_version": __version__,
        "git_commit": _git_commit(),
        "model": settings.model,
        "base_url_configured": settings.base_url is not None,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "max_steps": max_steps,
        "requested_runs": runs,
        "task_filter": task_filter,
        "started_at": _timestamp(),
    }


def new_results_path(prefix: str = "eval") -> Path:
    """为一次运行生成不易冲突的时间戳结果路径。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return RESULTS_DIR / f"{prefix}_{timestamp}.json"


def save_results(report: dict[str, Any], target: Path | None = None) -> Path:
    """原子保存评测 JSON，并返回最终路径。"""
    resolved_target = target or new_results_path()
    _atomic_write_json(resolved_target, report)
    return resolved_target


def _build_report(
    metadata: dict[str, Any],
    task_results: list[dict[str, Any]],
    complete: bool,
) -> dict[str, Any]:
    """构造 checkpoint 或最终报告。"""
    return {
        "metadata": dict(metadata),
        "complete": complete,
        "summary": summarize(task_results),
        "tasks": list(task_results),
    }


def _atomic_write_json(target: Path, report: dict[str, Any]) -> None:
    """通过同目录临时文件替换，避免留下半截 JSON。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def _trace_callbacks(
    task_id: str,
    run_number: int,
    tracker: VerificationTracker,
    enabled: bool,
) -> tuple[Any, Any, Any]:
    """创建不泄漏文件内容或命令输出的实时轨迹回调。"""
    prefix = f"[eval][run {run_number}][{task_id}]"

    def on_tool_call(step: int, tool_call: ToolCall) -> None:
        _trace(enabled, f"{prefix}[step {step}] tool={tool_call.name}")

    def on_tool_result(
        step: int,
        tool_call: ToolCall,
        result: dict[str, Any],
    ) -> None:
        del step
        if result.get("success") and tool_call.name == "run_command":
            data = result.get("data", {})
            outcome = (
                "timeout" if data.get("timed_out") else f"exit={data.get('exit_code')}"
            )
        elif result.get("success"):
            outcome = "success"
        else:
            outcome = f"error={result.get('error', 'ToolExecutionError')}"
        _trace(
            enabled,
            f"{prefix} result={outcome} "
            f"verification={tracker.verification_status}",
        )

    def on_llm_retry(retry_number: int, max_retries: int) -> None:
        _trace(
            enabled,
            f"{prefix} llm_retry={retry_number}/{max_retries}",
        )

    return on_tool_call, on_tool_result, on_llm_retry


def _tree_digest(root: Path) -> str:
    """为 fixture 文件树生成稳定摘要，以检测越界污染。"""
    digest = hashlib.sha256()
    for path in sorted(
        (candidate for candidate in root.rglob("*") if candidate.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _bounded_verifier_output(completed: subprocess.CompletedProcess[str]) -> str:
    """保留少量独立 verifier 输出，避免结果文件无限增长。"""
    output = (completed.stdout + completed.stderr).strip()
    return output[:2000]


def _average(values: list[int | float]) -> float:
    """计算可用于空集合的平均值。"""
    return round(sum(values) / len(values), 3) if values else 0.0


def _percentile(values: list[int | float], fraction: float) -> int | float:
    """使用 nearest-rank 计算小样本的确定性分位数。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(fraction * len(ordered) + 0.999999) - 1))
    return round(ordered[index], 3)


def _git_commit() -> str | None:
    """读取当前 Git commit；非 Git 环境返回空值。"""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _timestamp() -> str:
    """返回带本地时区的 ISO 时间。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _trace(enabled: bool, message: str) -> None:
    """立即输出可安全展示的 Runner 进度。"""
    if enabled:
        print(message, flush=True)


def main(argv: list[str] | None = None) -> int:
    """解析参数、创建真实 LLM Client 并运行评测。"""
    parser = argparse.ArgumentParser(description="Run Coding Agent evaluation")
    parser.add_argument("--task", help="run one task by task_id")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=DEFAULT_MAX_STEPS,
        help=(
            "agent step limit "
            f"({MIN_MAX_STEPS}-{MAX_MAX_STEPS})"
        ),
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help=f"sequential repetitions (default: 1, range: 1-{MAX_EVAL_RUNS})",
    )
    parser.add_argument(
        "--quiet-trace",
        action="store_true",
        help="hide per-step safe progress summaries",
    )
    arguments = parser.parse_args(argv)
    if not MIN_MAX_STEPS <= arguments.max_steps <= MAX_MAX_STEPS:
        parser.error(
            f"max-steps must be between {MIN_MAX_STEPS} and {MAX_MAX_STEPS}"
        )
    if not 1 <= arguments.runs <= MAX_EVAL_RUNS:
        parser.error(f"runs must be between 1 and {MAX_EVAL_RUNS}")
    try:
        tasks = load_tasks(arguments.task)
        settings = Settings.from_env()
        client = LLMClient(settings)
    except (ConfigurationError, ValueError) as error:
        parser.error(str(error))

    target = new_results_path()
    metadata = build_metadata(
        settings,
        arguments.max_steps,
        arguments.runs,
        arguments.task,
    )
    report = run_evaluation(
        tasks,
        client,
        arguments.max_steps,
        runs=arguments.runs,
        checkpoint_path=target,
        metadata=metadata,
        trace=not arguments.quiet_trace,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Results: {target}")
    return 0 if report["summary"]["passed"] == report["summary"]["tasks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
