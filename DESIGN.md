# Design Notes

## Stage 0

### Goal

Establish the project structure and CLI entry point without implementing agent functionality.

### Design Decisions

1. Python is used for rapid development and straightforward local process/file control.
2. A `src` layout is used to separate package code from project-level files.
3. Secrets will be provided through environment variables and never committed.
4. Agent functionality will be implemented incrementally rather than introduced in the initial commit.

### Current Architecture

```text
User
  ->
CLI
  ->
Placeholder response
```

### Not Implemented Yet

- LLM interaction
- Tool calling
- File tools
- Command execution
- Agent loop
- Context management

## Stage 1

### Goal

Connect the CLI to a real language model and maintain explicit multi-turn conversation history.

### Architecture

```text
User
  ->
CLI
  ->
Conversation
  ->
LLM Client
  ->
LLM API
  ->
Text Response
  ->
Conversation
  ->
CLI
```

### Design Decisions

1. Conversation history is maintained locally rather than delegated to a hosted agent service.
2. The LLM layer is isolated from CLI interaction.
3. OpenAI-compatible APIs are used to keep the model backend replaceable.
4. No tool calling is introduced in Stage 1.
5. API credentials remain external to the repository.

### Current Limitation

The model can reason about text supplied by the user, but it cannot inspect the local project.

For example, asking:

> Please find the bug in calculator.py

cannot be completed unless the user manually pastes `calculator.py` into the conversation.
This limitation will motivate Stage 2.
