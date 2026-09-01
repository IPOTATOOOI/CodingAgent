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

## Stage 2

### Goal

Give the language model read-only visibility into the local workspace through explicitly defined tools.

### Architecture

```text
User
  ->
CLI
  ->
Conversation
  ->
LLM
  ->
Tool Call
  ->
Tool Registry
  ->
Read-only Local Tool
  ->
Tool Result
  ->
Conversation
  ->
LLM
  ->
Final Response
```

### Tools

- `list_directory`
- `read_file`
- `search_text`

### Workspace Boundary

Every filesystem path is resolved against a configured workspace root.

Canonical paths outside the workspace are rejected by the local runtime. Resolving
the final path before checking containment also prevents an existing symlink from
bypassing the boundary. The model itself is not treated as a security boundary.

This is basic workspace-boundary enforcement rather than a complete security sandbox.
Secret-bearing `.env` variants are also excluded from reading and search so that API
credentials are not copied into remote model context. `.env.example` remains readable.

### Tool Resolution Policy

Stage 2 intentionally supports only one tool-call resolution round per user turn.

This stage validates the fundamental chain:

```text
LLM decision
  -> structured tool request
  -> local execution
  -> observation
  -> final model response
```

If the second model response contains another tool call, the runtime returns a
controlled limit message and does not make a third model request. General multi-step
autonomous execution is intentionally deferred to Stage 5.

### Current Limitations

The agent can inspect code but cannot modify files or execute commands.

Therefore, it can diagnose many problems but cannot yet apply or verify fixes.

## Stage 3

### Goal

Let the model create and modify UTF-8 text files while keeping every mutation under
explicit local Runtime control.

### Tools

- `write_file`: creates a new file and refuses every existing target.
- `edit_file`: replaces one exact text block in an existing file.

`write_file` is deliberately create-only. Existing files must go through
`edit_file`, which limits a change to a named local block instead of trusting a full
model-generated rewrite. Missing parent directories are created only after the
resolved target has passed the Workspace boundary check.

### Exact Replacement Strategy

`edit_file` reads the current UTF-8 content, counts occurrences of `old_text`, and
only proceeds when the count is exactly one. Zero matches return `TextNotFound`;
multiple matches return `AmbiguousEdit`; empty or identical text is also rejected.
The Runtime then performs one replacement and writes the updated UTF-8 bytes back.
Using raw bytes for decoding and encoding preserves existing CRLF or LF newlines.

### Workspace Mutation Safety

Both mutation tools reuse `resolve_workspace_path`. It resolves the model-supplied
path against the canonical workspace root and rejects any final path outside that
root, including an existing symlink that resolves outside. Create-only behavior,
ordinary-file checks, unique matching, explicit UTF-8 decoding, and the 1 MiB size
limit provide additional safeguards. These checks are enforced by the local Runtime,
not by trusting the model or the system prompt.

### Bounded Tool Resolution

One user turn has two explicit tool-resolution blocks: the first commonly inspects
the file and the second applies an edit. A third model response requesting another
tool is rejected with `ToolResolutionLimitError`. This is fixed control flow rather
than a general autonomous loop.

### Limitations

Stage 3 has no command execution, compilation, or test tool. Mutation success only
confirms that a file operation completed; it cannot verify program correctness, and
the system prompt forbids claiming otherwise. Multiple tool calls in one round are
executed sequentially and multi-file edits are not transactional: an earlier success
is not rolled back when a later call fails. There is no backup, undo, formatter, AST
editing, or automatic import management.

## Stage 4

### Goal

Provide actual execution feedback by allowing the Agent to run non-interactive
development commands locally.

### New Tool

- `run_command`

### Command Representation

Commands are represented as argument arrays, such as
`["python", "-m", "pytest", "-q"]`, rather than raw Shell strings. The Runtime uses
`subprocess.run` with `shell=False` and closed standard input. This avoids implicit
Shell parsing and does not provide interactive terminals or background processes.

