# Mini Coding Agent

<p align="center">
  <strong>一个从零实现、由真实执行反馈驱动、可观察且具备验证意识的本地 Coding Agent。</strong><br>
  <em>A framework-free, execution-grounded and observable coding agent built directly in Python.</em>
</p>

<p align="center">
  <a href="https://github.com/IPOTATOOOI/CodingAgent/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/IPOTATOOOI/CodingAgent/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.10–3.13" src="https://img.shields.io/badge/Python-3.10%E2%80%933.13-3776AB?logo=python&logoColor=white">
  <img alt="Version 0.10.0" src="https://img.shields.io/badge/version-0.10.0-6f42c1">
  <img alt="PySide6 GUI" src="https://img.shields.io/badge/GUI-PySide6-41CD52?logo=qt&logoColor=white">
</p>

![Mini Coding Agent desktop GUI](./01desktop_GUI.png)

Mini Coding Agent 不是把一组固定步骤包装成聊天窗口。模型每一步都根据真实工具结果决定下一步；Runtime 负责上下文边界、工具分发、可靠性约束、命令策略和完成验证；CLI 与桌面 GUI 只是同一套 Agent Runtime 的两个交互入口。

Mini Coding Agent is not a chat window wrapped around a fixed workflow. Each model decision is grounded in real tool results, while the Runtime owns context bounds, tool dispatch, reliability policies, command control and completion verification. The CLI and desktop GUI are two interfaces over the same Agent Runtime.

## Why Mini Coding Agent? / 为什么做这个项目？

普通 LLM 对话可以建议如何修改代码，但一个 Coding Agent Harness 还必须把“建议”变成受控、可观察、可验证的本地执行过程。

A regular LLM chat can suggest a fix. A coding-agent harness must turn that suggestion into a controlled, observable and verifiable local execution process.

| 普通代码对话 / Plain coding chat | Mini Coding Agent Harness |
| --- | --- |
| 根据文本猜测项目状态 / Infers state from text | 通过文件和命令工具观察真实项目 / Observes the real project through tools |
| 给出一次性答案 / Produces a one-shot answer | 根据新 Observation 持续选择下一步 / Chooses the next action from fresh observations |
| 依赖 Prompt 约束行为 / Relies mainly on prompting | 在 Runtime 中执行确定性策略 / Enforces deterministic Runtime policies |
| 修改后即可声称完成 / May claim completion after editing | 支持的代码修改必须获得执行证据 / Requires execution evidence for supported mutations |
| 长对话直接塞入 Prompt / Sends growing history directly | 保留完整历史，同时构建有界模型上下文 / Preserves full history while bounding model context |

## Highlights / 核心亮点

| Highlight | 工程含义 / Engineering meaning |
| --- | --- |
| **① Built from Scratch**<br>**Framework-free Agent Harness** | Agent Loop、Conversation、Tool Dispatch、Context Management、Reliability 和执行控制均直接使用 Python 实现，而不是委托给 Agent Framework。<br><br>The core loop, conversation state, tool dispatch, context management, reliability policies and execution control are implemented directly in Python. |
| **② Feedback-driven Autonomy**<br>**Execution feedback drives the next action** | 流程不是硬编码的 `read → edit → test`。每次 Tool Result 都成为新的 Observation，由模型重新决定后续动作。<br><br>The workflow is not hard-coded. Every tool result becomes a new observation that drives the next model decision. |
| **③ Bounded Context**<br>**Full history, bounded model context** | Runtime 保存完整 Conversation，但只向模型发送经过协议安全压缩的有界视图。<br><br>The Runtime preserves complete history while sending only a bounded, protocol-safe view to the model. |
| **④ Runtime Reliability**<br>**Runtime guardrails, not prompt-only safety** | 重复动作、无进展、重试、最大步骤和危险命令由确定性 Runtime Policy 处理，而不是只写一句“请不要这样做”。<br><br>Repeated actions, no-progress loops, retries, step bounds and dangerous commands are handled by deterministic Runtime policies. |
| **⑤ Verification-aware Completion**<br>**An edit is an action, not proof of correctness.** | 修改会产生新的 mutation generation；只有获得匹配的测试、构建或语法检查证据后，支持的代码任务才能完成。<br><br>A mutation creates a new generation. Supported code tasks complete only after matching test, build or syntax-check evidence exists. |

