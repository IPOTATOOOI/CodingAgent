# Mini Coding Agent

A lightweight coding agent implemented from scratch.

## Current Status

Stage 9: Runtime events, streaming interaction, steering, and durable sessions.

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
- A PySide6 desktop GUI that reuses the existing Agent Runtime
- A typed Runtime Event stream shared by the Agent Worker and GUI
- Streaming model text with cooperative cancellation
- Running-task steering and queued follow-up messages
- Atomic, workspace-scoped conversation persistence and recovery
- Character and estimated-token context budgets
- Optional tool schema generation from typed Python callables

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

如需运行测试，请安装测试依赖：

```bash
pip install -e ".[test]"
```

PySide6 是桌面界面的运行依赖，会随项目一起安装。GUI 和 CLI 使用同一套
Agent Runtime、LLM 配置及 Workspace 安全边界。

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

### Desktop GUI

```bash
python -m coding_agent.gui --workspace . --max-steps 20
```

安装项目后也可以使用：

```bash
coding-agent-gui --workspace . --max-steps 20
```

GUI 采用适合演示 Autonomous Loop 的三栏结构：

- 左侧 `Project` 使用 Qt 文件系统模型浏览当前 Workspace；双击受支持的 UTF-8
  文本文件可进行只读、有大小限制的预览。
- 中间 `Conversation` 使用右侧用户气泡、左侧 Agent 气泡和中性 Runtime 提示块，
  Agent Final Response 支持常见 Markdown 标题、列表、粗体和代码格式，并提供多行
  任务输入、Run、Stop 和 Clear 操作。
- 右侧 `Agent Activity / Execution Trace` 以面向用户的时间线解释每一步在做什么、
  执行结果意味着什么，以及 Agent 为什么继续或停止。例如“运行项目测试 → 测试失败，
  继续定位和修复”。Read、Search、List、Create、Edit、Run 等底层工具标签仍会保留；
  真实命令、参数、退出码和有界 stdout/stderr 放在点击详情中，不会把完整文件内容或
  无限输出直接塞进主时间线。
- 成功的 `edit_file` 步骤会显示文件路径、准确修改行号以及有界的 `- 修改前 / + 修改后`
  Diff；点击步骤可以查看更完整的 Unified Diff。失败的编辑不会展示为已发生的修改。
- 底部状态栏显示 Agent 状态、步数、Verification、工具调用数量和耗时。
- 顶部太阳/月亮图标可以即时切换亮色和暗色主题，不会清空当前会话。

LLM 请求和 Agent Loop 在 `QThread` Worker 中执行，主线程只处理 Qt 界面更新。
Stop 使用协作式取消：流式 LLM 请求会在收到下一个数据块时停止；`run_command` 每
100ms 检查一次取消状态并终止直接子进程；Agent 仍只在协议安全的步骤边界修改
Conversation。它不会强制终止 Python Worker 线程，也不是操作系统级进程沙箱。

任务运行期间，中间输入区保持可用，`Run Task` 会变成 `Send Update`。此时提交的文字
会进入线程安全的 steering 队列，在当前操作结束后的下一个 Agent Step 作为新 User
Message 生效。如果消息恰好在模型生成最终回答期间到达，Runtime 会继续下一步处理，
不会静默丢弃它。`AgentMessageQueue` 也提供独立的 follow-up 队列，Worker 会在当前任务
完成后按加入顺序执行。

GUI 默认会在每次任务结束后把 Conversation 快照交给单线程后台写入器，并在再次打开
相同 Workspace 时恢复；顶部 `Auto-save` 可以随时关闭自动保存，`Clear Saved` 经确认后
删除全部 Workspace 的已保存会话。Windows 默认保存到
`%LOCALAPPDATA%/MiniCodingAgent/sessions`；其他平台使用用户目录下的
`.mini-coding-agent/MiniCodingAgent/sessions`。文件名由平台规范化后的 Workspace 路径
哈希生成。

