# CrewAI Conversational HTML/CSS Editing Agent

A specialized, local-first conversational CLI editing agent built with Python, CrewAI, Pydantic, Groq, and an embedded read-only Gemini CLI patch reviewer. The agent accepts natural language editing requests in a long-running terminal session and applies exact, single-file HTML or CSS modifications deterministically.

---

## Table of Contents

- [Overview](#overview)
- [Scope & Exclusions](#scope--exclusions)
- [Architecture](#architecture)
- [Gemini CLI Patch Reviewer (Phase 8)](#gemini-cli-patch-reviewer-phase-8)
- [Per-Turn Execution Flow](#per-turn-execution-flow)
- [Repository Structure](#repository-structure)
- [Requirements](#requirements)
- [Setup & Installation](#setup--installation)
- [Environment Configuration](#environment-configuration)
- [Model Selection](#model-selection)
- [Usage](#usage)
- [Conversational Editing & Memory](#conversational-editing--memory)
- [Safety Guarantees](#safety-guarantees)
- [Testing](#testing)
- [Restoring Workspace](#restoring-workspace)
- [Troubleshooting](#troubleshooting)

---

## Overview

The **CrewAI Conversational HTML/CSS Editing Agent** provides a terminal REPL loop where users interactively edit allowed workspace HTML and CSS files using plain English.

Unlike unconstrained code-generation assistants, this project operates under strict safety and determinism boundaries:
- **Two-Agent Crew**: A **Locator** agent finds the target file and exact source substring; an **Editor** agent produces a single precise replacement patch.
- **Embedded Read-Only Gemini Reviewer**: An optional Gemini CLI tool reviews candidate patches in read-only plan mode before editor confirmation.
- **Deterministic Validation & Execution**: Python validates all structural and path constraints, generates rotating backups (`.bak`), applies atomic writes, and renders unified diffs.
- **Strict Structured Outputs**: Pydantic models strictly enforce data shapes and prohibit arbitrary LLM text formats.
- **Local-First & Reread Source**: Workspace HTML/CSS files are reread directly from disk on every single turn to maintain the true file state.

---

## Scope & Exclusions

### In-Scope Capabilities
- Direct single-turn HTML edits (text content, attribute values, tags).
- Direct single-turn CSS edits (property values, selectors, declarations).
- Conversational follow-up requests ("Make it bigger", "Change the same button again", "Even darker").
- Advisory read-only patch reviewing via embedded Gemini CLI (`--approval-mode plan`).
- Rejection of ambiguous, out-of-scope, or dangerous instructions with clear user feedback.
- Automatic rotating file backup creation (`index.html.bak`, `index.html.bak.1`).
- Unified diff output display for applied edits.

### Explicit Exclusions
- **No JavaScript support**: JavaScript files (`.js`) and inline event handlers are strictly out of scope and rejected.
- **No multi-file edits**: Exactly one file is edited per turn. Requests spanning multiple files are rejected.
- **No visual rendering or browser automation**: Execution is terminal-based without browser drivers or DOM layout calculation.
- **No unconstrained code generation**: All edits must match existing source content exactly.
- **No filesystem writes by Gemini CLI**: Gemini CLI operates purely in read-only plan mode. Python is the sole component that performs file backups and writes.

---

## Architecture

The project follows a modular, pipeline-based design:

```mermaid
flowchart TD
    User([User Terminal REPL]) -->|Natural Language Prompt| Session[Session Loop / REPL]
    Session -->|Reread Files + Build Memory Context| Orchestration[Orchestration Core]
    Orchestration -->|Execute Sequential Crew| Crew[CrewAI Process]
    
    subgraph CrewAI Sequential Process
        Locator[Locator Agent] -->|LocatorResult| Editor[Editor Agent]
        Editor -->|Candidate Patch| GeminiTool[Gemini CLI Reviewer Tool]
        GeminiTool -->|--approval-mode plan via stdin| GeminiCLI[Embedded Gemini CLI Subprocess]
        GeminiCLI -->|Advisory GeminiReviewResult| GeminiTool
        GeminiTool -->|Verdict Feedback| Editor
        Editor -->|ProposedPatch| Validation[Structured Output Validation]
    end

    Crew --> Validation
    Validation -->|Passes Strict Rules| Patcher[Deterministic Patcher]
    Validation -->|Rejected / Ambiguous| Session
    
    Patcher -->|1. Validate Source Snapshot| Disk[(Workspace Files)]
    Patcher -->|2. Create Rotating Backup| Disk
    Patcher -->|3. Atomic Text Replace| Disk
    Patcher -->|4. Return Unified Diff| Session
    Patcher --> RecordMemory[Update Session State Memory]
```

---

## Gemini CLI Patch Reviewer (Phase 8)

### Responsibilities: Groq vs Gemini CLI
- **Groq**: Powers the primary CrewAI agents (**Locator** and **Editor**) responsible for analyzing natural language and formulating candidate patches.
- **Gemini CLI**: Serves as an optional, secondary **read-only patch reviewer** attached to the Editor agent.

### Read-Only Plan Mode Guarantee
When `GEMINI_CLI_ENABLED=true`, the Editor agent invokes the `GeminiCliReviewTool`. The tool executes the Gemini CLI using:
```bash
gemini --model <configured model> --approval-mode plan --output-format json --prompt <fixed prompt>
```
- `--approval-mode plan`: Forces Gemini CLI to operate strictly in read-only planning mode. It cannot apply edits or execute system actions.
- **Subprocess Safety**:
  - Executed with `shell=False`.
  - Binary path located strictly via `shutil.which("gemini")`.
  - Dynamic review data passed safely through `stdin`.
  - Minimal environment isolation (only `PATH`, `HOME`/`USERPROFILE`, `TMP`, and `GEMINI_API_KEY`).
  - Stderr and secret keys are never printed or leaked.

### Failure & Fallback Behavior
- If Gemini CLI is missing from `PATH`, times out, or fails, the tool returns `verdict: "unavailable"`.
- If the reviewer returns `unsafe`, the Editor rejects the instruction.
- If Gemini CLI is disabled (`GEMINI_CLI_ENABLED=false`), the Editor operates directly as in Phase 7 without tool attachment.
- **Python remains the single, authoritative writer.** Gemini CLI never writes to disk.

---

## Per-Turn Execution Flow

1. **Input Collection**: User enters an instruction at the REPL prompt (`web> `).
2. **Fresh Source Read**: Python reads all allowlisted HTML/CSS files directly from disk.
3. **Memory Context Assembly**: Recent successful edits and target metadata (file, selector, property) are formatted into a context string.
4. **Crew Execution**:
   - **Locator Task**: Identifies target file, selector/property, and exact source snippet (`LocatorResult`).
   - **Editor Task**: Receives locator context, formulates candidate patch, optionally requests Gemini CLI review (`GeminiCliReviewTool`), and produces final `ProposedPatch`.
5. **Python Validation**:
   - Verify task completion and Pydantic structured output.
   - Verify allowlisted file paths.
   - Verify `old_text` exists **exactly once** in the current file source.
   - Verify source snapshot has not changed.
6. **Safe File Modification**:
   - Create rotating backup (`filename.bak`, `filename.bak.1`).
   - Perform atomic write to target file.
   - Generate and print unified diff.
7. **Session State Record**: Record turn metadata for downstream follow-up awareness.

---

## Repository Structure

```
.
├── src/
│   └── web/
│       ├── __init__.py
│       ├── main.py            # CLI entry point (`web`)
│       ├── session.py         # Terminal REPL loop
│       ├── orchestration.py   # Turn processing and crew execution wrapper
│       ├── crew.py            # CrewAI Agents and Tasks configuration
│       ├── models.py          # Pydantic schemas (LocatorResult, ProposedPatch)
│       ├── state.py           # SessionState memory and history limit
│       ├── settings.py        # Environment & pydantic-settings config
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── gemini_cli_tool.py # Read-only Gemini CLI patch reviewer tool
│       │   └── patcher.py     # Deterministic backup, diff & atomic replace
│       └── workspace/         # Default target web workspace
│           ├── index.html
│           └── style.css
├── tests/
│   ├── test_crew.py
│   ├── test_gemini_cli_integration.py # Phase 8 integration tests
│   ├── test_gemini_cli_tool.py        # Phase 8 tool & settings tests
│   ├── test_integration.py   # End-to-end mocked integration tests
│   ├── test_models.py
│   ├── test_orchestration.py
│   ├── test_patcher.py
│   ├── test_reliability.py
│   ├── test_session.py
│   ├── test_settings.py
│   ├── test_state.py
│   └── test_validation.py
├── .env.example               # Template environment configuration
├── pyproject.toml             # Project setup and dependencies
├── DEMO.md                    # Supervisor demonstration script
└── README.md                  # Project documentation
```

---

## Requirements

- **Python**: `>=3.10, <3.14` (Python 3.11 or 3.12 recommended).
- **Package Manager**: [`uv`](https://github.com/astral-sh/uv) (recommended) or standard `pip`.
- **Primary LLM API**: Groq API Key (`GROQ_API_KEY`).
- **Optional Patch Reviewer**: Gemini CLI (`npm install -g @google/gemini-cli`) and `GEMINI_API_KEY`.

---

## Setup & Installation

1. **Navigate to the web project directory**:
   ```bash
   cd web
   ```

2. **Install Python dependencies using `uv`**:
   ```bash
   uv sync
   ```

3. **(Optional) Install Gemini CLI**:
   ```bash
   npm install -g @google/gemini-cli
   gemini --version
   ```

---

## Environment Configuration

Create a `.env` file in the `web/` directory based on `.env.example`:

```bash
cp .env.example .env
```

Configure your `.env` variables:

```env
# Groq configuration (Required)
GROQ_API_KEY=gsk_your_actual_groq_api_key_here
GROQ_MODEL=groq/llama-3.3-70b-versatile
PROJECT_ROOT=src/web/workspace
ALLOWED_FILES=index.html,style.css
BACKUP_LIMIT=3
SESSION_HISTORY_LIMIT=5

# Gemini CLI read-only patch reviewer settings (Optional)
GEMINI_API_KEY=your_actual_gemini_api_key_here
GEMINI_CLI_ENABLED=true
GEMINI_CLI_MODEL=flash
GEMINI_CLI_TIMEOUT_SECONDS=60
GEMINI_CLI_MAX_OUTPUT_CHARS=20000
```

---

## Model Selection

- **Primary Crew LLM (Groq)**: `groq/llama-3.3-70b-versatile` (or `groq/llama3-70b-8192`).
- **Reviewer LLM (Gemini CLI)**: `flash` (or `gemini-1.5-flash`).

---

## Usage

### Running the Terminal REPL

Launch the agent with `uv`:

```bash
uv run web
```

Or run via python module:

```bash
uv run python -m web.main
```

### Running Without Gemini CLI

To run purely with the two CrewAI agents without Gemini CLI reviewing, set:
```env
GEMINI_CLI_ENABLED=false
```

---

## Safety Guarantees

1. **Path Traversal Protection**: Only allowlisted relative files (`index.html`, `style.css`) within `PROJECT_ROOT` can be accessed or modified.
2. **Read-Only Reviewer**: Gemini CLI runs strictly with `--approval-mode plan` over `stdin`.
3. **Subprocess Isolation**: Subprocess uses `shell=False` and clean environment dictionary.
4. **Exact Single Match**: `old_text` must appear **exactly once** in the target file.
5. **Rotating Backups**: File backups (`.bak`, `.bak.1`) are created prior to writing changes.
6. **Atomic File Write**: Files are written to a temporary file before atomic replacement.
7. **No Secret Leaks**: Provider and CLI errors redact API keys and sensitive tokens.

---

## Testing

The project contains unit and integration tests with **100% mocked LLM and subprocess calls** (no API usage or network dependencies).

Run all tests:

```bash
uv run pytest tests/ -v
```

Run Phase 8 tests only:

```bash
uv run pytest tests/test_gemini_cli_tool.py tests/test_gemini_cli_integration.py -v
```

---

## Restoring Workspace

To reset workspace files to clean initial states after testing or running demo turns:

```bash
git checkout src/web/workspace/
rm -f src/web/workspace/*.bak*
```

---

## Troubleshooting

| Problem | Cause | Resolution |
| :--- | :--- | :--- |
| `GROQ_API_KEY is not set` | Missing `.env` or invalid key | Ensure `.env` exists in `web/` directory with a valid `GROQ_API_KEY`. |
| `GEMINI_API_KEY is missing when GEMINI_CLI_ENABLED is true` | Feature enabled without key | Set `GEMINI_API_KEY` in `.env` or set `GEMINI_CLI_ENABLED=false`. |
| `Gemini CLI executable ('gemini') not found in PATH` | Gemini CLI not installed | Install with `npm install -g @google/gemini-cli` or set `GEMINI_CLI_ENABLED=false`. |
| `patch file must be one of allowlisted workspace files` | Request targeted a non-allowlisted file | Check `ALLOWED_FILES` setting or request edits only to `index.html` or `style.css`. |
| `old_text matched 0 times` | Source file was modified externally | Verify file content; re-issue instruction relative to current source. |