> **Full runtime history ≠ Full prompt**<br>
> **完整运行历史 ≠ 每次都把全部历史发送给模型**

## How It Works / 系统如何工作

![Mini Coding Agent architecture](./02Architecture.png)

整体分为三层：Interface Layer 提供 CLI 与 PySide6 GUI；Agent Harness 负责对话、上下文、决策循环、可靠性与验证；Local Runtime 将结构化 Tool Call 落到受 Workspace Boundary 和 Command Policy 约束的真实文件系统与进程。

The system has three layers: the Interface Layer provides CLI and PySide6 GUI; the Agent Harness owns conversation, context, decision loops, reliability and verification; the Local Runtime maps structured tool calls to the real filesystem and processes under workspace and command policies.

> 架构图强调职责边界。当前工具的精确语义以本文的 Tool Reference 为准：Runtime 目前注册 7 个核心工具，`write_file` 只创建新文件而不会覆盖已有文件。<br>
> The figure focuses on responsibility boundaries. The Tool Reference below is authoritative: the current Runtime registers seven core tools, and `write_file` is create-only.

### Autonomous control loop / 自主控制循环

![Autonomous Agent Loop](./03Autonomous_Agent_Loop.png)

一次 LLM Response 算一个 Agent Step；同一 Response 可以提出多个结构化 Tool Call，这些工具按顺序执行并作为完整 Tool Result 组追加到 Conversation，然后才开始下一步。工具失败是 Observation，而不是自动终止条件：例如测试退出码为 1 可以驱动读取错误、修改实现并重新验证。

One LLM response is one agent step. A response may request multiple structured tool calls; they execute sequentially and their results are appended as a complete protocol group before the next decision. Tool failure is an observation, not an automatic stop condition: a failing test can drive inspection, repair and another verification run.

```text
User Task
   ↓
LLM Decision ── final response ──→ Completion Gate
   ↓ tool calls                         ↑
Local Runtime → Tool Results → Conversation
   └────────────────────────────→ Next Decision
```

### Context and reliability / 上下文与可靠性

![Bounded Context and Runtime Guardrails](./04Context+Reliability.png)

`ContextManager` 在每次模型请求前构建有界视图，默认上限为 60,000 字符和估算 16,000 tokens，并优先保留 System 指令、当前任务和最近 12 个协议组。Assistant Tool Call 与随后的 Tool Results 会作为一个整体保留、压缩或移除，不会破坏原生 Tool Calling 关系。

Before every model request, `ContextManager` builds a bounded view with default budgets of 60,000 characters and an estimated 16,000 tokens. System instructions, the current task and the latest 12 protocol groups are prioritized. Assistant tool calls and their following tool results are retained, compacted or removed as a unit.

可靠性不是一条 Prompt，而是一组运行时规则：

Runtime reliability is a set of enforceable rules, not a single prompt instruction:

- 第 3 次连续相同动作在执行前返回 `RepeatedAction`。<br>
  The third consecutive identical action is rejected before execution.
- Workspace 未变化时，相同只读 Observation 返回 `RepeatedObservation`。<br>
  Duplicate read-only observations are skipped while the workspace is unchanged.
- 连续读取过多会触发 progress warning；连续 4 个 Step 没有新 Observation 或有效修改会以 `no_progress` 停止。<br>
  Excessive inspection emits progress guidance; four no-progress steps terminate with `no_progress`.
- 瞬时 LLM 错误最多重试 2 次；认证和模型不存在等永久错误不重试。<br>
  Transient LLM errors are retried at most twice; permanent errors are not retried.
- `max_steps` 是最终硬边界，默认 20，但可设置任意正整数。<br>
  `max_steps` is the final hard bound. It defaults to 20 and accepts any positive integer.

### Verification-aware completion / 验证感知的完成条件

![Verification Gate](./05Verification_Gate.png)

