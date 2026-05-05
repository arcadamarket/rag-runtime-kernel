# UNIT TEST — RAG Runtime Kernel Init Prompt Validation
# Platform: Claude Desktop (with Filesystem MCP + windows-mcp + Desktop Commander)
# Version: For INIT_UNIVERSAL_RUNTIME_KERNEL v3.1.3
# Usage: Drop this file into a Claude Desktop project alongside the init prompt.
#        Then say: "Run unit tests"

---

## INSTRUCTIONS FOR THE EXECUTING LLM

You are running a validation test suite against the INIT_UNIVERSAL_RUNTIME_KERNEL specification.
For each test below, execute the check described, then report:

```
[TEST_ID] — [PASS|FAIL|SKIP] — [one-line reason]
```

After all tests complete, output a summary table with pass/fail/skip counts.

If a test cannot be executed because a required tool is unavailable, report SKIP with the missing tool name.

Do NOT modify any files during testing. All tests are READ-ONLY unless explicitly marked [WRITE-TEST].

---

## GROUP A — TOOL VERIFICATION (§3)

### A1 — Filesystem MCP available
Call `tool_search` for Filesystem tools. Confirm at least `read_text_file`, `write_file`, and `list_directory` are loadable.
**Pass:** All three tools found.
**Fail:** Any missing.

### A2 — Filesystem MCP read access to root_rag
Attempt to read `RAG_MASTER.json` from root_rag (path defined in the init prompt pointer block or from any bootstrapped RAG).
**Pass:** File content returned successfully and is valid JSON.
**Fail:** Read error, permission denied, or path not found.

### A3 — Filesystem MCP write access to root_rag
Attempt to write a test file `__unit_test_probe.tmp` to root_rag with content `{"test": true}`. Then read it back. Then delete it.
**Pass:** Write succeeded, read-back matches, cleanup done.
**Fail:** Write failed or content mismatch.
**[WRITE-TEST]**

### A4 — windows-mcp:PowerShell available
Call `tool_search` for PowerShell. Attempt `echo "test"` via PowerShell.
**Pass:** Output received.
**Fail/Skip:** Tool not found or unresponsive.

### A5 — Desktop Commander available
Call `tool_search` for Desktop Commander `read_file` or `list_directory`.
**Pass:** Tool found.
**Fail/Skip:** Tool not found.

### A6 — Tool-to-filesystem mapping awareness
Self-check: Given the path `C:\Users\...\RAG_MASTER.json`, which tool should you use?
**Pass:** Answer is Filesystem MCP (NOT Desktop Commander, NOT bash_tool).
**Fail:** Any other answer.

### A7 — bash_tool filesystem isolation
Self-check: Does bash_tool operate on the user's Windows machine or Claude's Linux container?
**Pass:** Answer is "Claude's Linux container."
**Fail:** Any other answer.

---

## GROUP B — BOOT SEQUENCE (§19)

### B1 — State enters BOOTING
When you received these tests, did you enter BOOTING state before doing substantive work?
**Pass:** Affirmative — boot sequence was initiated first (or is being initiated now).
**Fail:** Substantive work was done before boot.

### B2 — HOT loaded and parsed
Read the HOT file. Confirm it parses as valid JSON with at least these top-level keys: `meta`, `execution_mode`, `state_machine_status`, `policy_flags`, `pov_roles`.
**Pass:** All keys present and JSON valid.
**Fail:** Missing keys or parse error.

### B3 — Consistency check (sequence counters)
Read `meta.last_checkpoint_seq` from HOT. Read the WAL (`RUNTIME_SNAPSHOT.log`). Verify the most recent WAL entry's session matches or is older than the HOT state.
**Pass:** Temporal coherence confirmed.
**Fail:** WAL has entries newer than HOT's `last_updated_utc` (potential drift).

### B4 — Environmental integrity (§19 step 5)
Verify all three root paths (`root_project`, `root_deliverables`, `root_rag`) from HOT's `meta` exist on disk.
**Pass:** All three paths accessible.
**Fail:** Any path missing or inaccessible.

### B5 — Files Tab rule (§7)
Check if a `RAG_MASTER.json` is present in the Files Tab / Project Instructions context (not the filesystem). If found, verify you would issue the §7 warning.
**Pass:** Either no Files Tab copy exists, or you correctly identify it as non-authoritative.
**Fail:** Files Tab copy would be used instead of filesystem copy.

### B6 — POV roles populated (§19 step 10)
Read `pov_roles` from HOT. Verify it is a non-empty array with at least 1 entry.
**Pass:** `pov_roles` has entries.
**Fail:** Empty or missing.

### B7 — Boot scan offered (§19 step 9)
Self-check: After loading HOT and passing consistency, would you offer a boot scan to the user?
**Pass:** Yes, per §19.
**Fail:** Auto-scan or no offer.

---

## GROUP C — STATE MACHINE (§2)

### C1 — Valid transitions enumeration
Self-check: List all legal transitions from BOOTING.
**Pass:** BOOTING → READY and BOOTING → RECOVERY (and only these).
**Fail:** Any additional or missing transitions.

### C2 — Invalid transition rejection
Self-check: Is BOOTING → CLOSING a legal transition?
**Pass:** No — correctly rejected.
**Fail:** Accepted as legal.

### C3 — RECOVERY entry conditions
Self-check: Name at least 3 conditions that trigger RECOVERY state.
**Pass:** At least 3 valid conditions from §20/§21.
**Fail:** Fewer than 3 or incorrect conditions.

---

## GROUP D — SCHEMA VALIDATION (§32, §33)

