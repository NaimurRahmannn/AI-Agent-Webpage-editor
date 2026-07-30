# Supervisor Demonstration Script (DEMO.md)

This document provides a supervisor-ready, step-by-step demonstration walkthrough for the **CrewAI Conversational HTML/CSS Editing Agent**.

---

## 1. Preparation Checklist

Before launching the demonstration, ensure the environment is configured and dependencies are installed:

```bash
# Navigate to web directory
cd web

# Verify Python version (>=3.10)
python --version

# Verify environment file exists and GROQ_API_KEY is configured
cat .env

# Verify clean workspace state (no lingering backup files)
git status src/web/workspace/
```

---

## 2. Architecture Quick Summary for Supervisor

| System Component | Responsibility |
| :--- | :--- |
| **Terminal REPL (`session.py`)** | Interactive prompt, history rendering, user status commands. |
| **Orchestration Core (`orchestration.py`)** | Coordinates source rereading, memory injection, crew execution, validation, and patcher calls. |
| **CrewAI Locator Agent (`crew.py`)** | Identifies file, target element/selector, and exact source snippet (`LocatorResult`). |
| **CrewAI Editor Agent (`crew.py`)** | Uses locator task output to create exact `old_text` and `new_text` patch (`ProposedPatch`). |
| **Deterministic Patcher (`patcher.py`)** | Validates exact 1-match criteria, path allowlisting, creates `.bak` backups, and executes atomic file writes. |
| **Session Memory (`state.py`)** | Holds bounded recent successful turns to enable referential follow-ups ("make it darker"). |

---

## 3. Automated Test Verification

Run the full automated test suite (125+ unit & integration tests, 100% deterministic with mocked LLM calls):

```bash
uv run pytest tests/ -v
```

Expected Output:
`125+ passed` (with 0 failures).

---

## 4. Live Demonstration Workflow

Launch the live interactive terminal session:

```bash
uv run web
```

---

### Step 1: Direct HTML Editing

**Demonstrating direct HTML modification.**

**Input Prompt:**
```text
web> Change the main hero heading text to "Ship extraordinary products."
```

**Expected Behavior:**
- Agent identifies `src/web/workspace/index.html`.
- Agent extracts exact `<h1>` tag.
- Unified diff printed to terminal showing deleted `- <h1>...</h1>` and added `+ <h1>...</h1>`.
- `index.html.bak` backup file created automatically in workspace.

---

### Step 2: Direct CSS Editing

**Demonstrating direct CSS styling modification.**

**Input Prompt:**
```text
web> Change the CTA button background color to #2d6a4f
```

**Expected Behavior:**
- Agent identifies `src/web/workspace/style.css`.
- Agent targets `.cta` rule `background: #ff0000;`.
- Unified diff shows replaced declaration.
- `style.css.bak` created in workspace.

---

### Step 3: Conversational Follow-Up Edit

**Demonstrating memory and referential context.**

**Input Prompt:**
```text
web> Make that button color even darker green, like #1b4332
```

**Expected Behavior:**
- Agent resolves "that button color" using `SessionState` memory (`LastTarget`).
- Sources are reread directly from disk (reflecting the edit from Step 2).
- Diff shows update from `#2d6a4f` to `#1b4332`.

---

### Step 4: Rejection of Ambiguous Instruction

**Demonstrating safe refusal of ambiguous requests.**

**Input Prompt:**
```text
web> Make the text bigger
```

**Expected Behavior:**
- Agent detects multiple text elements (headings, paragraphs, buttons).
- Agent refuses edit with `status: ambiguous` and asks user to specify which element to change.
- No files are modified; no backup created.

---

### Step 5: Rejection of Unsupported Scope (JavaScript / Multi-File)

**Demonstrating boundary enforcement.**

**Input Prompt:**
```text
web> Add a JavaScript click event listener to the CTA button and change CSS padding at the same time
```

**Expected Behavior:**
- Agent rejects request with `status: unsupported`.
- Refusal reason explains JavaScript and multi-file edits are strictly out of scope.
- Files remain intact.

---

## 5. Post-Demo Workspace Restoration

To clean up backup files and reset workspace files back to clean repository state:

```bash
# Remove created backup files
rm -f src/web/workspace/*.bak*

# Reset workspace HTML/CSS files
git checkout src/web/workspace/
```

---

## 6. Final Project Acceptance Checklist

- [x] **Architecture**: Long-running CLI REPL with Crews using `Process.sequential`.
- [x] **Strict Models**: `LocatorResult` and `ProposedPatch` inherit from `StrictModel` (`extra = "forbid"`).
- [x] **Single-File Safety**: Exactly one file modified per turn with exact 1-match verification.
- [x] **Deterministic Patcher**: Backup rotation (`.bak`), atomic replacement, unified diffs.
- [x] **Source Reread**: Allowed HTML/CSS files reread on every turn.
- [x] **Conversational Memory**: Bounded session history (`SESSION_HISTORY_LIMIT=5`).
- [x] **Reliability**: Provider errors gracefully classified without leaking API keys.
- [x] **Test Coverage**: Complete suite covering success, rejection, and failure paths.
- [x] **Documentation**: Complete `README.md` and supervisor `DEMO.md`.
