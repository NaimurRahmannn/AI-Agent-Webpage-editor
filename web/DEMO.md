# Supervisor Demonstration Script (DEMO.md) — Phase 9

This document provides a supervisor-ready, step-by-step demonstration walkthrough for the **CrewAI Conversational HTML/CSS Editing Agent with Conversational Clarification Workflows**.

---

## 1. Preparation Checklist

```bash
# Navigate to web directory
cd web

# Verify clean workspace state (no backup files)
git status src/web/workspace/

# Verify test suite
uv run pytest tests/ -v
```

---

## 2. Architecture & Workflow Overview for Supervisor

| Stage | Action | Safety Guarantee |
| :--- | :--- | :--- |
| **Initial Ambiguous Turn** | Locator identifies multiple link/button targets and returns `ClarificationRequest`. | **No source changes. No backup files. No Gemini CLI calls.** |
| **Clarification Prompt** | System displays numbered candidate options and switches prompt to `clarify> `. | Process-local state. Bounded to 3 attempts. `cancel` available. |
| **User Selection** | User types option number (`5`) or label (`CTA link`). | Selection is advisory. Python rereads current files fresh from disk. |
| **Locator Rerun** | Locator relocates selected target in the latest source files. | Locator must return `status: located` before patch generation. |
| **Gemini Review** | Advisory Gemini CLI review runs in read-only plan mode (`--approval-mode plan`). | Reviewer is read-only. Python remains sole file writer. |
| **Patcher Execution** | Python validates 1-match criteria, creates `.bak` backup, applies atomic write, and prints unified diff. | Memory updated only after atomic write succeeds. |

---

## 3. Live Demonstration Workflow

Launch the agent REPL:

```bash
uv run web
```

---

### Step 1: Ambiguous Instruction & Clarification Prompt

**Input Prompt:**
```text
web-editor> Change the link text.
```

**Expected Output:**
```text
Which link should I change?
1. Brand link: Weft Studio
2. Navigation link: Work
3. Navigation link: About
4. CTA link: Start a project

Enter an option number or target label.
Type 'cancel' to cancel this clarification.

clarify> 
```

**Supervisor Note**: Observe that the prompt changed to `clarify> ` and no source files or backups were created.

---

### Step 2: Selecting an Option & Deterministic Patch Application

**Input at `clarify>` prompt:**
```text
clarify> 4
```

**Expected Output:**
```text
Resolved clarification:
  Original request: Change the link text.
  Selected target: CTA link: Start a project

Rereading current files and running the editing crew...

Applied: Change the CTA link text to Contact Us.
File: index.html
Backup: index.html.bak

Diff:
--- a/index.html
+++ b/index.html
@@ -19,1 +19,1 @@
-      <a class="cta" href="#contact">Start a project</a>
+      <a class="cta" href="#contact">Contact Us</a>
```

---

### Step 3: Cancelling a Clarification Request

**Input Prompt:**
```text
web-editor> Make the text bigger.
```

**Output:**
```text
Which element should I change?
1. Hero heading text
2. Hero copy text
3. CTA button text

clarify> cancel

Clarification cancelled.

web-editor> 
```

**Supervisor Note**: Typing `cancel` clears pending clarification and safely returns prompt to `web-editor> `.

---

### Step 4: Rejection of Unsupported Request (No Clarification)

**Input Prompt:**
```text
web-editor> Add an onclick listener to the CTA link
```

**Expected Output:**
```text
Unsupported request: JavaScript changes are unsupported.
Summary: JavaScript changes are unsupported.
No source files were changed.
```

**Supervisor Note**: Unsupported requests (JavaScript, broad redesigns, multi-file edits) are rejected directly and never become clarification flows.

---

## 4. Post-Demo Workspace Restoration

```bash
# Clean created backups and reset workspace
rm -f src/web/workspace/*.bak*
git checkout src/web/workspace/
```

---

## 5. Final Project Acceptance Checklist

- [x] **Clarification State**: Process-local `ClarificationManager` handles pending clarifications with bounded attempts.
- [x] **No Writes During Ambiguity**: Source files, backups, and memory remain unchanged until target is resolved.
- [x] **Fresh Reread & Rerun**: Option selection triggers fresh source reread and reruns locator/editor pipeline.
- [x] **Gemini Review Timing**: Read-only Gemini review runs ONLY after a unique target is located and candidate patch formulated.
- [x] **Single File Writer**: Python patcher remains the sole file writer and backup creator.
- [x] **Boundary Preservation**: JavaScript, broad redesigns, multi-file requests remain `unsupported`.
- [x] **Test Coverage**: 100% mocked unit and integration tests (26 Phase 9 tests, 151 total repository tests).
- [x] **Documentation**: Complete `README.md` and supervisor `DEMO.md`.
