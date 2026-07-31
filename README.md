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
GROQ_API_KEY=gsk_your_groq_api_key
GROQ_MODEL=groq/llama-3.3-70b-versatile
PROJECT_ROOT=src/web/workspace
ALLOWED_FILES=index.html,style.css
PATCH_MODE=automatic
SYNTAX_VALIDATION_ENABLED=true
```

### 4. Run the Agent REPL

```bash
python -m web.main
```

### 5. Run Test Suite

```bash
python -m pytest tests/ -v
```

For full architecture details, per-turn execution flows, safety guarantees, and configuration guides, please refer to [web/README.md](web/README.md).
