"""重复动作、无进展判断和 LLM 重试策略测试。"""

import unittest
from unittest.mock import Mock

from coding_agent.llm import LLMError, ToolCall
from coding_agent.reliability import LLMRetryPolicy, ReliabilityTracker


class ReliabilityTrackerTests(unittest.TestCase):
    def test_action_signature_canonicalizes_json_key_order(self) -> None:
        first = ToolCall(
            "call-1",
            "read_file",
            '{"path":"a.py","start_line":1}',
        )
        second = ToolCall(
            "call-2",
            "read_file",
            '{"start_line":1,"path":"a.py"}',
        )

        self.assertEqual(
            ReliabilityTracker.action_signature(first),
            ReliabilityTracker.action_signature(second),
        )

    def test_first_two_identical_actions_are_allowed_and_third_is_blocked(self) -> None:
        tracker = ReliabilityTracker()
        calls = [
            ToolCall(f"call-{index}", "read_file", '{"path":"a.py"}')
            for index in range(3)
        ]

        blocked = [tracker.is_repeated_action(call) for call in calls]

        self.assertEqual(blocked, [False, False, True])

    def test_intervening_different_action_breaks_consecutive_repeat(self) -> None:
        tracker = ReliabilityTracker()
        read = ToolCall("read", "read_file", '{"path":"a.py"}')
        edit = ToolCall(
            "edit",
            "edit_file",
            '{"path":"a.py","old_text":"x","new_text":"y"}',
        )

        blocked = [
            tracker.is_repeated_action(read),
            tracker.is_repeated_action(read),
            tracker.is_repeated_action(edit),
            tracker.is_repeated_action(read),
        ]

        self.assertEqual(blocked, [False, False, False, False])

    def test_identical_observations_accumulate_no_progress(self) -> None:
        tracker = ReliabilityTracker(no_progress_limit=2)
        call = ToolCall("call", "run_command", '{"command":["pytest"]}')
        result = {
            "success": True,
            "data": {
                "exit_code": 1,
                "timed_out": False,
                "stdout": "same failure",
                "stderr": "",
            },
        }

        tracker.start_step()
        tracker.record_tool_result(call, result)
        self.assertFalse(tracker.finish_step())
        tracker.start_step()
        tracker.record_tool_result(call, result)
        self.assertFalse(tracker.finish_step())
        tracker.start_step()
        tracker.record_tool_result(call, result)

        self.assertTrue(tracker.finish_step())

    def test_successful_edit_resets_no_progress(self) -> None:
        tracker = ReliabilityTracker(no_progress_limit=3)
        read = ToolCall("read", "read_file", '{"path":"a.py"}')
        read_result = {"success": True, "data": {"content": "same"}}
        edit = ToolCall("edit", "edit_file", '{"path":"a.py"}')
        edit_result = {"success": True, "data": {"modified": True}}

        for _ in range(2):
            tracker.start_step()
            tracker.record_tool_result(read, read_result)
            tracker.finish_step()
        self.assertEqual(tracker.consecutive_no_progress_steps, 1)

        tracker.start_step()
        tracker.record_tool_result(edit, edit_result)
        tracker.finish_step()

        self.assertEqual(tracker.consecutive_no_progress_steps, 0)

    def test_changed_command_output_is_progress(self) -> None:
        tracker = ReliabilityTracker(no_progress_limit=2)
        call = ToolCall("call", "run_command", '{"command":["pytest"]}')

        for stderr in ("first failure", "different failure"):
            tracker.start_step()
            tracker.record_tool_result(
                call,
                {
                    "success": True,
                    "data": {
                        "exit_code": 1,
                        "timed_out": False,
                        "stdout": "",
                        "stderr": stderr,
                    },
                },
            )
            self.assertFalse(tracker.finish_step())

        self.assertEqual(tracker.consecutive_no_progress_steps, 0)


class LLMRetryPolicyTests(unittest.TestCase):
    def test_transient_error_is_retried_until_success(self) -> None:
        request = Mock(side_effect=[LLMError("temporary", transient=True), "ok"])
        sleeper = Mock()
        callback = Mock()
        policy = LLMRetryPolicy(delays=(0.5, 1.0), sleeper=sleeper)

        result = policy.execute(request, callback)

        self.assertEqual(result, "ok")
        self.assertEqual(request.call_count, 2)
        sleeper.assert_called_once_with(0.5)
        callback.assert_called_once_with(1, 2)

    def test_retry_limit_allows_three_total_attempts(self) -> None:
        request = Mock(side_effect=LLMError("temporary", transient=True))
        policy = LLMRetryPolicy(delays=(0, 0), sleeper=Mock())

        with self.assertRaises(LLMError):
            policy.execute(request)

        self.assertEqual(request.call_count, 3)

    def test_permanent_error_is_not_retried(self) -> None:
        request = Mock(side_effect=LLMError("authentication error."))
        policy = LLMRetryPolicy(delays=(0, 0), sleeper=Mock())

        with self.assertRaises(LLMError):
            policy.execute(request)

        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