### Execution Result

`run_command` returns the exit code, separate stdout and stderr, timeout status,
duration, and independent output-truncation flags. A non-zero exit code is not a Tool
execution failure: it means the process started and the target program reported a
failure. Invalid arguments, invalid working directories, and executables that cannot
be started are structured Tool failures.

### Working Directory and Timeout

Every command working directory reuses `resolve_workspace_path` and must resolve
inside the configured workspace. The default timeout is 30 seconds, with an allowed
range of 1 to 120 seconds. A timeout is returned as a successful execution
observation with `exit_code=null` and `timed_out=true`.

### Output Limits

stdout and stderr are each truncated to 12000 characters before the result is sent to
the model. Because the current implementation uses `subprocess.run` with captured
output, this limits model context size; it is not a process-level memory or output
limit.

### Credential Isolation

The child environment inherits ordinary system settings such as `PATH`, but the
Agent's own `LLM_API_KEY` is removed before process creation.

### Security Boundary

`run_command` is not an OS-level sandbox. Disabling Shell parsing, bounding time,
limiting the working directory, and removing the Agent credential reduce risk, but a
child process still executes with the permissions of the current operating-system
user and may access paths outside the workspace when those permissions allow it.
Containers, virtual machines, and operating-system isolation are outside Stage 4.

### Tool Resolution Policy

Stage 4 contains three explicitly written Tool Resolution rounds. This supports:

```text
inspect
  -> edit
  -> execute
  -> report
```

If execution reveals another problem, a fourth Tool Call is rejected with a
controlled limit error. Execution feedback therefore exists, but it does not yet
drive an unrestricted autonomous repair loop; that control loop is deferred to
Stage 5.

## Stage 5

### Goal

Replace the fixed Stage 4 Tool Resolution rounds with one reusable autonomous control
loop. Execution feedback can now drive further model-selected inspection, editing,
and execution actions.

### Architecture

```text
User
  -> CLI
  -> Agent.run
  -> LLM decision
  -> Tool calls
  -> Local Runtime
  -> Tool observations
  -> Conversation
  -> next LLM decision
  -> ...
  -> Final Answer or Runtime Stop
```

The CLI owns terminal input, dependency construction, bounded trace summaries, and
final display. `Agent` owns user-message insertion, step counting, LLM requests,
tool dispatch, observation recording, and task termination. The CLI contains no
second copy of the Agent Loop.

### Step Semantics

One successfully received LLM response is one Agent Step. If one response contains
multiple Tool Calls, they execute sequentially but together still count as one step.
If both text and Tool Calls are present, the calls take priority; the complete
assistant Tool Call message, including its text, is preserved in Conversation.

### Observation and Recovery

Every tool result is serialized as a matching `role=tool` message. A structured Tool
Error such as `FileNotFound` is an observation rather than an Agent stop. A non-zero
command exit is also an observation because process execution itself succeeded. The
next LLM request sees those results and dynamically chooses whether to inspect,
search, edit, run another command, or finish.

### Runtime Bounds and Stop Reasons

The default `max_steps` is 20 and the CLI accepts any positive integer. When the last
allowed response requests tools, those tools execute and the task then stops with
`max_steps`; no additional LLM request is sent. A single response is limited to eight
Tool Calls. If it exceeds that bound, none of its calls execute and the task stops as
`invalid_response` with a `ToolCallLimitExceeded` explanation.

The current stop reasons are:

- `completed`: the model returned final text without Tool Calls.
- `max_steps`: the bounded loop ended before final text.
- `interrupted`: the user interrupted the active task.
- `invalid_response`: the model returned neither usable text nor valid bounded calls.
- `llm_error`: the model request failed.

Local Runtime stop messages are returned through `AgentResult`; they are not inserted
into Conversation as if the model had authored them.

### Current Limitations

