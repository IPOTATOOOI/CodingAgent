"""本地对话历史测试。"""

import unittest

from coding_agent.conversation import Conversation


class ConversationTests(unittest.TestCase):
    def test_initial_history_contains_system_message(self) -> None:
        conversation = Conversation("System prompt")

        self.assertEqual(
            conversation.messages,
            [{"role": "system", "content": "System prompt"}],
        )

    def test_add_user_message(self) -> None:
        conversation = Conversation("System prompt")
        conversation.add_user_message("hello")

        self.assertEqual(
            conversation.messages[-1], {"role": "user", "content": "hello"}
        )

    def test_user_and_assistant_order(self) -> None:
        conversation = Conversation("System prompt")
        conversation.add_user_message("hello")
        conversation.add_assistant_message("Hello")

        self.assertEqual(
            [message["role"] for message in conversation.messages],
            ["system", "user", "assistant"],
        )

    def test_messages_cannot_mutate_internal_history(self) -> None:
        conversation = Conversation("System prompt")
        messages = conversation.messages
        messages[0]["content"] = "Changed"
        messages.append({"role": "user", "content": "Injected"})

        self.assertEqual(
            conversation.messages,
            [{"role": "system", "content": "System prompt"}],
        )


if __name__ == "__main__":
    unittest.main()
