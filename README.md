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
CREWAI_TRACING_ENABLED=false

# Gemini CLI read-only patch reviewer settings
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

### Optional: Run with Docker

From the repository root:

```bash
docker build -t ai-agent-web-editor ./web
docker run --rm -it --env-file web/.env -v "$PWD/web/src/web/workspace:/app/src/web/workspace" ai-agent-web-editor
```

Run the test suite in Docker:

```bash
docker build --target test -t ai-agent-web-editor-test ./web
docker run --rm ai-agent-web-editor-test
```

## Known Limitations

- The editor only modifies allowlisted local HTML and CSS files; JavaScript, backend code, databases, and framework-wide changes are outside its scope.
- Each request produces one targeted replacement in one file. Large redesigns must be split into smaller instructions.
- Successful edits depend on the Locator and Editor agents returning consistent, exact source metadata. Ambiguous or mismatched output is rejected safely, so a request may need to be reworded more precisely.
- Preview mode displays a text diff; it does not provide a live rendered browser preview or visual regression testing.
- Backups and session memory are local and limited by configuration. They are not a replacement for Git history.
- LLM editing requires internet access and an available Groq API. This project currently uses a free-tier Groq API key, whose request or token quota can be exhausted. When Groq returns a rate-limit or quota error, new LLM-powered edits must wait for the quota to reset or use a key with additional capacity; local commands such as `:status`, `:undo`, and pending-preview controls remain available.
- The optional Gemini reviewer also depends on a separately installed CLI, credentials, network availability, and its provider quota.

For full architecture details, per-turn execution flows, safety guarantees, and configuration guides, please refer to [web/README.md](web/README.md).
