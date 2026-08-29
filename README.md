# Mini Coding Agent

A lightweight coding agent implemented from scratch.

## Current Status

Stage 7: Verification and evaluation.

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
- Bounded LLM-visible context with deterministic old-result compaction
- Protocol-safe grouping of Assistant Tool Calls and Tool Results
- Consecutive repeated-action detection
- Deterministic no-progress termination
- Bounded retries for transient LLM failures
- Local command policy for package/environment mutation and direct deletion
- Execution-grounded completion verification for supported code/config changes
- Mutation generation tracking and bounded runtime verification reminders
- A six-task evaluation runner with Agent-invisible independent verifiers

It does not yet support:

- Semantic long-term memory, RAG, or vector storage
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

Before process creation, a local command policy blocks selected package/environment
mutation commands, including `pip install`, `python -m pip install`, `conda install`,
`npm install`, `yarn add`, and equivalent uninstall/remove forms. Direct executables
such as `rm`, `rmdir`, `del`, `erase`, and `Remove-Item` are also blocked. Normal
development commands such as `python -m pytest`, `python -m unittest`, `npm test`,
and `npm run build` remain allowed.

This is shell-free local command execution with workspace-scoped working directories
and execution limits. It is not an OS-level sandbox: child processes still run with
the permissions of the current operating-system user.

## Reliability Safeguards

The autonomous loop uses several deterministic Runtime safeguards:

1. `max_steps` remains the final hard upper bound.
2. The third consecutive identical Tool Action is not executed; the model receives
   a structured `RepeatedAction` result and can change strategy.
3. Four consecutive Agent Steps without a new Observation or successful Workspace
   mutation terminate the task with `no_progress`.
4. Before every LLM request, `ContextManager` creates a bounded view of the complete
   Conversation. The default character budget is 60000 and the recent-group target
   is 12.
5. Older large Tool Results are compacted deterministically. System instructions,
   the current User Task, and recent actions are prioritized.
6. Rate limits, connection failures, timeouts, and server 5xx responses may be
   retried twice using 0.5- and 1-second delays. Authentication and model-not-found
   errors are not retried.
7. Selected environment-mutating and directly destructive commands are rejected
   before subprocess creation with `CommandBlocked`.

The full Conversation remains available to the local Runtime and debugging code;
only the LLM-visible request view is compacted. Assistant Tool Call messages and all
following Tool Results are retained, compacted, or removed as one group so native
Tool Calling protocol relationships are not split.

These are basic local Runtime safeguards, not a complete security sandbox. Child
processes that are allowed still execute with the permissions of the current user.

## Verification Gate

Successful `write_file` or `edit_file` execution does not by itself prove that new
code works. After supported source-code or project-configuration files are changed,
the Runtime marks the latest mutation generation as `unverified`. A later recognized
test, build, or syntax-check command must finish without a timeout and with exit code
0 before the Runtime accepts a final answer as `completed`.

Recognized commands include Python pytest, unittest, compileall and py_compile forms;
direct `test_*.py` and `*_test.py` scripts; and common npm, yarn, pnpm, Go, Cargo,
Maven and Gradle test/build commands. A generic successful command such as
`python script.py` or `python -c ...` is not automatically verification evidence.

If the model tries to finish while the latest generation is unverified or failed,
the Runtime rejects that completion attempt and adds a short system reminder. At
most two reminders are added. Continued unsupported completion attempts stop with
`verification_required`, while `max_steps` remains the final step boundary. A new
supported mutation after a successful check creates a new generation and requires
new evidence. Documentation-only `.md`, `.txt`, and `.rst` changes do not require
this code verification gate.

This mechanism requires execution-based evidence for supported changes; it does not
guarantee correctness. A syntax check is weaker than a behavioral test, and passing
tests can still miss defects.

## Evaluation

The `eval/` directory contains six small coding tasks. For every task, the runner:

1. copies a source fixture into a fresh `TemporaryDirectory()`;
2. creates a fresh Agent whose workspace is only that copied fixture;
3. runs the task prompt using the configured real LLM;
4. runs an independent `verify.py` located outside the Agent workspace;
5. records verifier PASS/FAIL and runtime metrics.

Run all tasks or one task with:

```bash
python eval/runner.py
python eval/runner.py --task single_bug_fix --max-steps 12
```

Verified Success Rate is based only on independent verifier exit codes, not the
Agent's final text or stop reason. Results depend on the selected model and run: the
suite is intentionally small, LLM behavior is nondeterministic, it measures only
selected small coding tasks, and passing tests is not formal proof of correctness.

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
Stage 7 - Verification and evaluation

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

Stage 7 can use a failed execution result to choose further inspection, editing, and
execution actions until the model returns final text or a Runtime bound stops the
task. It does not hard-code a `run -> read -> edit -> run` workflow; the model selects
the next tool from the accumulated conversation. For supported source/configuration
changes, a final text response is accepted only after the latest mutation generation
has successful recognized execution evidence.

Possible `AgentResult.stop_reason` values are `completed`, `max_steps`,
`interrupted`, `invalid_response`, `llm_error`, `no_progress`, and
`verification_required`. A response
containing more than eight tool calls is treated as an invalid response and none of
those calls execute.

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