单个会话文件默认硬限制为 2 MiB。超过限制时，SessionStore 会按完整 Assistant Tool Call /
Tool Result 协议组压缩较早结果并保留最近上下文；如果受保护的 System/User 消息本身仍然
超限，则拒绝写入而不会产生半个文件。默认只保留最近 20 个、30 天内的会话。Unix 会话
目录和文件分别使用 `0700` / `0600` 权限。保存内容是本地明文消息、模型名和时间戳，
可能包含源代码与有界工具输出，但不保存 API Key；敏感项目可关闭 Auto-save。
`New Session` / `Clear Conversation` 会删除当前 Workspace 的已保存会话。

选择 Workspace 只会刷新文件树并为下一次任务创建相应的 Tool Registry；文件读写和
命令执行仍通过现有 Runtime 的规范路径检查，GUI 不会绕过 Workspace Boundary。

### Command-line interface

```bash
python -m coding_agent --workspace . --max-steps 20
```

or:

```bash
coding-agent --workspace . --max-steps 20
```

CLI 默认仍不读写会话文件。需要时可以显式启用：

```bash
coding-agent --workspace . --resume-session
coding-agent --workspace . --save-session
```

`--resume-session` 会恢复并在每轮后继续保存最近会话；`--save-session` 只从新会话开始保存。
CLI 与 GUI 使用相同的 2 MiB 上限、协议安全压缩和默认保留策略。

`--max-steps` limits the number of LLM responses in one user task. Its default is
20 and its accepted range is 1 through 50. Reaching the limit stops immediately
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
   Conversation. The default budgets are 60000 characters and an estimated 16000
   tokens; the recent-group target is 12. The dependency-free estimate treats CJK
   text more conservatively than a fixed characters-per-token ratio.
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

## Runtime Event 与扩展接口

`Agent` 通过可选的 `on_event(RuntimeEvent)` 发布统一事件，包括 task/step 生命周期、
Context 构建、LLM 请求与文本增量、重试、Tool 开始/结束、steering、Verification 和最终
停止原因。事件是普通 dataclass，不依赖 Qt；GUI Worker 只负责把它转成 Qt Signal。
原来的 `on_tool_call`、`on_tool_result` 和 `on_llm_retry` 回调继续保留，因此 CLI 和已有
集成不需要立即迁移。

新增工具可以使用 `ToolDefinition.from_callable(...)` 从类型注解生成基础 JSON Schema，
再通过 `parameter_overrides` 明确补充 minimum、maximum 等安全约束。现有六个核心工具仍
保留人工审查过的 schema，避免自动推断意外放宽 Runtime 边界。

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

The Runtime also tracks pending modified paths. Project-wide tests and builds may
cover all pending changes, while narrow syntax checks must target them. For example,
`python -m py_compile other.py` cannot verify a change to `app.py`, and a
`compileall src` check cannot verify an unrelated modified `config/settings.json`.

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
python eval/runner.py --runs 5
```

The runner prints bounded per-step progress by default; `--quiet-trace` hides it.
Every completed task atomically updates the result JSON, so an interrupted suite
retains prior results as an incomplete checkpoint. Multi-run summaries include
per-task pass rates, suite-run success rate, completion/max-step rates, and p50/p95
steps, tool calls, and duration.

Result metadata records the model name, Coding Agent version, Git commit, Python and
platform versions, max steps, run count, and whether a custom base URL was configured.
It never stores the API key or Base URL value. Agent metrics include Runtime
verification reminders, LLM retries, and mutation generations.

The runner captures verifier source in memory before the Agent starts, creates the
temporary verifier snapshot only after the Agent stops, and checks that the original
fixture and verifier were not modified. This strengthens verifier integrity but is
still not an OS-level process sandbox.

The four Completion Gate acceptance scenarios can be rerun and saved with:

```bash
python eval/verification_cases.py --max-steps 12
python eval/verification_cases.py --case B --max-steps 12
```

Verified Success Rate is based only on independent verifier exit codes, not the
Agent's final text or stop reason. Results depend on the selected model and run: the
suite is intentionally small, LLM behavior is nondeterministic, it measures only
selected small coding tasks, and passing tests is not formal proof of correctness.

## Continuous Integration

GitHub Actions runs the unit suite, syntax compilation, editable installation, and
package-version consistency checks on Linux and Windows with Python 3.10 and 3.13.
Real LLM evaluations remain manual because they use external credentials, cost, and
nondeterministic model behavior.

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
python -m coding_agent --workspace examples/demo_project --max-steps 20
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
