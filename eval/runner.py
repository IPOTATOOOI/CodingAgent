"""使用真实 LLM 和独立 verifier 顺序运行小型 Coding Agent 评测。"""

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

from coding_agent.agent import Agent, DEFAULT_MAX_STEPS, MAX_MAX_STEPS, MIN_MAX_STEPS
from coding_agent.cli import SYSTEM_PROMPT
from coding_agent.config import ConfigurationError, Settings
from coding_agent.conversation import Conversation
from coding_agent.llm import LLMClient
from coding_agent.tools.registry import create_tool_registry


EVAL_ROOT = Path(__file__).resolve().parent
TASKS_FILE = EVAL_ROOT / "tasks.json"
RESULTS_DIR = EVAL_ROOT / "results"
VERIFIER_TIMEOUT_SECONDS = 30


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
) -> dict[str, Any]:
    """在彼此隔离的临时工作区中顺序运行任务。"""
    task_results = []
    for task in tasks:
        print(f"[eval] running {task['task_id']}")
        started_at = time.monotonic()
        result: dict[str, Any] = {
            "task_id": task["task_id"],
            "success": False,
            "agent_stop_reason": "exception",
            "steps": 0,
            "tool_calls": 0,
            "verification_status": "not_required",
            "duration_seconds": 0.0,
        }
        try:
            with tempfile.TemporaryDirectory(prefix="coding-agent-eval-") as directory:
                temporary_root = Path(directory)
                workspace = temporary_root / "workspace"
                fixture = EVAL_ROOT / task["fixture"]
                shutil.copytree(fixture, workspace)

                agent = Agent(
                    llm_client=client,
                    conversation=Conversation(SYSTEM_PROMPT),
                    tool_registry=create_tool_registry(workspace),
                    max_steps=max_steps,
                )
                agent_result = agent.run(task["prompt"])
                verifier = EVAL_ROOT / task["verifier"]
                verifier_result = subprocess.run(
                    [sys.executable, str(verifier), str(workspace)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=VERIFIER_TIMEOUT_SECONDS,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    check=False,
                )
                result.update(
                    {
                        "success": verifier_result.returncode == 0,
                        "agent_stop_reason": agent_result.stop_reason,
                        "steps": agent_result.steps,
                        "tool_calls": agent_result.tool_calls,
                        "verification_status": agent_result.verification_status,
                        "verifier_exit_code": verifier_result.returncode,
                        "verifier_output": _bounded_verifier_output(verifier_result),
                    }
                )
        except Exception as error:
            result["error"] = f"{type(error).__name__}: {error}"
        finally:
            result["duration_seconds"] = round(time.monotonic() - started_at, 3)
            task_results.append(result)
            print(
                f"[eval] {task['task_id']}: "
                f"{'PASS' if result['success'] else 'FAIL'}"
            )
    return {"summary": summarize(task_results), "tasks": task_results}


def summarize(task_results: list[dict[str, Any]]) -> dict[str, Any]:
    """根据独立 verifier 结果计算总体指标。"""
    count = len(task_results)
    passed = sum(bool(result["success"]) for result in task_results)
    denominator = count or 1
    return {
        "tasks": count,
        "passed": passed,
        "verified_success_rate": round(passed / denominator, 4),
        "average_steps": round(
            sum(result["steps"] for result in task_results) / denominator,
            3,
        ),
        "average_tool_calls": round(
            sum(result["tool_calls"] for result in task_results) / denominator,
            3,
        ),
        "average_duration_seconds": round(
            sum(result["duration_seconds"] for result in task_results) / denominator,
            3,
        ),
        "stop_reason_distribution": dict(
            sorted(Counter(
                result["agent_stop_reason"] for result in task_results
            ).items())
        ),
    }


def save_results(report: dict[str, Any]) -> Path:
    """将一次真实评测原样保存为带时间戳的 JSON。"""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = RESULTS_DIR / f"eval_{timestamp}.json"
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def _bounded_verifier_output(completed: subprocess.CompletedProcess[str]) -> str:
    """保留少量独立 verifier 输出，避免结果文件无限增长。"""
    output = (completed.stdout + completed.stderr).strip()
    return output[:2000]


def main(argv: list[str] | None = None) -> int:
    """解析参数、创建真实 LLM Client 并运行评测。"""
    parser = argparse.ArgumentParser(description="Run Coding Agent evaluation")
    parser.add_argument("--task", help="run one task by task_id")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=DEFAULT_MAX_STEPS,
        help=f"agent step limit ({MIN_MAX_STEPS}-{MAX_MAX_STEPS})",
    )
    arguments = parser.parse_args(argv)
    if not MIN_MAX_STEPS <= arguments.max_steps <= MAX_MAX_STEPS:
        parser.error(
            f"max-steps must be between {MIN_MAX_STEPS} and {MAX_MAX_STEPS}"
        )
    try:
        tasks = load_tasks(arguments.task)
        client = LLMClient(Settings.from_env())
    except (ConfigurationError, ValueError) as error:
        parser.error(str(error))

    report = run_evaluation(tasks, client, arguments.max_steps)
    target = save_results(report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Results: {target}")
    return 0 if report["summary"]["passed"] == report["summary"]["tasks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