> **Mutation → Verification → Completion**<br>
> **修改 → 验证 → 完成**

成功执行 `write_file` 或 `edit_file` 只说明文件写入成功。对受支持的源码和项目配置修改，Runtime 会将最新 mutation generation 标为 `unverified`；只有能覆盖待验证路径的测试、构建或语法检查以退出码 0 完成后，最终回答才会被接受为 `completed`。

A successful `write_file` or `edit_file` call proves only that bytes were written. For supported source and project-configuration changes, the Runtime marks the latest mutation generation as `unverified`. A final response is accepted as `completed` only after a recognized test, build or syntax check covers the pending paths and exits successfully.

窄范围证据可以累计；验证通过后再次修改会创建新 generation 并要求重新验证；纯 `.md`、`.txt`、`.rst` 文档修改不触发代码验证门。该机制要求执行证据，但不声称测试通过等于形式化正确。

Narrow evidence may accumulate. A mutation after a successful check creates a new generation and requires new evidence. Documentation-only `.md`, `.txt` and `.rst` changes bypass the code gate. This mechanism requires execution evidence; it does not claim that passing tests is formal proof of correctness.

## Observable Desktop Interface / 可观察桌面界面

PySide6 GUI 不重新实现 Agent。`AgentWorker` 在 `QThread` 中运行现有 Runtime，并把框架无关的 `RuntimeEvent` 转换为 Qt Signals，因此 LLM 请求和命令执行不会阻塞主线程。

The PySide6 GUI does not reimplement the agent. `AgentWorker` runs the existing Runtime in a `QThread` and converts framework-neutral `RuntimeEvent` objects into Qt signals, keeping LLM requests and command execution off the UI thread.

| 区域 / Area | 展示内容 / What it shows |
| --- | --- |
| **Project / 项目文件** | Workspace 文件树与受限文本预览 / Workspace tree and bounded text preview |
| **Conversation / 对话** | 用户气泡、Markdown 最终回答、Runtime 提示与运行中 steering / User bubbles, Markdown responses, Runtime notices and live steering |
| **Activity / 执行轨迹** | 面向用户的步骤说明、Tool 参数摘要、结果、验证状态和可展开 Diff / User-facing step explanations, bounded details, results, verification and expandable diffs |
| **Task Evidence / 任务证据** | Steps、Tool Calls、Duration、Stop Reason、文件变更和 Verification / Steps, tool calls, duration, stop reason, file changes and verification |

GUI 支持亮/暗主题、Diff-first 详情、Ask / Auto Edit / Auto / Read Only 四种授权模式、协作式 Stop、会话自动保存、Evidence JSON 导出与只读回放。Evidence 和 Session 都有大小及保留上限；它们可能包含本地代码片段，分享前应检查。

The GUI supports light/dark themes, diff-first details, four approval modes, cooperative stop, durable sessions, Evidence JSON export and read-only replay. Evidence and session files are bounded and retention-limited; they may still contain local code snippets and should be reviewed before sharing.

## End-to-End Example / 端到端示例

用户不需要指定固定流程，只需描述目标：

The user describes the goal rather than prescribing a fixed workflow:

```text
Run the project tests, locate the failing implementation,
fix the problem, and verify the result.

Step 1  run_command     tests fail, exit_code = 1
Step 2  read_file       inspect the relevant implementation
Step 3  edit_file       apply a bounded code change
Step 4  run_command     tests pass, exit_code = 0
Gate    verification    verified
Final   Agent response  completed with execution evidence
```

模型也可以根据 Observation 选择搜索、浏览其他目录、创建缺失文件，或在验证失败后继续修复；Runtime 并未硬编码上述顺序。

The model may instead search, inspect other directories, create missing files or continue repairing after failed verification. The Runtime does not hard-code the sequence above.

## Independent Evaluation / 独立评估

`eval/` 包含 6 个小型真实 LLM Coding Agent fixture。Runner 为每项任务复制全新临时 Workspace，创建新的 Agent，并在 Agent 停止后运行位于 Workspace 外部、对 Agent 不可见的 `verify.py`。Verified Success Rate 只取决于独立 verifier 的退出码，不依赖 Agent 的最终文本或自我报告。

