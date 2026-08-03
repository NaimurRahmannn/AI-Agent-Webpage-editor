# AI Agent Webpage Editor

A conversational CLI editing agent built with CrewAI, Python, Pydantic, Groq, deterministic HTML/CSS syntax validation (`html5lib` & `tinycss2`), patch preview mode, safe undo, interactive clarification, and an embedded read-only Gemini CLI patch reviewer.

---

## Quick Navigation

- [Complete Project Documentation](web/README.md)
- [Demonstration Script (DEMO.md)](web/DEMO.md)
- [CrewAI Framework Guidelines (AGENTS.md)](web/AGENTS.md)

---

## Quick Start

### 1. Navigate to the `web` project directory

```bash
cd web
```

### 2. Environment Setup & Installation

Using `uv` (recommended):

```bash
uv sync
```

Or using standard `pip`:

```bash
python -m venv .venv
# Activate virtual environment
source .venv/bin/activate  # On Linux/macOS
# .venv\Scripts\activate   # On Windows

pip install -e .
```

### 3. Environment Variables

Create `.env` inside the `web` directory:

```env
# Groq API configuration (Required)
GROQ_API_KEY=gsk_replace_with_your_actual_groq_api_key
GROQ_MODEL=groq/llama-3.3-70b-versatile

# Workspace & session settings
PROJECT_ROOT=src/web/workspace
ALLOWED_FILES=["index.html","style.css"]
BACKUP_LIMIT=3
SESSION_HISTORY_LIMIT=5

# Gemini CLI read-only patch reviewer settings (Phase 8)
GEMINI_API_KEY=
GEMINI_CLI_ENABLED=false
GEMINI_CLI_MODEL=flash
GEMINI_CLI_TIMEOUT_SECONDS=60
GEMINI_CLI_MAX_OUTPUT_CHARS=20000

# Preview mode and syntax validation settings
PATCH_MODE=automatic
SYNTAX_VALIDATION_ENABLED=true
HTML_VALIDATION_ENABLED=true
CSS_VALIDATION_ENABLED=true
```

### 4. Run the Agent REPL

```bash
python -m web.main
```

### 5. Run Test Suite

```bash
./scripts/test.sh -v
```

For full architecture details, per-turn execution flows, safety guarantees, and configuration guides, please refer to [web/README.md](web/README.md).
