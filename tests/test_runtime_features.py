"""统一事件、消息队列和会话持久化的回归测试。"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from coding_agent.agent import Agent
from coding_agent.conversation import Conversation
from coding_agent.events import RuntimeEventKind
from coding_agent.llm import LLMResponse, ToolCall
from coding_agent.message_queue import AgentMessageQueue
from coding_agent.session import SessionStore
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