`eval/` contains six small real-LLM coding fixtures. For each task, the runner creates a fresh temporary workspace and agent, then executes an agent-invisible `verify.py` outside that workspace. Verified Success Rate depends only on the independent verifier exit code, not on the agent's final text or self-report.

项目中最近一次保存的完整运行记录（2026-08-29，`glm-5.3`，Python 3.13，`max_steps=12`）为：

The latest recorded complete run in this workspace (2026-08-29, `glm-5.3`, Python 3.13, `max_steps=12`) reported:

| Metric / 指标 | Recorded result / 记录结果 |
| --- | ---: |
| Independent verifier pass / 独立验证通过 | **6 / 6** |
| Verification status = verified | **6 / 6** |
| Agent stop reason = completed | **4 / 6** |
| Agent stop reason = max_steps | **2 / 6** |
| Average steps / 平均步骤 | **8.333** |
| Average tool calls / 平均工具调用 | **9.5** |
| Completion Gate cases A–D / 验证场景 A–D | **4 / 4** |

两项任务虽然到达 `max_steps`，但独立 verifier 已经通过。这正是分离“Agent 是否主动宣告完成”和“工作区结果是否真实正确”的原因。该结果只是一轮可复现记录，不是通用模型排行榜；任务集较小，LLM 行为具有非确定性。

Two tasks reached `max_steps` even though their independent verifiers passed. This is why agent-declared completion and workspace correctness are reported separately. The table is one reproducible recorded run, not a general model benchmark; the suite is intentionally small and LLM behavior is nondeterministic.

```bash
python eval/runner.py
python eval/runner.py --task single_bug_fix --max-steps 12
python eval/runner.py --runs 5
python eval/verification_cases.py --max-steps 12
```

结果会原子写入 `eval/results/` checkpoint。多轮运行会统计每任务 pass rate、整套成功率、completion/max-steps rate，以及 steps、tool calls 和 duration 的 p50/p95。真实 LLM Evaluation 默认不进入 CI，因为它依赖外部凭据、费用且具有非确定性。

Results are checkpointed atomically under `eval/results/`. Multi-run reports include per-task pass rates, suite success, completion/max-step rates and p50/p95 steps, tool calls and duration. Real-LLM evaluation is intentionally excluded from CI because it requires external credentials, incurs cost and is nondeterministic.

## Installation & Usage / 安装与使用

### Requirements / 环境要求

- Python 3.10 or newer / Python 3.10 及以上
- An OpenAI-compatible Chat Completions API / 兼容 OpenAI 的 Chat Completions API

```bash
pip install -e .
```

安装测试依赖 / Install test dependencies:

```bash
pip install -e ".[test]"
```

### Configuration / 配置

复制 `.env.example` 为 `.env`，或设置同名系统环境变量：

Copy `.env.example` to `.env`, or define the same values as environment variables:

```dotenv
LLM_API_KEY=your-api-key
LLM_MODEL=your-model
LLM_BASE_URL=https://your-compatible-provider.example/v1
```

`LLM_API_KEY` 与 `LLM_MODEL` 必填，`LLM_BASE_URL` 可选。系统环境变量优先于 `.env`；`.env` 已被 Git 忽略，禁止提交真实密钥。

`LLM_API_KEY` and `LLM_MODEL` are required; `LLM_BASE_URL` is optional. Environment variables override `.env`. The `.env` file is Git-ignored and real credentials must never be committed.

### Desktop GUI / 桌面界面

```bash
python -m coding_agent.gui --workspace examples/demo_project --max-steps 20
```

安装后也可以使用入口命令 / Installed entry point:

```bash
coding-agent-gui --workspace examples/demo_project --max-steps 20
```

### CLI / 命令行

```bash
python -m coding_agent --workspace examples/demo_project --max-steps 20
```

安装后也可以使用 / Installed entry point:

```bash
coding-agent --workspace examples/demo_project --max-steps 20
```

CLI 默认不持久化会话。需要时可以显式启用：

The CLI does not persist conversations by default. Enable it explicitly when needed:

