"""包含工具协议消息的本地对话历史测试。"""

import unittest

from coding_agent.conversation import Conversation
from coding_agent.llm import ToolCall


class ConversationTests(unittest.TestCase):
    def test_initial_history_contains_system_message(self) -> None:
        conversation = Conversation("System prompt")

        self.assertEqual(
            conversation.messages,
            [{"role": "system", "content": "System prompt"}],
        )

    def test_add_user_and_assistant_messages_in_order(self) -> None:
        conversation = Conversation("System prompt")
        conversation.add_user_message("hello")
        conversation.add_assistant_message("Hello")

        self.assertEqual(
            [message["role"] for message in conversation.messages],
            ["system", "user", "assistant"],
        )

    def test_runtime_system_message_can_be_appended(self) -> None:
        conversation = Conversation("Initial system")

        conversation.add_system_message("Runtime reminder")

        self.assertEqual(
            conversation.messages,
            [
                {"role": "system", "content": "Initial system"},
                {"role": "system", "content": "Runtime reminder"},
            ],
        )

    def test_add_tool_call_and_matching_tool_result(self) -> None:
        conversation = Conversation("System prompt")
        conversation.add_user_message("Read README")
        conversation.add_assistant_tool_calls(
            None,
            [ToolCall("call-1", "read_file", '{"path":"README.md"}')],
        )
        conversation.add_tool_result("call-1", '{"success":true}')

        messages = conversation.messages
        self.assertEqual(
            [message["role"] for message in messages],
            ["system", "user", "assistant", "tool"],
        )
        self.assertEqual(messages[2]["tool_calls"][0]["id"], "call-1")
        self.assertEqual(messages[3]["tool_call_id"], "call-1")

    def test_messages_deep_copy_protects_nested_history(self) -> None:
        conversation = Conversation("System prompt")
        conversation.add_assistant_tool_calls(
            None,
            [ToolCall("call-1", "read_file", '{"path":"README.md"}')],
        )
        messages = conversation.messages
        messages[1]["tool_calls"][0]["function"]["name"] = "changed"

        self.assertEqual(
            conversation.messages[1]["tool_calls"][0]["function"]["name"],
            "read_file",
        )


if __name__ == "__main__":
    unittest.main()