Stage 5 intentionally has no Planner, textual ReAct parser, hidden chain-of-thought
storage, Context Manager, token budget, context compaction, repeated-action detector,
no-progress detector, transient-error retry policy, or mandatory verification rule.
The hard step bound guarantees eventual stopping but cannot diagnose an unproductive
loop; context and reliability improvements belong to Stage 6, while mandatory
verification and evaluation belong to Stage 7.

## Stage 6

### Goal

Improve the reliability of the autonomous Agent Loop without expanding the six-tool
Action Space. Stage 5 proved that the Agent can inspect, modify, execute, and recover
iteratively. Stage 6 keeps that loop context-efficient, bounded, and resistant to
repetitive or locally unauthorized actions.

### Context Manager

Conversation remains the complete local Runtime history. Before each LLM request,
`ContextManager` creates a deep-copied, character-bounded view. The deterministic
policy preserves System instructions and the current User Task, prioritizes recent
actions and observations, compacts older large Tool Results, and then removes the
oldest non-protected groups if required.

Assistant Tool Call messages and their consecutive Tool Result messages are treated
as inseparable protocol groups. Compaction keeps Tool names, call IDs, success state,
paths or commands, and result metadata while removing old file contents and command
streams. The full Conversation is never modified by this process.

### Repeated Actions

Tool Calls are normalized into deterministic signatures made from the Tool name and
JSON arguments serialized with sorted keys. The first two consecutive identical
Actions remain allowed. The third and later consecutive requests are not executed;
the Runtime appends a structured `RepeatedAction` Tool Result so the model has an
opportunity to change strategy.

Successful `list_directory`, `read_file`, and `search_text` signatures are also
remembered for the current Workspace state. An identical later request is skipped
with `RepeatedObservation`; a successful file mutation or any executed command
invalidates the memory. Every 12 different read-only observations without either
kind of action adds a progress advisory so the model summarizes existing evidence
and moves to editing, verification, or completion.

### No-progress Detection

`ReliabilityTracker` maintains state only for the current User Task. A successful
`write_file` or `edit_file`, or a materially different Observation, resets the
no-progress counter. Repeated Actions, Command Policy blocks, and identical Tool
observations do not count as progress. Command fingerprints use Action arguments,
exit code, timeout state, stdout, and stderr while excluding duration noise.

Four consecutive no-progress Agent Steps stop the task with
`stop_reason=no_progress`. The detector does not decide that a programming task is
successful; only an LLM Final Answer can produce `completed`.

### LLM Retry

Rate limits, connection failures, timeouts, and HTTP 5xx responses are normalized as
transient `LLMError` instances. A request may be retried twice with fixed 0.5- and
1-second delays, for at most three attempts. Authentication, invalid configuration,
model-not-found, and invalid-response errors are permanent and stop immediately.
Failed attempts do not count as Agent Steps because no model decision was received.

### Runtime Command Policy

System Prompt rules are behavioral guidance rather than authorization boundaries.
Before `run_command` creates a subprocess, `CommandPolicy` checks the executable
basename and argument array. Package or environment mutation through pip, conda,
mamba, npm, yarn, or pnpm is blocked in the supported direct and
`python -m pip` forms. Direct destructive executables such as rm, rmdir, del, erase,
and Remove-Item are also blocked. Normal tests, builds, scripts, and compilers remain
allowed.

Blocked requests become structured `CommandBlocked` Tool Results. They do not end a
task immediately; the model can select an existing-environment alternative. Repeated
violations are handled by the same no-progress mechanism.

### Security Scope

The Command Policy is not an operating-system sandbox. Allowed subprocesses still
run with the current user's permissions and may access resources outside the logical
Workspace boundary. Stage 6 adds explicit local authorization rules, not container,
virtual-machine, or kernel isolation.

### Relationship to max_steps

`max_steps` remains the final hard execution bound because repeated-action and
no-progress rules are heuristics. Stage 6 attempts to stop wasteful behavior earlier,
while the Stage 5 limit still guarantees eventual termination.

### Current Limitations

