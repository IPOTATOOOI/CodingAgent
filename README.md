# Mini Coding Agent

A lightweight coding agent implemented from scratch.

## Current Status

Stage 5: Autonomous agent loop.

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
- A general observation-action loop driven by model decisions
- Configurable per-task step limits
- Recovery from structured tool errors and unsuccessful command exits
- At most eight sequential tool calls in one model response

It does not yet support:

- Context compaction
- Repeated-action and no-progress detection
- Automatic retries for transient LLM failures
- Mandatory post-edit verification
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
python -m coding_agent --workspace . --max-steps 12
```

or:

```bash
coding-agent --workspace . --max-steps 12
```

`--max-steps` limits the number of LLM responses in one user task. Its default is
12 and its accepted range is 1 through 50. Reaching the limit stops immediately
without making an additional LLM request.

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

## Agent Loop

One LLM response is one Agent Step. A response may request one or more tools; all
calls in that response execute sequentially and still count as one step. Their
structured results are appended to the conversation before the next model decision.

```text
User task
   ↓
LLM decision  ─────────────→ final text → completed
   ↓ tool calls
Local runtime
   ↓ observations
Conversation
   └───────────────────────→ next LLM decision
```

Tool failures do not automatically stop the loop. For example, `FileNotFound` can
lead the model to list the directory and retry with the correct path. Likewise,
`pytest` returning exit code 1 is a successful command observation, so the model can
inspect the failure, edit code, and run the tests again.

Example session:

```text
Mini Coding Agent
Stage 5 - Autonomous agent loop

Workspace: /path/to/project
Max steps per task: 12

Type your message.
Type /exit to quit.

> Inspect calculator.py, fix the implementation bug, and run the tests.
[step 1] [tool] run_command(command=['python', '-m', 'unittest', '-v'], cwd='.')
[result] exit_code=1, stdout=0 chars, stderr=624 chars
[step 2] [tool] read_file(path='calculator.py')
[result] lines=1-2, total=2
[step 3] [tool] edit_file(path='calculator.py')
[result] modified=calculator.py
[step 4] [tool] run_command(command=['python', '-m', 'unittest', '-v'], cwd='.')
[result] exit_code=0, stdout=20 chars, stderr=0 chars
Assistant:
<assistant response grounded in the real command result>
> /exit
Exiting Mini Coding Agent.
```

You can try this flow without touching the agent's own source tree:

```bash
python -m coding_agent --workspace examples/demo_project --max-steps 12
```

Stage 5 can use a failed execution result to choose further inspection, editing, and
execution actions until the model returns final text or a Runtime bound stops the
task. It does not hard-code a `run -> read -> edit -> run` workflow; the model selects
the next tool from the accumulated conversation.

Possible `AgentResult.stop_reason` values are `completed`, `max_steps`,
`interrupted`, `invalid_response`, and `llm_error`. A response containing more than
eight tool calls is treated as an invalid response and none of those calls execute.

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
