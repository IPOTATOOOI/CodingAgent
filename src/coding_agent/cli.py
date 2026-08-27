"""第一阶段纯文本对话的命令行界面。"""

from coding_agent.config import ConfigurationError, Settings
from coding_agent.conversation import Conversation
from coding_agent.llm import LLMClient, LLMError


SYSTEM_PROMPT = """You are Mini Coding Agent, a helpful programming assistant.
Answer programming questions clearly and accurately.
You cannot access local files or execute commands; ask the user to provide content."""


def run_cli(client: LLMClient | None = None) -> None:
    """运行多轮纯文本 LLM 对话。"""
    print("Mini Coding Agent")
    print("Stage 1 - LLM conversation")
    print()
    print("Type your message.")
    print("Type /exit to quit.")
    print()

    if client is None:
        try:
            client = LLMClient(Settings.from_env())
        except ConfigurationError as error:
            print(f"Configuration error: {error}")
            return

    conversation = Conversation(SYSTEM_PROMPT)

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print("Exiting Mini Coding Agent.")
            return

        if user_input in {"/exit", "/quit"}:
            print("Exiting Mini Coding Agent.")
            return
        if not user_input:
            continue

        conversation.add_user_message(user_input)
        try:
            response = client.complete(conversation.messages)
        except LLMError as error:
            print(f"LLM request failed: {error}")
            continue

        conversation.add_assistant_message(response)
        print(response)


def main() -> None:
    """启动命令行界面。"""
    run_cli()
