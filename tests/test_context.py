"""有界上下文视图、工具结果压缩与协议分组测试。"""

from copy import deepcopy
import json
import unittest

from coding_agent.context import ContextManager


def tool_interaction(
    call_id: str,
    name: str,
    arguments: dict,
    result: dict,
) -> list[dict]:
    """构造一组原生 Assistant Tool Call 与 Tool Result 消息。"""
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(result),
        },
    ]


class ContextManagerTests(unittest.TestCase):
    def test_short_context_is_returned_unchanged(self) -> None:
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        manager = ContextManager(max_chars=1000)

        result = manager.build_context(messages)

        self.assertEqual(result, messages)
        self.assertIsNot(result, messages)
        self.assertEqual(manager.last_stats.input_chars, manager.last_stats.output_chars)

    def test_system_and_current_user_are_preserved_over_budget(self) -> None:
        messages = [
            {"role": "system", "content": "Important system instructions"},
            {"role": "user", "content": "Old task"},
            {"role": "assistant", "content": "x" * 5000},
            {"role": "user", "content": "Current task must remain"},
            {"role": "assistant", "content": "Recent answer"},
        ]
        manager = ContextManager(max_chars=500, recent_groups=1)

        result = manager.build_context(messages)

        self.assertIn(messages[0], result)
        self.assertIn(messages[3], result)
        self.assertNotIn(messages[1], result)

    def test_old_read_result_is_compacted_with_metadata(self) -> None:
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Old task"},
            *tool_interaction(
                "old-read",
                "read_file",
                {"path": "src/app.py"},
                {
                    "success": True,
                    "data": {
                        "path": "src/app.py",
                        "content": "source line\n" * 800,
                        "total_lines": 800,
                    },
                },
            ),
            {"role": "assistant", "content": "Old response"},
            {"role": "user", "content": "Current task"},
            {"role": "assistant", "content": "Recent response"},
        ]
        manager = ContextManager(max_chars=2500, recent_groups=2)

        result = manager.build_context(messages)

        old_tool = next(
            message for message in result if message.get("tool_call_id") == "old-read"
        )
        summary = json.loads(old_tool["content"])
        self.assertTrue(summary["compacted"])
        self.assertEqual(summary["tool"], "read_file")
        self.assertEqual(summary["path"], "src/app.py")
        self.assertTrue(summary["success"])
        self.assertLess(manager.last_stats.output_chars, manager.last_stats.input_chars)
        self.assertLessEqual(manager.last_stats.output_chars, manager.max_chars)

    def test_tool_protocol_groups_are_never_split(self) -> None:
        messages = [{"role": "system", "content": "System"}]
        for index in range(5):
            messages.append({"role": "user", "content": f"Task {index}"})
            messages.extend(
                tool_interaction(
                    f"call-{index}",
                    "read_file",
                    {"path": f"file-{index}.py"},
                    {
                        "success": True,
                        "data": {"content": str(index) * 2000},
                    },
                )
            )
        manager = ContextManager(max_chars=1200, recent_groups=1)

        result = manager.build_context(messages)

        assistant_ids = {
            call["id"]
            for message in result
            for call in message.get("tool_calls", [])
        }
        tool_ids = {
            message["tool_call_id"]
            for message in result
            if message.get("role") == "tool"
        }
        self.assertEqual(assistant_ids, tool_ids)
        self.assertLessEqual(manager.last_stats.output_chars, manager.max_chars)

    def test_recent_command_failure_remains_full(self) -> None:
        latest_error = "LATEST_ASSERTION_DETAILS" * 20
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Old task"},
            *tool_interaction(
                "old-read",
                "read_file",
                {"path": "large.py"},
                {"success": True, "data": {"content": "x" * 9000}},
            ),
            {"role": "user", "content": "Current task"},
            *tool_interaction(
                "latest-run",
                "run_command",
                {"command": ["python", "-m", "pytest"]},
                {
                    "success": True,
                    "data": {
                        "exit_code": 1,
                        "timed_out": False,
                        "stdout": latest_error,
                        "stderr": "",
                    },
                },
            ),
        ]
        manager = ContextManager(max_chars=2500, recent_groups=2)

        result = manager.build_context(messages)

        latest_tool = next(
            message
            for message in result
            if message.get("tool_call_id") == "latest-run"
        )
        self.assertIn(latest_error, latest_tool["content"])
        self.assertNotIn("Older tool result compacted", latest_tool["content"])

    def test_build_context_does_not_modify_full_history(self) -> None:
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Task"},
            *tool_interaction(
                "call-1",
                "read_file",
                {"path": "large.py"},
                {"success": True, "data": {"content": "x" * 5000}},
            ),
            {"role": "user", "content": "Current task"},
        ]
        original = deepcopy(messages)

        ContextManager(max_chars=800, recent_groups=1).build_context(messages)

        self.assertEqual(messages, original)

    def test_token_budget_handles_cjk_more_conservatively_than_char_budget(self) -> None:
        messages = [
            {"role": "system", "content": "系统"},
            {"role": "user", "content": "旧任务" * 300},
            {"role": "assistant", "content": "旧回答" * 300},
            {"role": "user", "content": "当前任务"},
        ]
        manager = ContextManager(max_chars=10_000, max_tokens=120, recent_groups=1)

        result = manager.build_context(messages)

        self.assertLess(len(result), len(messages))
        self.assertLessEqual(manager.last_stats.output_tokens, manager.max_tokens)
        self.assertGreater(manager.last_stats.input_tokens, manager.max_tokens)


if __name__ == "__main__":
    unittest.main()
