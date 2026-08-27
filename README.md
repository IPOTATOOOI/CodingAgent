# Mini Coding Agent

A lightweight coding agent implemented from scratch.

## Current Status

Stage 1: Basic LLM conversation.

The project currently supports:

- CLI interaction
- OpenAI-compatible LLM APIs
- Multi-turn text conversation
- Environment-based model configuration

It does not yet support:

- File access
- Tool calling
- Command execution
- An autonomous agent loop

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
python -m coding_agent
```

or:

```bash
coding-agent
```

Example session:

```text
Mini Coding Agent
Stage 1 - LLM conversation

Type your message.
Type /exit to quit.

> What is dynamic programming?
<assistant response>
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
