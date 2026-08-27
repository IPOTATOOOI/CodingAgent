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