Stage 6 does not require every mutation to be verified, judge semantic task success,
run benchmarks, calculate success rates, or provide semantic long-term memory. The
mandatory verification and evaluation layer remains deferred to Stage 7.
## Stage 7：Verification and Evaluation

### 目标与边界

Stage 7 不增加 Agent Tool。它在 Stage 6 自主循环外增加两层判断：Runtime
Verification Gate 约束模型何时可以完成任务；Evaluation Framework 使用模型不可见的
独立 verifier 判断最终 workspace 是否真的满足选定任务要求。本阶段不实现 Planner、
Multi-Agent、RAG、MCP、GUI 或 OS/Docker Sandbox。

### VerificationTracker

`VerificationTracker` 每个 User Task 重置，并维护：

- `mutation_generation`：需要验证的成功修改代数；
- `verified_generation`：最近一次成功验证覆盖的代数；
- `verification_status`：`not_required`、`unverified`、`failed` 或 `verified`。

成功的 `write_file` 和 `edit_file` 只有在目标后缀属于支持的源码或工程配置集合时，
才增加 mutation generation。`.md`、`.txt` 和 `.rst` 等文档修改不进入强制代码验证。
每次新代产生后，即使上一代已验证，状态也重新变为 `unverified`。

### Verification command classifier

分类器只识别一个小型确定性集合：pytest、unittest、compileall、py_compile、直接测试
脚本、Node `--check` / `-c` / `--test`，以及常见 npm/yarn/pnpm、Go、Cargo、Maven 和 Gradle 测试或构建命令。普通
`python script.py` 和 `python -c` 不会仅因 exit code 0 被视为验证。

只有最新 mutation 后发生的已识别命令，在没有 timeout 且 exit code 为 0 时，才令
`verified_generation == mutation_generation`。非零退出码把状态置为 `failed`，结果仍作为
Observation 返回 LLM，以便继续修复。

### Completion Gate

模型返回 Final Answer 时，Agent 先检查 `completion_blocked`。若最新代已经验证或本任务
不需要验证，正常返回 `completed`。否则将模型回复作为尚未接受的草稿，并追加 Runtime system reminder，
让模型执行测试、构建或语法检查。Reminder 预算随 `max_steps` 调整，最少 5 次、最多 8 次；模型继续只返回 Final Answer
时，以 `verification_required` 停止。Reminder 不伪装成 Tool Result，也不制造假的
`tool_call_id`。所有额外决策仍受原有 `max_steps` 约束。

SessionStore 额外保存最后一次运行的 `stop_reason`、步数、Tool Call 数和 Verification 状态。
GUI 仅把 `completed` 恢复为最终回答；未验证草稿、达到步数上限或运行失败会恢复为对应的
未完成状态，避免把 Worker 已结束与任务已完成混为一谈。

`AgentResult` 在原字段之后增加 `tool_calls` 和 `verification_status`。Tool call 指模型实际
请求并由 Runtime 处理的调用，包含被 `RepeatedAction` 或 `CommandBlocked` 控制的请求。

### Independent Evaluation

`eval/tasks.json` 描述 6 个顺序任务。每个 fixture 被复制到全新的临时 workspace，随后
创建新 Agent。任务目录中的 `verify.py` 位于 workspace 外，因此 Agent 文件工具无法读取。
Agent 结束后，Runner 用单独进程运行 verifier；退出码 0 才是 PASS。评测成功不依赖
`stop_reason == completed`，从而保持 Agent self-report 与 evaluation ground truth 分离。

Runner 记录 task ID、独立成功状态、stop reason、steps、tool calls、verification status
和耗时，并汇总 Verified Success Rate、平均指标和 stop reason 分布。评测集有意很小，
LLM 具有非确定性，结果依赖模型与单次运行，且测试通过不是形式化正确性证明。

### Stage 7 可靠性增强

