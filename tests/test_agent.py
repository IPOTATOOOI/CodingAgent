"""自主 Agent Loop 与 Stage 6 Reliability 集成测试。"""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

from coding_agent.agent import Agent, MAX_TOOL_CALLS_PER_STEP
from coding_agent.context import ContextManager
from coding_agent.conversation import Conversation
from coding_agent.llm import LLMError, LLMResponse, ToolCall
from coding_agent.reliability import LLMRetryPolicy
from coding_agent.tools.registry import ToolRegistry, create_tool_registry


class AgentTests(unittest.TestCase):
    def test_cancellation_before_first_step_does_not_call_llm(self) -> None:
        client = Mock()
        conversation = Conversation("system")
        agent = Agent(
            llm_client=client,
            conversation=conversation,
            tool_registry=ToolRegistry(),
            should_cancel=lambda: True,
        )

        result = agent.run("task")

        self.assertEqual(result.stop_reason, "interrupted")
        self.assertEqual(result.steps, 0)
        client.complete.assert_not_called()

    def _create_agent(
        self,
        client: Mock,
        workspace: Path,
        max_steps: int = 12,
    ) -> tuple[Agent, Conversation]:
        conversation = Conversation("System prompt")
        agent = Agent(
            llm_client=client,
            conversation=conversation,
            tool_registry=create_tool_registry(workspace),
            max_steps=max_steps,
        )
        return agent, conversation

    def test_direct_final_answer_completes_in_one_step(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            client.complete.return_value = LLMResponse("Binary search is ...", [])
            agent, conversation = self._create_agent(client, Path(directory))

            result = agent.run("Explain binary search")

        self.assertEqual(result.content, "Binary search is ...")
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.steps, 1)
        self.assertEqual(
            [message["role"] for message in conversation.messages],
            ["system", "user", "assistant"],
        )

    def test_one_tool_then_final_answer_uses_two_steps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "README.md").write_text("Agent notes", encoding="utf-8")
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [ToolCall("call-1", "read_file", '{"path":"README.md"}')],
                ),
                LLMResponse("The README contains Agent notes.", []),
            ]
            agent, conversation = self._create_agent(client, workspace)

            result = agent.run("Summarize README")

        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.steps, 2)
        self.assertEqual(client.complete.call_count, 2)
        tool_result = json.loads(conversation.messages[3]["content"])
        self.assertIn("Agent notes", tool_result["data"]["content"])

    def test_read_edit_run_and_final_answer_preserve_message_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / "calculator.py"
            target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
            (workspace / "test_calculator.py").write_text(
                "from calculator import add\nassert add(2, 3) == 5\n",
                encoding="utf-8",
            )
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [ToolCall("call-1", "read_file", '{"path":"calculator.py"}')],
                ),
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            "call-2",
                            "edit_file",
                            json.dumps(
                                {
                                    "path": "calculator.py",
                                    "old_text": "return a - b",
                                    "new_text": "return a + b",
                                }
                            ),
                        )
                    ],
                ),
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            "call-3",
                            "run_command",
                            json.dumps(
                                {
                                    "command": [
                                        sys.executable,
                                        "test_calculator.py",
                                    ]
                                }
                            ),
                        )
                    ],
                ),
                LLMResponse("Fixed and verified.", []),
            ]
            agent, conversation = self._create_agent(client, workspace)

            result = agent.run("Fix add and verify")
            updated_content = target.read_text(encoding="utf-8")

        self.assertEqual(result.steps, 4)
        self.assertEqual(result.stop_reason, "completed")
        self.assertIn("return a + b", updated_content)
        self.assertEqual(
            [message["role"] for message in conversation.messages],
            [
                "system",
                "user",
                "assistant",
                "tool",
                "assistant",
                "tool",
                "assistant",
                "tool",
                "assistant",
            ],
        )

    def test_failed_command_observation_can_drive_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / "calculator.py"
            target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
            (workspace / "test_calculator.py").write_text(
                "from calculator import add\nassert add(2, 3) == 5\n",
                encoding="utf-8",
            )
            command = {
                "command": [
                    sys.executable,
                    "test_calculator.py",
                ]
            }
            verification_command = {
                "command": [
                    sys.executable,
                    "-m",
                    "py_compile",
                    "calculator.py",
                ]
            }
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [ToolCall("call-1", "run_command", json.dumps(command))],
                ),
                LLMResponse(
                    None,
                    [ToolCall("call-2", "read_file", '{"path":"calculator.py"}')],
                ),
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            "call-3",
                            "edit_file",
                            json.dumps(
                                {
                                    "path": "calculator.py",
                                    "old_text": "return a - b",
                                    "new_text": "return a + b",
                                }
                            ),
                        )
                    ],
                ),
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            "call-4",
                            "run_command",
                            json.dumps(verification_command),
                        )
                    ],
                ),
                LLMResponse("The failing implementation is repaired.", []),
            ]
            agent, conversation = self._create_agent(client, workspace)

            result = agent.run("Repair the failing implementation")

        command_results = [
            json.loads(message["content"])["data"]["exit_code"]
            for message in conversation.messages
            if message["role"] == "tool"
            and json.loads(message["content"]).get("data", {}).get("command")
        ]
        self.assertEqual(command_results, [1, 0])
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.steps, 5)

    def test_file_not_found_does_not_stop_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "right.py").write_text("answer = 42\n", encoding="utf-8")
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [ToolCall("call-1", "read_file", '{"path":"wrong.py"}')],
                ),
                LLMResponse(
                    None,
                    [ToolCall("call-2", "list_directory", '{"path":"."}')],
                ),
                LLMResponse(
                    None,
                    [ToolCall("call-3", "read_file", '{"path":"right.py"}')],
                ),
                LLMResponse("Found the value in right.py.", []),
            ]
            agent, conversation = self._create_agent(client, workspace)

            result = agent.run("Find the answer")

        first_result = json.loads(conversation.messages[3]["content"])
        self.assertEqual(first_result["error"], "FileNotFound")
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.steps, 4)

    def test_max_steps_stops_without_extra_llm_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "file.txt").write_text("text", encoding="utf-8")
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [ToolCall(f"call-{index}", "read_file", '{"path":"file.txt"}')],
                )
                for index in range(1, 4)
            ]
            agent, _ = self._create_agent(client, workspace, max_steps=3)

            result = agent.run("Keep inspecting")

        self.assertEqual(result.stop_reason, "max_steps")
        self.assertEqual(result.steps, 3)
        self.assertEqual(client.complete.call_count, 3)

    def test_invalid_empty_response_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            client.complete.return_value = LLMResponse(None, [])
            agent, _ = self._create_agent(client, Path(directory))

            result = agent.run("Hello")

        self.assertEqual(result.stop_reason, "invalid_response")
        self.assertEqual(result.steps, 1)

    def test_multiple_tool_calls_share_one_agent_step(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "a.py").write_text("a = 1\n", encoding="utf-8")
            (workspace / "b.py").write_text("b = 2\n", encoding="utf-8")
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [
                        ToolCall("call-1", "read_file", '{"path":"a.py"}'),
                        ToolCall("call-2", "read_file", '{"path":"b.py"}'),
                    ],
                ),
                LLMResponse("Both files were read.", []),
            ]
            agent, conversation = self._create_agent(client, workspace)

            result = agent.run("Read both files")

        self.assertEqual(result.steps, 2)
        self.assertEqual(
            [message["role"] for message in conversation.messages],
            ["system", "user", "assistant", "tool", "tool", "assistant"],
        )

    def test_all_multiple_tool_results_are_added_when_one_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "a.py").write_text("a = 1\n", encoding="utf-8")
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [
                        ToolCall("call-1", "read_file", '{"path":"a.py"}'),
                        ToolCall("call-2", "read_file", '{"path":"missing.py"}'),
                    ],
                ),
                LLMResponse("I handled the missing file.", []),
            ]
            agent, conversation = self._create_agent(client, workspace)

            result = agent.run("Inspect files")

        tool_results = [
            json.loads(message["content"])
            for message in conversation.messages
            if message["role"] == "tool"
        ]
        self.assertEqual([item["success"] for item in tool_results], [True, False])
        self.assertEqual(result.stop_reason, "completed")

    def test_too_many_tool_calls_execute_none(self) -> None:
        conversation = Conversation("System prompt")
        registry = Mock()
        registry.schemas = []
        client = Mock()
        client.complete.return_value = LLMResponse(
            None,
            [
                ToolCall(f"call-{index}", "read_file", '{"path":"a.py"}')
                for index in range(MAX_TOOL_CALLS_PER_STEP + 1)
            ],
        )
        agent = Agent(client, conversation, registry)

        result = agent.run("Read many files")

        self.assertEqual(result.stop_reason, "invalid_response")
        self.assertIn("ToolCallLimitExceeded", result.content)
        registry.execute.assert_not_called()
        self.assertEqual(
            [message["role"] for message in conversation.messages],
            ["system", "user"],
        )

    def test_tool_calls_take_priority_over_response_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "a.py").write_text("a = 1\n", encoding="utf-8")
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    "I will inspect first.",
                    [ToolCall("call-1", "read_file", '{"path":"a.py"}')],
                ),
                LLMResponse("Inspection complete.", []),
            ]
            agent, conversation = self._create_agent(client, workspace)

            result = agent.run("Inspect a.py")

        self.assertEqual(result.steps, 2)
        self.assertEqual(conversation.messages[2]["content"], "I will inspect first.")
        self.assertIn("tool_calls", conversation.messages[2])

    def test_llm_error_returns_controlled_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            client.complete.side_effect = LLMError("network error.")
            agent, _ = self._create_agent(client, Path(directory))

            result = agent.run("Hello")

        self.assertEqual(result.stop_reason, "llm_error")
        self.assertEqual(result.steps, 0)
        self.assertIn("network error", result.content)
        self.assertEqual(client.complete.call_count, 1)

    def test_keyboard_interrupt_returns_controlled_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            client.complete.side_effect = KeyboardInterrupt
            agent, _ = self._create_agent(client, Path(directory))

            result = agent.run("Hello")

        self.assertEqual(result.stop_reason, "interrupted")
        self.assertEqual(result.steps, 0)

    def test_max_steps_constructor_validation(self) -> None:
        conversation = Conversation("System prompt")
        registry = Mock()
        for invalid_value in (0, 51):
            with self.subTest(max_steps=invalid_value), self.assertRaises(ValueError):
                Agent(Mock(), conversation, registry, max_steps=invalid_value)

    def test_transient_llm_error_retries_without_adding_agent_step(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            client.complete.side_effect = [
                LLMError("temporary", transient=True),
                LLMResponse("Recovered", []),
            ]
            conversation = Conversation("System prompt")
            retry_callback = Mock()
            agent = Agent(
                client,
                conversation,
                create_tool_registry(Path(directory)),
                retry_policy=LLMRetryPolicy(delays=(0, 0), sleeper=Mock()),
                on_llm_retry=retry_callback,
            )

            result = agent.run("Hello")

        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.steps, 1)
        self.assertEqual(client.complete.call_count, 2)
        retry_callback.assert_called_once_with(1, 2)
        self.assertEqual(result.llm_retries, 1)

    def test_transient_llm_errors_stop_after_retry_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            client.complete.side_effect = LLMError("temporary", transient=True)
            agent = Agent(
                client,
                Conversation("System prompt"),
                create_tool_registry(Path(directory)),
                retry_policy=LLMRetryPolicy(delays=(0, 0), sleeper=Mock()),
            )

            result = agent.run("Hello")

        self.assertEqual(result.stop_reason, "llm_error")
        self.assertEqual(result.steps, 0)
        self.assertEqual(client.complete.call_count, 3)

    def test_repeated_action_observation_allows_strategy_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "a.py").write_text("target = 1\n", encoding="utf-8")
            repeated_calls = [
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            f"call-{index}",
                            "read_file",
                            '{"path":"a.py"}',
                        )
                    ],
                )
                for index in range(1, 4)
            ]
            client = Mock()
            client.complete.side_effect = [
                *repeated_calls,
                LLMResponse(
                    None,
                    [ToolCall("call-4", "search_text", '{"query":"target"}')],
                ),
                LLMResponse("Changed strategy successfully.", []),
            ]
            agent, conversation = self._create_agent(client, workspace)

            result = agent.run("Find target")

        tool_results = [
            json.loads(message["content"])
            for message in conversation.messages
            if message["role"] == "tool"
        ]
        self.assertEqual(tool_results[2]["error"], "RepeatedAction")
        self.assertTrue(tool_results[3]["success"])
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.steps, 5)

    def test_persistent_repeated_action_stops_with_no_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "a.py").write_text("value = 1\n", encoding="utf-8")
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            f"call-{index}",
                            "read_file",
                            '{"path":"a.py"}',
                        )
                    ],
                )
                for index in range(1, 13)
            ]
            agent, _ = self._create_agent(client, workspace)

            result = agent.run("Keep reading")

        self.assertEqual(result.stop_reason, "no_progress")
        self.assertEqual(result.steps, 5)
        self.assertEqual(client.complete.call_count, 5)

    def test_blocked_install_can_recover_with_existing_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            "call-1",
                            "run_command",
                            '{"command":["pip","install","pytest"]}',
                        )
                    ],
                ),
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            "call-2",
                            "run_command",
                            json.dumps(
                                {"command": [sys.executable, "-c", "print('ok')"]}
                            ),
                        )
                    ],
                ),
                LLMResponse("Used the existing environment.", []),
            ]
            agent, conversation = self._create_agent(client, workspace)

            result = agent.run("Run checks")

        results = [
            json.loads(message["content"])
            for message in conversation.messages
            if message["role"] == "tool"
        ]
        self.assertEqual(results[0]["error"], "CommandBlocked")
        self.assertEqual(results[1]["data"]["exit_code"], 0)
        self.assertEqual(result.stop_reason, "completed")

    def test_llm_receives_compact_view_while_conversation_remains_full(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conversation = Conversation("System prompt")
            for index in range(4):
                conversation.add_user_message(f"Old task {index}")
                conversation.add_assistant_tool_calls(
                    None,
                    [
                        ToolCall(
                            f"old-{index}",
                            "read_file",
                            json.dumps({"path": f"file-{index}.py"}),
                        )
                    ],
                )
                conversation.add_tool_result(
                    f"old-{index}",
                    json.dumps(
                        {
                            "success": True,
                            "data": {"content": "x" * 4000},
                        }
                    ),
                )
            full_before = conversation.messages
            client = Mock()
            client.complete.return_value = LLMResponse("Done", [])
            context_manager = ContextManager(max_chars=1800, recent_groups=1)
            agent = Agent(
                client,
                conversation,
                create_tool_registry(Path(directory)),
                context_manager=context_manager,
            )

            result = agent.run("Current task")

        llm_messages = client.complete.call_args.args[0]
        self.assertEqual(result.stop_reason, "completed")
        self.assertLess(len(llm_messages), len(conversation.messages))
        self.assertEqual(conversation.messages[:-2], full_before)
        self.assertLess(
            context_manager.last_stats.output_chars,
            context_manager.last_stats.input_chars,
        )

    def test_multi_repair_with_repeated_command_remains_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "state.txt").write_text("first=bad\nsecond=bad\n", encoding="utf-8")
            check_command = {
                "command": [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; "
                        "value=Path('state.txt').read_text(); "
                        "raise SystemExit(0 if 'first=good' in value and "
                        "'second=good' in value else 1)"
                    ),
                ]
            }
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [ToolCall("run-1", "run_command", json.dumps(check_command))],
                ),
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            "edit-1",
                            "edit_file",
                            json.dumps(
                                {
                                    "path": "state.txt",
                                    "old_text": "first=bad",
                                    "new_text": "first=good",
                                }
                            ),
                        )
                    ],
                ),
                LLMResponse(
                    None,
                    [ToolCall("run-2", "run_command", json.dumps(check_command))],
                ),
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            "edit-2",
                            "edit_file",
                            json.dumps(
                                {
                                    "path": "state.txt",
                                    "old_text": "second=bad",
                                    "new_text": "second=good",
                                }
                            ),
                        )
                    ],
                ),
                LLMResponse(
                    None,
                    [ToolCall("run-3", "run_command", json.dumps(check_command))],
                ),
                LLMResponse("Both repairs passed.", []),
            ]
            agent, conversation = self._create_agent(client, workspace)

            result = agent.run("Repair both failures")

        command_results = [
            json.loads(message["content"])["data"]["exit_code"]
            for message in conversation.messages
            if message["role"] == "tool"
            and json.loads(message["content"]).get("data", {}).get("command")
        ]
        self.assertEqual(command_results, [1, 1, 0])
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.steps, 6)


if __name__ == "__main__":
    unittest.main()
