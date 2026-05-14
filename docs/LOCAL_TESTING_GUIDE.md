# Local Testing Guide — RAG Runtime Kernel v3.2

How to run the kernel on your machine and connect it to GPT Chat (or any LLM) via Custom Actions.

---

## Prerequisites

- Python 3.10+ installed
- The `rag-runtime-kernel` repo cloned locally
- A RAG project directory with `RAG_MASTER.json` (your existing `RAG/` folder works)
- For GPT Chat: a free [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-local-tunnel/) (`cloudflared`) to expose localhost

---

## 1. Start the Kernel (HTTP mode)

Open a terminal in the repo root:

```bash
cd "C:\Users\pakhol\Desktop\GitHub Project (RAG Runtime Kernel)\GIT WORKTREES\rag-runtime-kernel"

python -m rag_kernel serve --project "C:\Users\pakhol\Desktop\GitHub Project (RAG Runtime Kernel)\RAG" --port 7437
```

Expected output:

```
RAG Runtime Kernel serving on http://127.0.0.1:7437
Project: C:\Users\pakhol\Desktop\GitHub Project (RAG Runtime Kernel)\RAG
Session: S-20260514-...
State: READY
Press Ctrl+C to stop.
```

The kernel is now:
- Listening on `http://127.0.0.1:7437`
- HOT memory loaded from `RAG_MASTER.json`
- WAL open and recording
- Project lock held

---

## 2. Verify with curl

In a second terminal, test the endpoints:

```bash
# Check status
curl http://localhost:7437/status

# Read HOT memory
curl http://localhost:7437/hot

# Boot (if not auto-booted)
curl -X POST http://localhost:7437/boot

# Submit a proposal
curl -X POST http://localhost:7437/propose -H "Content-Type: application/json" -d "{\"action\": \"update\", \"payload\": {\"test_key\": \"hello\"}, \"risk\": \"low\", \"reasoning\": \"Testing the kernel locally\"}"

# Commit the proposal (use the ID from the propose response)
curl -X POST http://localhost:7437/commit/S-20260514-xxxxxx-P1

# Checkpoint (persists HOT + hashes)
curl -X POST http://localhost:7437/checkpoint

# Read COLD partitions
curl http://localhost:7437/cold

# Shut down cleanly
curl -X POST http://localhost:7437/close
```

---

## 3. Expose to GPT Chat via Cloudflare Tunnel

GPT Chat Custom Actions need a public HTTPS URL. Cloudflare Tunnel provides one for free with zero config.

### Install cloudflared

```bash
# Windows (winget)
winget install cloudflare.cloudflared

# Or download from: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
```

### Start the tunnel

```bash
cloudflared tunnel --url http://localhost:7437
```

Output will show something like:

```
+--------------------------------------------------------------------------------------------+
|  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
|  https://random-words-here.trycloudflare.com                                              |
+--------------------------------------------------------------------------------------------+
```

Copy that `https://....trycloudflare.com` URL.

---

## 4. Configure GPT Chat Custom Actions

1. Go to [ChatGPT](https://chat.openai.com) and open a conversation (or create a custom GPT)
2. Click your profile icon -> **My GPTs** -> **Create a GPT** (or edit an existing one)
3. Go to the **Configure** tab -> scroll to **Actions** -> **Create new action**
4. Set the **Authentication** to **None**
5. Paste the following OpenAPI schema, replacing `YOUR_TUNNEL_URL` with the cloudflared URL:

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
                  description: "Proposal action type. Prefix custom actions with 'custom:'"
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

6. Click **Save**

---

## 5. Test from GPT Chat

In the GPT conversation, try prompts like:

> "Check the kernel status"

GPT will call `getStatus` and show you the current state, session ID, etc.

> "Read my project memory"

GPT will call `getHot` and return the contents of RAG_MASTER.json.

> "Propose updating the current_status.unit_tests field to mark them as executed"

GPT will call `propose` with the appropriate payload. You'll see the staged proposal ID in the response, then you can say:

> "Commit that proposal"

And GPT will call `commitProposal` with the ID.

> "Checkpoint the state"

This triggers an atomic write of RAG_MASTER.json with hash verification and backup rotation.

---

## 6. Claude Desktop (MCP mode)

For Claude Desktop, use MCP mode instead of HTTP:

```bash
python -m rag_kernel mcp --project "C:\Users\pakhol\Desktop\GitHub Project (RAG Runtime Kernel)\RAG"
```

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "rag-kernel": {
      "command": "python",
      "args": [
        "-m", "rag_kernel", "mcp",
        "--project", "C:\\Users\\pakhol\\Desktop\\GitHub Project (RAG Runtime Kernel)\\RAG"
      ]
    }
  }
}
```

Claude Desktop will see 11 tools: `rag_boot`, `rag_status`, `rag_hot`, `rag_cold`, `rag_propose`, `rag_commit`, `rag_reject`, `rag_checkpoint`, `rag_wal`, `rag_recover`, `rag_close`.

---

## 7. API Quick Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/status` | Kernel state, session, project path |
| GET | `/hot` | Full HOT memory |
| GET | `/cold` | COLD partition summary + token estimates |
| GET | `/cold/{key}` | Load specific COLD partition |
| GET | `/wal` | WAL entries |
| POST | `/boot` | Boot kernel (auto-called by `serve`) |
| POST | `/propose` | Stage a proposal `{action, payload, risk, reasoning}` |
| POST | `/commit/{id}` | Commit staged proposal |
| POST | `/reject/{id}` | Reject staged proposal |
| POST | `/checkpoint` | Atomic write + hash + backup |
| POST | `/recover` | Crash recovery |
| POST | `/close` | Graceful shutdown |

---

## Troubleshooting

**"Address already in use"** — Another process is on port 7437. Use `--port 7438` or kill the other process.

**"Project directory does not exist"** — Check the `--project` path. It must point to the folder containing `RAG_MASTER.json`.

**"Lock conflict"** — Another kernel instance holds the lock. Close it first, or delete `.rag_kernel.lock` in the project directory.

**"Hash mismatch" on boot** — RAG_MASTER.json was edited outside the kernel. The kernel enters RECOVERY state. Use the `/recover` endpoint or fix hashes manually.

**Tunnel disconnects** — The kernel keeps running locally. Restart `cloudflared tunnel --url http://localhost:7437` to get a new URL (you'll need to update the GPT action server URL).

---

*Generated for RAG Runtime Kernel v3.2 — 2026-05-14*
