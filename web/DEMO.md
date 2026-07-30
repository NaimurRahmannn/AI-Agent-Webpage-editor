# Supervisor Demonstration Script (DEMO.md) — Phase 10

This document provides a supervisor-ready, step-by-step demonstration walkthrough for the **CrewAI Conversational HTML/CSS Editing Agent with Patch Preview, Deterministic Syntax Validation, and Safe Undo**.

---

## 1. Preparation Checklist

```bash
# Navigate to web directory
cd web

# Set PATCH_MODE=preview in .env or environment
export PATCH_MODE=preview

# Verify test suite passes
python -m pytest tests/ -v
```

---

## 2. Live Demonstration Workflow

Launch the agent REPL:

```bash
python -m web.main
```

---

### Step 1: System Status Inspection (`:status`)

**Input:**
```text
web-editor> :status
```

**Expected Output:**
```text
System Status
-------------
Patch mode: preview
Syntax validation: enabled
  - HTML validation: enabled
  - CSS validation: enabled
Groq: configured
Gemini CLI patch reviewer: enabled
Clarification pending: False
Preview pending: none
Successful turns retained: 0/5
Last edited file: none
```

---

### Step 2: Edit Instruction in Preview Mode

**Input:**
```text
web-editor> Change the title heading to Weft Studio Platform
```

**Expected Output:**
```text
Reread 2 configured file(s) from disk (2842 characters).
Running locator and editor agents...

Preview ready for: index.html
Summary: Change title heading to Weft Studio Platform
Syntax validation: HTML syntax valid

Diff:
--- a/index.html
+++ b/index.html
@@ -12,1 +12,1 @@
-  <h1>Weft Studio</h1>
+  <h1>Weft Studio Platform</h1>

Type ':apply' to apply this patch, or ':cancel' to discard it.

preview> 
```

**Supervisor Note**: Observe that prompt changed to `preview> `, the diff is displayed, syntax was validated, but no source file or `.bak` backup file was written yet.

---

### Step 3: Committing the Preview Patch (`:apply`)

**Input at `preview>` prompt:**
```text
preview> :apply
```

**Expected Output:**
```text
Applied preview: Change title heading to Weft Studio Platform
File: index.html
Backup: index.html.bak

Diff:
--- a/index.html
+++ b/index.html
@@ -12,1 +12,1 @@
-  <h1>Weft Studio</h1>
+  <h1>Weft Studio Platform</h1>

web-editor> 
```

**Supervisor Note**: File `index.html` has now been modified, `index.html.bak` backup was created, and prompt returned to `web-editor> `.

---

### Step 4: Safe Recovery with Undo (`:undo`)

**Input:**
```text
web-editor> :undo index.html
```

**Expected Output:**
```text
Undo completed: Restored index.html from index.html.bak
File: index.html
New backup: index.html.bak

Reverse Diff:
--- a/index.html
+++ b/index.html
@@ -12,1 +12,1 @@
-  <h1>Weft Studio Platform</h1>
+  <h1>Weft Studio</h1>

web-editor> 
```

**Supervisor Note**: The previous `index.html` source was restored, a reverse diff was rendered, and the pre-undo source was safely preserved in the backup rotation.

---

## 3. Post-Demo Workspace Restoration

```bash
# Clean created backups and reset workspace
rm -f src/web/workspace/*.bak*
git checkout src/web/workspace/
```

---

## 4. Final Project Acceptance Checklist

- [x] **Deterministic Syntax Validation**: `html5lib` and `tinycss2` validate complete resulting HTML/CSS files before backup or write.
- [x] **Patch Preview Mode**: `PATCH_MODE=preview` holds pending preview transactions without touching disk until `:apply`.
- [x] **Safe Undo**: `:undo` restores allowlisted files from rotating backups using atomic replacement and reverse diffs.
- [x] **Colon Commands**: `:status`, `:preview`, `:apply`, `:cancel`, `:undo` operate cleanly.
- [x] **100% Test Coverage**: All 180+ unit and integration tests pass deterministically.
- [x] **Documentation**: Complete `README.md` and supervisor `DEMO.md`.
