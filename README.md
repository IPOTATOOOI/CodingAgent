# Mini Coding Agent

A lightweight coding agent implemented from scratch.

## Current Status

Stage 2: Read-only local tools.

The project currently supports:

- CLI interaction
- OpenAI-compatible LLM APIs
- Multi-turn text conversation
- Environment-based model configuration
- Native LLM tool calling
- Workspace-scoped directory listing
- Workspace-scoped UTF-8 text file reading
- Literal text search
- Structured local tool execution

It does not yet support:

- File modification
- Command execution
- Autonomous multi-step agent loops
- Context compaction

## Requirements

Python >= 3.10

## Installation

```bash
pip install -e .
```

## Configuration

Set the following system environment variables before starting the CLI:

- `LLM_API_KEY` (required)
- `LLM_MODEL` (required)
- `LLM_BASE_URL` (optional, for a compatible provider)

PowerShell example using placeholder values:

```powershell
$env:LLM_API_KEY="your-api-key"
$env:LLM_MODEL="your-model"
$env:LLM_BASE_URL="https://your-provider.example/v1"
```

The CLI loads values from a local `.env` file and then reads the environment
variables. Existing system environment variables take precedence over `.env`.
The `.env` file is ignored by Git and must never be committed.

## Run

```bash
python -m coding_agent --workspace .
```

or:

```bash
coding-agent --workspace .
```

The workspace defines the root directory that the read-only tools may inspect.
Canonical paths outside this root are rejected. This is basic workspace-boundary
enforcement, not a complete security sandbox. Secret-bearing `.env` variants are
excluded from file reading and text search; `.env.example` remains inspectable.

Example session:

```text
Mini Coding Agent
Stage 2 - Read-only tools

Workspace: /path/to/project

Type your message.
Type /exit to quit.

> Read README.md and summarize it.
[tool] read_file(path='README.md')
Assistant:
<assistant response based on the file content>
> /exit
Exiting Mini Coding Agent.
```

## Development Stages

- Stage 0: Project initialization
- Stage 1: Basic LLM client
- Stage 2: Read-only tools
- Stage 3: File editing
- Stage 4: Command execution
- Stage 5: Autonomous agent loop
- Stage 6: Context and reliability
- Stage 7: Verification and evaluation
- Stage 8: Final documentation and demo