```bash
coding-agent --workspace . --save-session
coding-agent --workspace . --resume-session
```

`--max-steps` 限制单个用户任务允许的 LLM Response 数量。默认值为 20，可接受任意正整数；设置更大的值会增加 API 使用量、运行时间和本地工具动作数量。

`--max-steps` bounds the number of LLM responses in one user task. It defaults to 20 and accepts any positive integer; larger values increase API usage, runtime and the number of local tool actions.

## Tool Reference & Safety Model / 工具与安全模型

| Tool | Current semantics / 当前语义 |
| --- | --- |
| `list_directory` | 列出 Workspace 内目录的直接子项 / List direct entries inside the workspace |
| `read_file` | 按行读取受限大小的 UTF-8 文本 / Read bounded UTF-8 text with line metadata |
| `search_text` | 在 Workspace 内进行有界字面文本搜索 / Bounded literal search inside the workspace |
| `create_directory` | 显式创建目录并安全补齐缺失父目录 / Create a directory and missing parents safely |
| `write_file` | 只创建新文件并补齐父目录；拒绝覆盖 / Create new files and parents; never overwrite |
| `edit_file` | 仅在 `old_text` 唯一匹配时执行精确替换 / Replace only when `old_text` matches exactly once |
| `run_command` | 使用参数数组、`shell=False` 和 Workspace-scoped `cwd` 执行非交互命令 / Execute non-interactive commands with argument arrays, `shell=False` and workspace-scoped `cwd` |

核心边界 / Key boundaries:

- 所有文件路径解析为规范路径后必须位于 Workspace 内。<br>
  Canonical file paths must remain inside the workspace.
- `.env` 等敏感环境文件不能通过读取或搜索工具访问；`.env.example` 可检查。<br>
  Secret-bearing `.env` variants are excluded from read/search tools; `.env.example` remains visible.
- `pip install`、`python -m pip install`、`conda install`、`npm install` 及对应卸载形式会在创建 subprocess 前被阻止。<br>
  Package/environment mutation commands are blocked before subprocess creation.
- `rm`、`rmdir`、`del`、`erase`、`Remove-Item` 等直接删除命令同样被阻止。<br>
  Direct deletion commands are also blocked.
- `run_command` 默认超时 30 秒、最大 120 秒，stdout/stderr 分别以 12,000 字符为 Context 上限；子进程环境会移除 `LLM_API_KEY`。<br>
  Commands default to 30 seconds, cap at 120 seconds, bound stdout/stderr to 12,000 context characters each, and do not inherit `LLM_API_KEY`.
- GUI 的 Read Only / Ask / Auto Edit / Auto 是额外授权层，不替代 Workspace Boundary 或 Command Policy。<br>
  GUI approval modes add user control without replacing workspace or command policies.

> **Important:** 这是受边界约束的本地执行器，不是 OS-level sandbox。允许的子进程仍拥有当前操作系统用户的权限。<br>
> **Important:** This is a bounded local executor, not an OS-level sandbox. Allowed child processes still run with the current user's permissions.

### Operational bounds / 运行数据边界

| Data | Bound / 上限与保留策略 |
| --- | --- |
| LLM-visible context | 60,000 chars + estimated 16,000 tokens; latest 12 protocol groups prioritized |
| File tools | UTF-8 text; files larger than 1 MiB rejected |
| Session snapshot | 2 MiB each; latest 20; 30-day retention |
| Evidence Trace | 4 MiB each; latest 100; 30-day retention |
| Tool calls per step | At most 8; an oversized response executes none |

## Project Structure / 项目结构

```text
CodingAgent/
├── src/coding_agent/
│   ├── agent.py              # autonomous loop and stop reasons
│   ├── conversation.py       # full protocol-safe history
│   ├── context.py            # bounded LLM-visible context
│   ├── reliability.py        # repeat/no-progress policies
│   ├── verification.py       # mutation generations and evidence gate
│   ├── approval.py           # Read Only / Ask / Auto Edit / Auto
│   ├── evidence.py           # structured task evidence
│   ├── session.py            # bounded durable conversations
│   ├── tools/                # filesystem, command and registry
│   └── gui/                  # PySide6 view layer and QThread worker
├── tests/                    # deterministic unit and integration tests
├── eval/                     # six real-LLM fixtures and independent verifiers
├── examples/demo_project/    # safe demonstration workspace
├── DESIGN.md                 # detailed design decisions
└── pyproject.toml            # package metadata and entry points
```

