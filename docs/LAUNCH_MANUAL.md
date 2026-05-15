# Launch & Test Manual — RAG Runtime Kernel v3.2

Comprehensive setup instructions for every supported platform and mode.

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [AUTONOMOUS Mode (Prompt-Only)](#autonomous-mode-prompt-only)
   - [Claude Projects (Web)](#1-claude-projects-web)
   - [GPT Projects (Web)](#2-gpt-projects-web)
   - [Any LLM with File Upload](#3-any-llm-with-file-upload)
4. [ENFORCED Mode (Prompt + Runtime)](#enforced-mode-prompt--runtime)
   - [Claude Desktop (MCP)](#4-claude-desktop-mcp-mode)
   - [Claude Code CLI](#5-claude-code-cli)
   - [Claude Code in VS Code](#6-claude-code-in-vs-code)
   - [GPT Chat (HTTP via Custom Actions)](#7-gpt-chat-http-mode-via-custom-actions)
   - [Any LLM with HTTP Access](#8-any-llm-with-http-access)
5. [Testing](#testing)
6. [API Quick Reference](#api-quick-reference)
7. [MCP Tools Reference](#mcp-tools-reference)
8. [Troubleshooting](#troubleshooting)

---

## Overview

The RAG Runtime Kernel is a filesystem-backed, event-sourced, prompt-controlled project memory system for LLMs. It has two components:

- **Init prompt** (`INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.x.md`) — loaded into LLM context to govern behavior.
- **Python runtime** (`src/rag_kernel/`) — 8 modules providing external enforcement: `state_machine`, `persistence`, `cold_manager`, `concurrency`, `api`, `mcp_transport`, `schemas`, `__main__`.

Two operating modes:

| Mode | What runs | Who enforces rules | Python required? |
|------|-----------|-------------------|-----------------|
| **AUTONOMOUS** | Init prompt only | The LLM self-enforces | No |
| **ENFORCED** | Init prompt + Python kernel | The kernel enforces via API/MCP | Yes |

---

## Prerequisites

**AUTONOMOUS mode:** No installation. You need only the init prompt file and a `RAG_MASTER.json`.

**ENFORCED mode:**
- Python 3.10 or later
- The `rag-runtime-kernel` repository cloned locally
- A RAG project directory containing `RAG_MASTER.json`
- Zero external dependencies (stdlib only)

---

## AUTONOMOUS Mode (Prompt-Only)

No Python. No server. The LLM reads the init prompt and self-enforces all RAG Kernel rules (state machine, proposals, checkpoints, etc.) using only its own context and file access.

### 1. Claude Projects (Web)

1. Go to [claude.ai](https://claude.ai) and click **Projects** in the left sidebar.
2. Click **Create project**.
3. Give it a name (e.g., "My Project — RAG Kernel").
4. Open the project and click the **gear icon** (Project Settings).
5. In **Project Instructions**, paste the entire contents of `INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.x.md`.
6. Click **Add content** (the paperclip/file icon) and upload your `RAG_MASTER.json` as a project file.
   - If you also have `RAG_COLD.json`, upload that too.
7. Start a new conversation inside the project.
8. The LLM will auto-detect the init prompt and boot the RAG Kernel in autonomous mode.
9. Verify by asking: *"What is the current kernel state?"* — it should report `READY`.

**How it works:** Claude reads `RAG_MASTER.json` from the project files as HOT memory. All mutations go through the propose/commit cycle enforced by the prompt. Claude writes updated JSON back to the project file on checkpoint.

### 2. GPT Projects (Web)

1. Go to [chatgpt.com](https://chatgpt.com) and click **Explore GPTs** or **My GPTs**.
2. Click **Create a GPT** (or edit an existing one).
3. Go to the **Configure** tab.
4. In **Instructions**, paste the entire contents of `INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.x.md`.
5. Under **Knowledge**, click **Upload files** and add your `RAG_MASTER.json`.
   - If you also have `RAG_COLD.json`, upload that too.
6. Click **Save** (choose "Only me" or "Anyone with a link").
7. Open a conversation with your GPT.
8. Verify by asking: *"What is the current kernel state?"* — it should report `READY`.

**Note:** GPT file access is read-only in the Knowledge section. The LLM will track mutations in-context and output updated JSON for you to re-upload when needed.

### 3. Any LLM with File Upload

This works with any LLM that supports system prompts and file attachments (Gemini, local models via Open WebUI, etc.).

1. Set the system prompt / instructions to the full contents of `INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.x.md`.
2. Attach `RAG_MASTER.json` to the conversation (upload or paste).
3. Optionally attach `RAG_COLD.json` for COLD partition access.
4. Begin the conversation. The LLM will operate under RAG Kernel rules.
5. Verify: ask for kernel status. It should self-report as `READY`.

**Limitations of autonomous mode:**
- No atomic writes — the LLM outputs JSON that you save manually.
- No WAL (write-ahead log) — crash recovery depends on conversation history.
- No file locking — concurrent sessions are not protected.
- No hash verification — integrity checks are best-effort.

For full enforcement, use ENFORCED mode below.

---

## ENFORCED Mode (Prompt + Runtime)

The Python kernel runs as an HTTP server or MCP server. The LLM calls the kernel's API/tools. The kernel enforces all state transitions, atomic writes, WAL, hashing, locking, and crash recovery.

### 4. Claude Desktop (MCP Mode)

#### Install

1. Clone the repository:
   ```bash
   git clone https://github.com/YOUR_ORG/rag-runtime-kernel.git
   cd rag-runtime-kernel
   ```

2. Verify Python 3.10+:
   ```bash
   python --version
   ```
   No `pip install` needed — the kernel has zero external dependencies.

#### Configure

3. Open your Claude Desktop config file:
   - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
   - **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Linux:** `~/.config/Claude/claude_desktop_config.json`

4. Add the MCP server entry:
   ```json
   {
     "mcpServers": {
       "rag-kernel": {
         "command": "python",
         "args": [
           "-m", "rag_kernel", "mcp",
           "--project", "C:\\path\\to\\your\\RAG"
         ]
       }
     }
   }
   ```
   Replace `C:\\path\\to\\your\\RAG` with the absolute path to your RAG project directory (the folder containing `RAG_MASTER.json`).

   On macOS/Linux, use forward slashes:
   ```json
   "args": ["-m", "rag_kernel", "mcp", "--project", "/home/user/my-project/RAG"]
   ```

5. If the repo is not installed as a package, set `PYTHONPATH` so Python can find the module:
   ```json
   {
     "mcpServers": {
       "rag-kernel": {
         "command": "python",
         "args": ["-m", "rag_kernel", "mcp", "--project", "C:\\path\\to\\your\\RAG"],
         "env": {
           "PYTHONPATH": "C:\\path\\to\\rag-runtime-kernel\\src"
         }
       }
     }
   }
   ```

6. Restart Claude Desktop.

#### Verify

7. Open a new conversation in Claude Desktop.
8. Claude should show `rag-kernel` in the MCP tools list (hammer icon).
9. The following 11 tools become available:

   | Tool | Description |
   |------|-------------|
   | `rag_boot` | Initialize kernel session |
   | `rag_status` | Get kernel state, session, transitions |
   | `rag_hot` | Read HOT memory (RAG_MASTER.json) |
   | `rag_cold` | Read COLD partitions (full or by key) |
   | `rag_propose` | Submit a mutation proposal |
   | `rag_commit` | Commit a staged proposal |
   | `rag_reject` | Reject a staged proposal |
   | `rag_checkpoint` | Persist state with atomic write + hash |
   | `rag_wal` | Read write-ahead log entries |
   | `rag_recover` | Attempt crash recovery from backup |
   | `rag_close` | Graceful shutdown |

10. Test by asking Claude: *"Boot the RAG kernel and show me the status."*
    Claude will call `rag_boot` then `rag_status` and display the result.

### 5. Claude Code CLI

1. Open your Claude Code MCP configuration:
   ```bash
   claude mcp add rag-kernel -- python -m rag_kernel mcp --project /path/to/your/RAG
   ```

   Or manually edit `~/.claude/settings.json` (global) or `.claude/settings.json` (per-project):
   ```json
   {
     "mcpServers": {
       "rag-kernel": {
         "command": "python",
         "args": ["-m", "rag_kernel", "mcp", "--project", "/path/to/your/RAG"],
         "env": {
           "PYTHONPATH": "/path/to/rag-runtime-kernel/src"
         }
       }
     }
   }
   ```

2. Restart Claude Code or run `claude` in a new terminal.
3. Verify the tools are loaded:
   ```
   > /mcp
   ```
   You should see `rag-kernel` listed with 11 tools.

4. Test:
   ```
   > Boot the RAG kernel and show status.
   ```

### 6. Claude Code in VS Code

1. Open VS Code with the Claude Code extension installed.
2. Open the Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`).
3. Run **Claude Code: Open Settings**.
4. Navigate to **MCP Servers** and add:
   ```json
   {
     "mcpServers": {
       "rag-kernel": {
         "command": "python",
         "args": ["-m", "rag_kernel", "mcp", "--project", "/path/to/your/RAG"],
         "env": {
           "PYTHONPATH": "/path/to/rag-runtime-kernel/src"
         }
       }
     }
   }
   ```
   Alternatively, create or edit `.claude/settings.json` in your workspace root with the same content.

5. Reload the VS Code window (`Ctrl+Shift+P` -> **Developer: Reload Window**).
6. Open the Claude Code panel and verify the `rag-kernel` tools appear.
7. Test: ask Claude to boot the kernel and read HOT memory.

### 7. GPT Chat (HTTP Mode via Custom Actions)

GPT does not support MCP. Instead, the kernel runs as an HTTP server exposed via a Cloudflare tunnel.

#### Step 1: Start the HTTP server

```bash
cd /path/to/rag-runtime-kernel

python -m rag_kernel serve --project /path/to/your/RAG --port 7437
```

If `rag_kernel` is not on `PYTHONPATH`:
```bash
# Windows
set PYTHONPATH=C:\path\to\rag-runtime-kernel\src
python -m rag_kernel serve --project C:\path\to\your\RAG --port 7437

# macOS/Linux
PYTHONPATH=/path/to/rag-runtime-kernel/src python -m rag_kernel serve --project /path/to/your/RAG --port 7437
```

Expected output:
```
RAG Runtime Kernel serving on http://127.0.0.1:7437
Project: /path/to/your/RAG
Session: S-12345-1715000000
State: READY
Press Ctrl+C to stop.
```

#### Step 2: Expose via Cloudflare Tunnel

Install cloudflared:
```bash
# Windows
winget install cloudflare.cloudflared

# macOS
brew install cloudflared

# Linux
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared
chmod +x cloudflared
sudo mv cloudflared /usr/local/bin/
```

Start the tunnel:
```bash
cloudflared tunnel --url http://localhost:7437
```

Copy the generated URL (e.g., `https://random-words-here.trycloudflare.com`).

#### Step 3: Configure GPT Custom Actions

1. Go to [chatgpt.com](https://chatgpt.com) -> **My GPTs** -> **Create a GPT** (or edit existing).
2. Go to the **Configure** tab.
3. Scroll to **Actions** -> **Create new action**.
4. Set **Authentication** to **None**.
5. Paste the OpenAPI schema below, replacing `YOUR_TUNNEL_URL` with your cloudflared URL:

```yaml
openapi: 3.1.0
info:
  title: RAG Runtime Kernel
  version: 0.1.0
  description: Filesystem-backed LLM memory persistence engine
servers:
  - url: https://YOUR_TUNNEL_URL.trycloudflare.com
paths:
  /status:
    get:
      operationId: getStatus
      summary: Get kernel status (state, session, project path)
      responses:
        '200':
          description: Kernel status
          content:
            application/json:
              schema:
                type: object
  /hot:
    get:
      operationId: getHot
      summary: Read the full HOT memory (RAG_MASTER.json)
      responses:
        '200':
          description: HOT memory contents
          content:
            application/json:
              schema:
                type: object
  /cold:
    get:
      operationId: getColdSummary
      summary: List available COLD partitions with token estimates
      responses:
        '200':
          description: COLD summary
          content:
            application/json:
              schema:
                type: object
  /cold/{partition}:
    get:
      operationId: getColdPartition
      summary: Load a specific COLD partition by key
      parameters:
        - name: partition
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Partition data
          content:
            application/json:
              schema:
                type: object
  /wal:
    get:
      operationId: getWAL
      summary: Read the write-ahead log entries
      responses:
        '200':
          description: WAL entries
          content:
            application/json:
              schema:
                type: object
  /boot:
    post:
      operationId: bootKernel
      summary: Boot the kernel (load HOT, verify hashes, open WAL)
      responses:
        '200':
          description: Boot result
          content:
            application/json:
              schema:
                type: object
  /propose:
    post:
      operationId: propose
      summary: Submit a proposal for HOT memory modification
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [action, payload, risk, reasoning]
              properties:
                action:
                  type: string
                  enum: [create, update, delete, merge, rename, archive, restore]
                  description: "Proposal action type"
                payload:
                  type: object
                  description: The data to merge/create/update in HOT memory
                risk:
                  type: string
                  enum: [low, medium, high]
                reasoning:
                  type: string
                  description: Why this change is needed
      responses:
        '200':
          description: Staged proposal with ID
          content:
            application/json:
              schema:
                type: object
  /commit/{proposalId}:
    post:
      operationId: commitProposal
      summary: Commit a staged proposal to HOT memory
      parameters:
        - name: proposalId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Commit result
          content:
            application/json:
              schema:
                type: object
  /reject/{proposalId}:
    post:
      operationId: rejectProposal
      summary: Reject a staged proposal
      parameters:
        - name: proposalId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Rejection result
          content:
            application/json:
              schema:
                type: object
  /checkpoint:
    post:
      operationId: checkpoint
      summary: Persist HOT memory to disk with atomic write + hash + backup
      responses:
        '200':
          description: Checkpoint result
          content:
            application/json:
              schema:
                type: object
  /recover:
    post:
      operationId: recover
      summary: Attempt crash recovery (replay WAL or restore from backup)
      responses:
        '200':
          description: Recovery result
          content:
            application/json:
              schema:
                type: object
  /close:
    post:
      operationId: closeKernel
      summary: Graceful shutdown (checkpoint + release lock)
      responses:
        '200':
          description: Close result
          content:
            application/json:
              schema:
                type: object
```

6. Click **Save**.

#### Step 4: Test from GPT Chat

Try these prompts in your GPT conversation:
- *"Check the kernel status"* — calls `getStatus`
- *"Read my project memory"* — calls `getHot`
- *"Propose updating test_key to hello"* — calls `propose`
- *"Commit that proposal"* — calls `commitProposal`
- *"Checkpoint the state"* — calls `checkpoint`

### 8. Any LLM with HTTP Access

Any LLM or agent framework that can make HTTP requests can use the kernel.

1. Start the HTTP server:
   ```bash
   PYTHONPATH=/path/to/rag-runtime-kernel/src python -m rag_kernel serve --project /path/to/your/RAG --port 7437
   ```

2. The kernel listens on `http://127.0.0.1:7437` by default.
   - For remote access, use `--host 0.0.0.0` (or a Cloudflare tunnel for HTTPS).

3. All endpoints accept and return JSON. See the [API Quick Reference](#api-quick-reference) below.

4. Typical workflow:
   ```
   POST /boot           -> kernel enters READY state
   GET  /status          -> verify state
   GET  /hot             -> read current memory
   POST /propose         -> stage a mutation (returns proposal_id)
   POST /commit/{id}     -> apply the mutation
   POST /checkpoint      -> persist to disk with hash + backup
   POST /close           -> graceful shutdown
   ```

5. For agent frameworks (LangChain, AutoGPT, CrewAI, etc.), wrap the HTTP calls as tools. Each endpoint maps to one tool.

---

## Testing

### Running Unit Tests

The kernel has 337 unit tests across 8 test files.

```bash
cd /path/to/rag-runtime-kernel

# Run all tests
python -m pytest tests/ -v

# Run all tests with summary
python -m pytest tests/ -v --tb=short

# Run a specific module's tests
python -m pytest tests/test_state_machine.py -v
python -m pytest tests/test_persistence.py -v
python -m pytest tests/test_cold_manager.py -v
python -m pytest tests/test_concurrency.py -v
python -m pytest tests/test_api.py -v
python -m pytest tests/test_mcp_transport.py -v
python -m pytest tests/test_main.py -v
python -m pytest tests/test_schemas.py -v
```

If `PYTHONPATH` is not set:
```bash
# Windows
set PYTHONPATH=C:\path\to\rag-runtime-kernel\src
python -m pytest tests/ -v

# macOS/Linux
PYTHONPATH=src python -m pytest tests/ -v
```

### Verifying the HTTP Server (curl)

Start the server, then in a second terminal:

```bash
# 1. Check status
curl http://localhost:7437/status

# 2. Read HOT memory
curl http://localhost:7437/hot

# 3. Submit a proposal
curl -X POST http://localhost:7437/propose \
  -H "Content-Type: application/json" \
  -d '{"action": "update", "payload": {"test_key": "hello"}, "risk": "low", "reasoning": "Testing the kernel locally"}'

# 4. Commit the proposal (use the proposal_id from step 3)
curl -X POST http://localhost:7437/commit/PROPOSAL_ID_HERE

# 5. Checkpoint
curl -X POST http://localhost:7437/checkpoint

# 6. Read COLD partitions
curl http://localhost:7437/cold

# 7. Read WAL
curl http://localhost:7437/wal

# 8. Graceful shutdown
curl -X POST http://localhost:7437/close
```

### Verifying MCP Tools (Claude Desktop / Claude Code)

After configuring the MCP server:

1. Open a conversation and ask: *"List all available RAG kernel tools."*
   - Expected: Claude lists all 11 `rag_*` tools.

2. Ask: *"Boot the RAG kernel."*
   - Expected: Claude calls `rag_boot` and reports `state: READY`.

3. Ask: *"Show kernel status."*
   - Expected: Claude calls `rag_status` and shows state, session_id, seq, available transitions.

4. Ask: *"Read HOT memory."*
   - Expected: Claude calls `rag_hot` and displays the contents of `RAG_MASTER.json`.

5. Ask: *"Propose adding a test_key with value hello."*
   - Expected: Claude calls `rag_propose` and returns a proposal_id.

6. Ask: *"Commit that proposal."*
   - Expected: Claude calls `rag_commit` with the proposal_id, returns committed: true.

7. Ask: *"Checkpoint."*
   - Expected: Claude calls `rag_checkpoint`, returns checkpointed: true with state_hash.

---

## API Quick Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/status` | Kernel state, session_id, seq, available transitions, lock status |
| `GET` | `/hot` | Full HOT memory (RAG_MASTER.json contents) |
| `GET` | `/cold` | COLD partition summary with token estimates |
| `GET` | `/cold/{key}` | Load a specific COLD partition by key name |
| `GET` | `/wal` | WAL entries (optional `?since=N` query param) |
| `POST` | `/boot` | Boot kernel: load HOT, verify hashes, open WAL, acquire lock |
| `POST` | `/propose` | Stage a proposal: `{action, payload, risk, reasoning}` |
| `POST` | `/commit/{id}` | Commit a staged proposal by ID |
| `POST` | `/reject/{id}` | Reject a staged proposal by ID |
| `POST` | `/checkpoint` | Atomic write + hash recompute + backup rotation |
| `POST` | `/recover` | Attempt crash recovery from `.bak` file |
| `POST` | `/close` | Graceful shutdown: checkpoint, flush WAL, release lock |

Default port: **7437** (mnemonic: R-G-K on a phone keypad).

All endpoints return JSON. The `serve` command auto-boots the kernel on startup.

---

## MCP Tools Reference

| Tool | Parameters | Description |
|------|-----------|-------------|
| `rag_boot` | _(none)_ | Initialize kernel session |
| `rag_status` | _(none)_ | Get state, session, seq, transitions |
| `rag_hot` | _(none)_ | Read HOT memory |
| `rag_cold` | `partition?` (string) | Read COLD data; omit for full summary |
| `rag_propose` | `action` (string), `payload` (object) | Stage a mutation proposal |
| `rag_commit` | `proposal_id` (string) | Commit a staged proposal |
| `rag_reject` | `proposal_id` (string) | Reject a staged proposal |
| `rag_checkpoint` | _(none)_ | Persist with atomic write + hash |
| `rag_wal` | `since?` (integer) | Read WAL entries, optionally filtered |
| `rag_recover` | _(none)_ | Attempt recovery from .bak file |
| `rag_close` | _(none)_ | Graceful shutdown |

---

## Troubleshooting

**"Address already in use"**
Another process is on port 7437. Use `--port 7438` or kill the other process.

**"Project directory does not exist"**
Check the `--project` path. It must point to the folder containing `RAG_MASTER.json`.

**"Lock conflict"**
Another kernel instance holds the lock. Close it first, or delete `.rag_kernel.lock` in the project directory.

**"Hash mismatch" on boot**
`RAG_MASTER.json` was edited outside the kernel. The kernel enters RECOVERY state. Use `/recover` (HTTP) or `rag_recover` (MCP) to restore from backup, or fix hashes manually.

**"ModuleNotFoundError: No module named 'rag_kernel'"**
Set `PYTHONPATH` to include the `src/` directory of the repository:
```bash
# Windows
set PYTHONPATH=C:\path\to\rag-runtime-kernel\src

# macOS/Linux
export PYTHONPATH=/path/to/rag-runtime-kernel/src
```
For MCP configs, add `"env": {"PYTHONPATH": "..."}` to the server entry.

**Tunnel disconnects (GPT mode)**
The kernel keeps running locally. Restart `cloudflared tunnel --url http://localhost:7437` to get a new URL. Update the GPT action server URL to match.

**Claude Desktop does not show MCP tools**
- Verify `claude_desktop_config.json` is valid JSON (no trailing commas).
- Verify the `--project` path exists and contains `RAG_MASTER.json`.
- Restart Claude Desktop completely (quit and reopen).
- Check the Claude Desktop MCP logs for errors.

**pytest not found**
Install pytest: `pip install pytest`. The kernel itself has no dependencies, but tests require pytest.

---

*RAG Runtime Kernel v3.2 — 2026-05-15*
