# Mini Coding Agent

A lightweight coding agent implemented from scratch.

## Current Status

Stage 4: Local command execution.

The project currently supports:

- CLI interaction
- OpenAI-compatible LLM APIs
- Multi-turn text conversation
- Environment-based model configuration
- Native LLM tool calling
- Workspace-scoped directory listing
- Workspace-scoped UTF-8 text file reading
- Literal text search
- Create-only UTF-8 text file writing
- Exact, unique text replacement in existing files
- Shell-free non-interactive local command execution
- Separate stdout, stderr, exit-code, and timeout feedback
- Bounded command output returned to the model
- Structured local tool execution
- At most three explicit tool-call rounds per user turn

It does not yet support:

- Unrestricted autonomous repair loops
- Context compaction
- Persistent or interactive shell sessions
- Background processes
- OS-level execution sandboxing

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

The workspace defines the root directory that the file tools may inspect or modify.
Canonical paths outside this root are rejected. This is basic workspace-boundary
enforcement, not a complete security sandbox. Secret-bearing `.env` variants are
excluded from file reading and text search; `.env.example` remains inspectable.

`write_file` creates new files only and refuses to overwrite existing paths.
`edit_file` changes an existing UTF-8 text file only when `old_text` occurs exactly
once. Neither tool creates missing directories. Files larger than 1 MiB are rejected.

`run_command` accepts an executable and arguments as a string array, uses
`shell=False`, disables interactive input, and restricts its working directory to the
workspace. Commands have a 30-second default timeout, a 120-second maximum timeout,
and separate 12000-character stdout/stderr context limits. The Agent's
`LLM_API_KEY` is removed from the child process environment.

This is shell-free local command execution with workspace-scoped working directories
and execution limits. It is not an OS-level sandbox: child processes still run with
the permissions of the current operating-system user.

Example session:

```text
Mini Coding Agent
Stage 4 - Local command execution

Workspace: /path/to/project

Type your message.
Type /exit to quit.

> Inspect calculator.py, fix the implementation bug, and run the tests.
[tool] read_file(path='calculator.py')
[tool] edit_file(path='calculator.py')
[tool] run_command(command=['python', '-m', 'pytest', '-q'], cwd='.')
[result] exit_code=0, stdout=20 chars, stderr=0 chars
Assistant:
<assistant response grounded in the real command result>
> /exit
Exiting Mini Coding Agent.
```

You can try this flow without touching the agent's own source tree:

```bash
python -m coding_agent --workspace examples/demo_project
```

Stage 4 can inspect, modify, and execute the demo project. A non-zero process exit
code remains a successful tool invocation: it means the command ran and the target
program reported failure. After three tool rounds the Agent reports any remaining
problem instead of starting an unrestricted repair loop.

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
