"""统一事件、消息队列和会话持久化的回归测试。"""

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from coding_agent.agent import Agent
from coding_agent.conversation import Conversation
from coding_agent.events import RuntimeEventKind
from coding_agent.llm import LLMResponse, ToolCall
from coding_agent.message_queue import AgentMessageQueue
from coding_agent.reliability import ReliabilityTracker
from coding_agent.session import SessionStore, SessionTooLargeError
from coding_agent.tools.registry import create_tool_registry


class RuntimeFeatureTests(unittest.TestCase):
    def test_runtime_events_cover_task_step_tool_and_finish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [ToolCall("read-1", "list_directory", '{"path":"."}')],
                ),
                LLMResponse("Done", []),
            ]
            events = []
            agent = Agent(
                client,
                Conversation("System"),
                create_tool_registry(Path(directory)),
                on_event=events.append,
            )

            result = agent.run("Inspect")

        kinds = [event.kind for event in events]
        self.assertEqual(kinds[0], RuntimeEventKind.TASK_STARTED)
        self.assertIn(RuntimeEventKind.CONTEXT_BUILT, kinds)
        self.assertIn(RuntimeEventKind.TOOL_STARTED, kinds)
        self.assertIn(RuntimeEventKind.TOOL_FINISHED, kinds)
        self.assertEqual(kinds[-1], RuntimeEventKind.TASK_FINISHED)
        self.assertEqual(events[-1].payload["stop_reason"], "completed")
        self.assertEqual(result.stop_reason, "completed")

    def test_many_inspections_emit_a_progress_warning_for_the_next_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "a.py").write_text("a = 1\n", encoding="utf-8")
            (workspace / "b.py").write_text("b = 2\n", encoding="utf-8")
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [ToolCall("read-1", "read_file", '{"path":"a.py"}')],
                ),
                LLMResponse(
                    None,
                    [ToolCall("read-2", "read_file", '{"path":"b.py"}')],
                ),
                LLMResponse("Done", []),
            ]
            events = []
            conversation = Conversation("System")
            agent = Agent(
                client,
                conversation,
                create_tool_registry(workspace),
                reliability_tracker=ReliabilityTracker(
                    inspection_reminder_interval=2
                ),
                on_event=events.append,
            )

            result = agent.run("Inspect")

        warnings = [
            event
            for event in events
            if event.kind == RuntimeEventKind.PROGRESS_WARNING
        ]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].payload["inspection_calls"], 2)
        self.assertTrue(
            any(
                message["role"] == "system"
                and "concrete next action" in message["content"]
                for message in conversation.messages
            )
        )
        self.assertEqual(result.stop_reason, "completed")

    def test_steering_added_during_request_is_applied_before_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = AgentMessageQueue()
            received_contexts = []

            class SteeringClient:
                def complete(self, messages, tools=None):
                    del tools
                    received_contexts.append(messages)
                    if len(received_contexts) == 1:
                        queue.add_steering("Also inspect tests")
                        return LLMResponse("Initial answer", [])
                    return LLMResponse("Updated answer", [])

            conversation = Conversation("System")
            agent = Agent(
                SteeringClient(),
                conversation,
                create_tool_registry(Path(directory)),
                message_queue=queue,
            )

            result = agent.run("Inspect source")

        self.assertEqual(result.content, "Updated answer")
        self.assertEqual(result.steps, 2)
        self.assertIn(
            "Also inspect tests",
            [message.get("content") for message in received_contexts[1]],
        )

    def test_message_queue_keeps_steering_and_followups_separate(self) -> None:
        queue = AgentMessageQueue()
        queue.add_steering("first")
        queue.add_steering("second")
        queue.add_follow_up("later")

        self.assertEqual(queue.drain_steering(), ["first", "second"])
        self.assertEqual(queue.pop_follow_up(), "later")
        self.assertIsNone(queue.pop_follow_up())

    def test_session_round_trip_is_workspace_scoped_and_deletable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            conversation = Conversation("System")
            conversation.add_user_message("Fix tests")
            conversation.add_assistant_message("Done")
            store = SessionStore(root / "sessions")

            path = store.save(conversation, workspace, "test-model")
            snapshot = store.load(workspace)

            self.assertTrue(path.exists())
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot.model, "test-model")
            self.assertEqual(snapshot.messages, conversation.messages)
            self.assertNotIn("api", json.loads(path.read_text(encoding="utf-8")))
            self.assertTrue(store.delete(workspace))
            self.assertIsNone(store.load(workspace))

    def test_session_compacts_old_tool_results_and_stays_within_hard_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            conversation = Conversation("System")
            for index in range(6):
                conversation.add_user_message(f"Task {index}")
                conversation.add_assistant_tool_calls(
                    None,
                    [
                        ToolCall(
                            f"call-{index}",
                            "read_file",
                            json.dumps({"path": f"file-{index}.py"}),
                        )
                    ],
                )
                conversation.add_tool_result(
                    f"call-{index}",
                    json.dumps(
                        {"success": True, "data": {"content": "x" * 5000}}
                    ),
                )
            conversation.add_user_message("Current task")
            store = SessionStore(
                root / "sessions",
                max_bytes=8_000,
                recent_groups=1,
            )

            path = store.save(conversation, workspace, "test-model")
            snapshot = store.load(workspace)

            self.assertLessEqual(path.stat().st_size, store.max_bytes)
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertTrue(snapshot.compacted)
            assistant_ids = {
                call["id"]
                for message in snapshot.messages
                for call in message.get("tool_calls", [])
            }
            tool_ids = {
                message["tool_call_id"]
                for message in snapshot.messages
                if message.get("role") == "tool"
            }
            self.assertEqual(assistant_ids, tool_ids)

    def test_session_rejects_oversized_protected_user_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            conversation = Conversation("System")
            conversation.add_user_message("中" * 10_000)
            store = SessionStore(root / "sessions", max_bytes=5_000)

            with self.assertRaises(SessionTooLargeError):
                store.save(conversation, workspace, "test-model")

            self.assertFalse(store.path_for(workspace).exists())

    def test_session_cleanup_enforces_age_count_and_clear_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            root.mkdir()
            now = time.time()
            paths = [root / f"session-{index}.json" for index in range(4)]
            for index, path in enumerate(paths):
                path.write_text("{}", encoding="utf-8")
                modified = now - (2 * 24 * 60 * 60 if index == 0 else index)
                path.touch()
                os.utime(path, (modified, modified))
            stale_temporary = root / ".session-stale.tmp"
            stale_temporary.write_text("partial", encoding="utf-8")
            old_time = now - 2 * 24 * 60 * 60
            os.utime(stale_temporary, (old_time, old_time))
            store = SessionStore(root, retention_days=1, max_sessions=2)

            removed = store.cleanup(now=now)

            self.assertEqual(removed, 3)
            self.assertEqual(len(list(root.glob("*.json"))), 2)
            self.assertEqual(store.clear_all(), 2)
            self.assertEqual(list(root.glob("*.json")), [])

    def test_event_observer_failure_does_not_break_agent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            client.complete.return_value = LLMResponse("Done", [])

            agent = Agent(
                client,
                Conversation("System"),
                create_tool_registry(Path(directory)),
                on_event=lambda event: (_ for _ in ()).throw(RuntimeError("observer")),
            )
            result = agent.run("Task")

        self.assertEqual(result.stop_reason, "completed")


if __name__ == "__main__":
    unittest.main()