核心模块不依赖 Qt；GUI 只消费 Runtime Event 并调用现有 Agent，因此 CLI、测试和其他集成可以独立复用 Harness。

Core modules do not depend on Qt. The GUI consumes Runtime Events and invokes the existing Agent, so the CLI, tests and other integrations can reuse the harness independently.

## Tests & CI / 测试与持续集成

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests eval
```

当前本地完整回归结果为 **198 tests passed，1 skipped**；跳过项是 Windows 环境缺少创建符号链接权限，不是功能失败。

The latest local regression run reports **198 tests passed and 1 skipped**. The skipped case requires Windows symlink privileges and is not a product failure.

GitHub Actions 在 Ubuntu 与 Windows 上分别使用 Python 3.10、3.13，执行 editable install、完整 Unit Tests、语法编译和包版本一致性检查。测试覆盖 Agent Loop、Tool Protocol、Context、Reliability、Command Policy、Verification、Evaluation、Session、Evidence、GUI Worker 与 CLI 兼容性。

GitHub Actions runs editable installation, the full unit suite, syntax compilation and package-version checks on Ubuntu and Windows with Python 3.10 and 3.13. Coverage includes the agent loop, tool protocol, context, reliability, command policy, verification, evaluation, sessions, evidence, GUI worker and CLI compatibility.

## Design Evolution / 设计演进

![Development Evolution](./06Development_Evolution.png)

项目不是一次性堆出最终形态，而是沿着可验证的能力边界逐步演进：

The project evolved through testable capability boundaries instead of appearing as one monolithic implementation:

```text
INIT → CHAT → SEE → EDIT → EXECUTE → ITERATE → CONTROL → VERIFY
```

| Stage | Capability / 能力 |
| --- | --- |
| 0 | Project initialization and CLI skeleton / 项目初始化与 CLI 骨架 |
| 1 | LLM chat and conversation / LLM 对话与会话状态 |
| 2 | Read-only local observation / 只读本地观察 |
| 3 | Bounded file editing / 有边界的文件编辑 |
| 4 | Local command execution and feedback / 本地命令执行与反馈 |
| 5 | Autonomous observation-action loop / 自主观察—行动循环 |
| 6 | Context, reliability and Runtime safety / 上下文、可靠性与运行时安全 |
| 7 | Verification and independent evaluation / 完成验证与独立评估 |
| 8–9 | Observable GUI, Evidence, approval, streaming and durable sessions / 可观察 GUI、证据、授权、流式交互与持久会话 |

更完整的设计权衡、协议和安全边界见 [`DESIGN.md`](./DESIGN.md)。

See [`DESIGN.md`](./DESIGN.md) for detailed trade-offs, protocols and safety boundaries.

## Limitations / 当前限制

- 不提供 OS-level sandbox；允许的命令继承当前用户权限。<br>
  No OS-level sandbox; allowed commands inherit current-user privileges.
- 不支持交互式 Shell、持久 Shell Session 或后台进程。<br>
  No interactive shell, persistent shell session or background process support.
- 不包含语义长期记忆、RAG 或向量数据库。<br>
  No semantic long-term memory, RAG or vector store.
- Verification 是工程证据门，不是正确性的形式化证明。<br>
  Verification is an engineering evidence gate, not a formal proof of correctness.
- 独立评估集只有 6 个小型任务，结果依赖模型、配置和具体运行。<br>
  The independent evaluation contains only six small tasks and remains model- and run-dependent.

## License / 许可证

当前仓库尚未添加明确的开源许可证。在添加 `LICENSE` 文件之前，请不要假定代码已被授予复制、修改或分发权限。

No explicit open-source license has been added yet. Until a `LICENSE` file is provided, do not assume permission to copy, modify or redistribute the code.