### D1 — HOT schema completeness
Parse HOT. Verify these required fields exist:
- `meta.schema_version`
- `meta.rag_version`
- `meta.root_project`, `meta.root_deliverables`, `meta.root_rag`
- `meta.policy_version`
- `meta.rag_files.hot`, `meta.rag_files.cold`, `meta.rag_files.backup`, `meta.rag_files.snapshot_log`
- `execution_mode`
- `state_machine_status`
- `policy_flags` (object with at least `atomic_writes_required`)
- `pov_mandate` (object with `count`)
- `sessions_recent` (array)
**Pass:** All present.
**Fail:** Any missing.

### D2 — HOT size governance
Check HOT file size in bytes. Must be under 15,360 bytes (~15KB).
**Pass:** Under limit.
**Fail:** Over limit.

### D3 — COLD schema (if COLD exists)
If `RAG_COLD.json` exists at root_rag, parse it and verify:
- `meta.type` == `"RAG_COLD"`
- `meta.parent_hot` == `"RAG_MASTER.json"`
- `documents_inventory` exists
- `conflict_ledger` exists (array)
- `sessions` exists (array)
**Pass:** All present or COLD does not exist yet (session-zero).
**Fail:** COLD exists but schema incomplete.

### D4 — Backup file exists
Check if `RAG_MASTER.json.bak` exists at root_rag.
**Pass:** File exists and is valid JSON.
**Fail:** Missing (acceptable only on first session — report as WARN).

---

## GROUP E — WRITE PROTOCOL (§13)

### E1 — WAL exists and is append-only JSONL
Read `RUNTIME_SNAPSHOT.log`. Verify each line is valid JSON with at least `event_id`, `timestamp_utc`, `event_type`.
**Pass:** All lines valid.
**Fail:** Parse errors or missing required fields.

### E2 — Backup is full verbatim (not a stub)
If `.bak` exists, verify its size is > 100 bytes and it contains `meta.rag_version`.
**Pass:** Full backup confirmed.
**Fail:** Stub or empty.

### E3 — Sequence counter monotonicity
Read `last_checkpoint_seq` from HOT. It must be >= 1 (after first session) and strictly increasing across sessions.
**Pass:** Value >= 1.
**Fail:** Value is 0 after a completed session, or non-integer.

---

## GROUP F — TOOL FALLBACK CHAIN (§3a)

### F1 — Fallback chain knowledge
Self-check: If Filesystem:write_file fails with a timeout, what is your next action?
**Pass:** Switch to windows-mcp:PowerShell `Set-Content` as fallback.
**Fail:** HALT immediately without trying fallback, or retry the same tool.

### F2 — Halt after exhausting chain
Self-check: If BOTH Filesystem MCP AND PowerShell fail for a write operation, what do you do?
**Pass:** HALT + report per §21, list both failed tools, provide user action plan.
**Fail:** Retry either tool, or proceed without writing.

### F3 — Boot health check
Self-check: During BOOTING, should you test each tool with a minimal operation?
**Pass:** Yes, per §3 tool verification — confirm read/write access before entering READY.
**Fail:** Skip tool testing.

---

## GROUP G — FILESYSTEM BOUNDARY (§6)

### G1 — Boundary enforcement
Self-check: Given root_rag = `C:\Users\pakhol\Desktop\MyProject\RAG`, is accessing `C:\Users\pakhol\Desktop\OtherProject\` allowed?
**Pass:** No — outside root_project, root_deliverables, root_rag.
**Fail:** Yes or "depends."

### G2 — Upload source rule
Self-check: User uploads a file via chat. It appears at `/mnt/user-data/uploads/file.pdf`. Should you search the user's Windows machine for the same file?
**Pass:** No — the upload IS the authorized source. Use bash_tool to read it from Claude's container.
**Fail:** Search user's machine.

### G3 — Recursive search prohibition
Self-check: Is a recursive desktop search (depth > 2, scope beyond root_*) allowed without explicit user authorization?
**Pass:** No — prohibited per §6.
**Fail:** Yes.

---

## GROUP H — POLICY COMPLIANCE

### H1 — operating_protocol populated
Read `operating_protocol` from HOT. Verify it is NOT an empty object `{}`.
**Pass:** Contains at least one key.
**Fail:** Empty object.

### H2 — Multi-account session tagging (§27)
Check if HOT contains `meta.written_by_session` field.
**Pass:** Field exists with a session ID.
**Fail:** Field missing.

### H3 — COLD mandatory load triggers (§8)
Self-check: You are asked to run a "diff analysis between RAG and source files." Do you load COLD?
**Pass:** Yes — mandatory trigger per §8.
**Fail:** No, or "only if needed."

### H4 — Session-close self-initiation (§17)
Self-check: Context is at 60% capacity and you've completed a substantive task. Do you initiate CLOSING?
**Pass:** Not yet — threshold is 75% for warn, 80% for halt. But you do check at task boundaries.
**Fail:** Either auto-close at 60% or never self-initiate.

---

## GROUP I — COMPLETION STANDARD (§36)

### I1 — Comprehensive correctness check
Self-check: Enumerate all 11 conditions from §36. For each, state whether the current system satisfies it.
**Pass:** All 11 enumerated and assessed.
**Fail:** Fewer than 11 or unable to assess.

---

## SUMMARY FORMAT

After all tests, output:

```
═══════════════════════════════════════
  UNIT TEST RESULTS — Claude Desktop
  Spec version: v3.1.3
  Date: [today]
═══════════════════════════════════════
  PASS:  [count]
  FAIL:  [count]
  SKIP:  [count]
  WARN:  [count]
  TOTAL: [count]
═══════════════════════════════════════
```

Then list any FAIL items with recommended remediation.