验证状态除了 generation 之外还记录尚未验证的 workspace 相对路径。项目级测试或构建
可以覆盖全部待验证修改；`py_compile` 和 `compileall` 等窄范围语法检查必须覆盖对应
文件或目录，避免用无关文件的成功编译解锁 Completion Gate。成功验证后清空待验证路径。

窄范围验证证据按路径累计：`py_compile`、`compileall` 和 Node `--check` 每次成功后只移除
本次实际覆盖的待验证路径，全部路径清空后才进入 `verified`。部分覆盖或完全选错目标时，
Runtime 立即追加包含剩余路径的 System feedback，而不是等模型再次提交 Final Answer 才给出
模糊提醒。GUI 对同一轮 Verification guidance 复用一个 Activity Item，更新提醒次数和剩余
路径；问题解决后该 Item 转为成功状态。

Evaluation Runner 默认输出有界实时轨迹，并在每个任务结束后原子保存 checkpoint。
它支持顺序多轮运行，统计 per-task pass rate、suite-run success rate、Agent completion
rate、max-steps rate 和 p50/p95。结果元数据包含版本、commit、模型、Python、平台和
运行配置，但不包含凭据或 Base URL 内容。

Verifier 源码在 Agent 启动前读入内存，Agent 停止后才写入新的临时目录并执行；Runner
同时检查原始 fixture 和 verifier 的摘要。完整性破坏会使任务失败。这可以防止 verifier
在评测期间被篡改后影响真值，但允许的子进程仍继承 OS 用户权限，因此不等于安全沙箱。

`eval/verification_cases.py` 将 Completion Gate A～D 变成可重复、可保存 checkpoint 的
真实 LLM 验收。GitHub Actions 则在 Linux/Windows、Python 3.10/3.13 上执行不依赖真实
LLM 的确定性单元测试、compileall 和版本一致性检查。

## Observable Runtime GUI Enhancements

### Evidence Trail

GUI 直接订阅 Stage 6 的 `RuntimeEvent`，通过 `EvidenceTrailBuilder` 生成稳定版本的结构化
Trace。它记录 Task、Model、Step、Tool Call、唯一创建/修改文件数量、创建目录数量、
Verification、Stop Reason 和 Duration。Tool 参数中的完整 `content`、`old_text`、
`new_text` 被替换为长度摘要；另行保留最多 8,000 字符的 Diff 作为可审计修改证据。
read_file 正文不进入 Trace，stdout/stderr 只保存有界预览。

`EvidenceStore` 采用临时文件、flush、fsync 和 `os.replace` 原子写入。Export 复制当前
Snapshot；Replay 只解析版本化 JSON 并重建 GUI Activity，不创建 Agent、ToolRegistry 或
subprocess，因此是严格的只读展示，而不是重新执行历史动作。CLI 保持原有默认无隐式
持久化语义，自动 Evidence 仅用于 GUI 任务。自动 Trace 默认只保留最近 100 份、30 天，
清理范围严格限制在 EvidenceStore 根目录；显式导出的副本不受影响。

### Approval Mode

`ToolRegistry` 在参数 schema 验证之后、handler 调用之前执行可选 `ApprovalCallback`。
Read 工具始终自动允许；Ask 对目录/文件修改和命令询问；Auto Edit 自动允许修改、询问
命令；Auto 自动允许但仍受 Workspace Boundary 和 Command Policy 约束；Read Only 在执行
前返回 `ReadOnlyMode`。GUI Worker 使用 Event/Lock 等待主线程授权卡片，等待期间 GUI
保持响应，Stop 会唤醒等待并拒绝尚未执行的调用。授权结果也写入 Evidence Trail。

### Diff-first 与目录工具

现有 Unified Diff 生成逻辑继续复用，但成功 Edit/Create 的 Activity Item 改为先展示
`@@ 位置 @@`、删改行和 `✓ Applied`，结果摘要退居其后。`create_directory` 用于表达空目录
或显式项目结构，并报告一次调用实际补齐的全部层级；`write_file` 仍自动创建父目录，模型
无需在每次写文件前机械调用目录工具。
