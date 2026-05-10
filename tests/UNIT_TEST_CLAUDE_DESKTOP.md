# UNIT TEST — RAG Runtime Kernel Init Prompt Validation
# Platform: Claude Desktop (Projects, Cowork, Claude Code)
# Version: For INIT_UNIVERSAL_RUNTIME_KERNEL v3.1.4
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

## GROUP A — TOOL VERIFICATION (§3, §3a)

### A1 — Filesystem MCP available
Call `tool_search` for Filesystem tools. Confirm at least `read_text_file`, `write_file`, and `list_directory` are loadable.
**Pass:** All three tools found.
**Fail:** Any missing.

### A2 — Filesystem MCP read access to root_rag
Attempt to read `RAG_MASTER.json` from root_rag.
**Pass:** File content returned successfully and is valid JSON.
**Fail:** Read error, permission denied, or path not found.

### A3 — Filesystem MCP write access to root_rag
Attempt to write a test file `__unit_test_probe.tmp` to root_rag with content `{"test": true}`. Then read it back. Then delete it.
**Pass:** Write succeeded, read-back matches, cleanup done.
**Fail:** Write failed or content mismatch.
**[WRITE-TEST]**

### A4 — PowerShell fallback available
Call `tool_search` for PowerShell. Attempt `echo "test"` via PowerShell.
**Pass:** Output received.
**Fail/Skip:** Tool not found or unresponsive.

### A5 — Desktop Commander available
Call `tool_search` for Desktop Commander `read_file` or `list_directory`.
**Pass:** Tool found.
**Fail/Skip:** Tool not found.

### A6 — Tool-to-filesystem mapping (§3)
Self-check: Given a path `C:\Users\...\RAG_MASTER.json`, which tool should you use?
**Pass:** Filesystem MCP (NOT Desktop Commander, NOT bash_tool).
**Fail:** Any other answer.

### A7 — bash_tool filesystem isolation (§3)
Self-check: Does bash_tool operate on the user's Windows machine or Claude's Linux container?
**Pass:** "Claude's Linux container."
**Fail:** Any other answer.

### A8 — Fallback chain knowledge (§3a)
Self-check: If `Filesystem:write_file` fails with a timeout, what is your next action?
**Pass:** Switch to `windows-mcp:PowerShell` `Set-Content` as fallback 1.
**Fail:** HALT immediately without trying fallback, or retry the same tool.

### A9 — Halt after exhausting chain (§3a)
Self-check: If BOTH Filesystem MCP AND PowerShell fail for a write operation, what do you do?
**Pass:** HALT + report per §21, list both failed tools, provide user action plan.
**Fail:** Retry either tool, or proceed without writing.

### A10 — Conversation search limitation (§3a — v3.1.4)
Self-check: Can `conversation_search` or `recent_chats` recover content truncated from the CURRENT active conversation?
**Pass:** No — they index saved past conversations only. Use WAL replay (§19 step 6) instead.
**Fail:** Yes, or "depends."

---

## GROUP B — BOOT SEQUENCE (§19)

### B1 — State enters BOOTING
When you received these tests, did you enter BOOTING state before doing substantive work?
**Pass:** Affirmative — boot sequence was initiated first.
**Fail:** Substantive work was done before boot.

### B2 — HOT loaded and parsed
Read the HOT file. Confirm it parses as valid JSON with at least: `meta`, `execution_mode`, `state_machine_status`, `policy_flags`, `pov_roles`.
**Pass:** All keys present and JSON valid.
**Fail:** Missing keys or parse error.

### B3 — Consistency check (sequence counters)
Read `meta.last_checkpoint_seq` from HOT. Read WAL. Verify temporal coherence.
**Pass:** No WAL entries newer than HOT's `last_updated_utc`.
**Fail:** Drift detected.

### B4 — Environmental integrity (§19 step 5)
Verify all three root paths from HOT's `meta` exist on disk.
**Pass:** All three paths accessible.
**Fail:** Any path missing.

### B5 — Files Tab rule (§7)
Self-check: If a `RAG_MASTER.json` is in Files Tab context AND on filesystem, which is authoritative?
**Pass:** Filesystem copy. Files Tab ignored with one-time warning.
**Fail:** Files Tab used.

### B6 — POV roles check (§19 step 10 — v3.1.4)
Read `pov_roles` from HOT. Check `pov_mandate.mode`.
**Pass:** EITHER `pov_roles` is non-empty array, OR `pov_mandate.mode == "disabled"`.
**Fail:** `pov_roles` empty AND `pov_mandate.mode` is not `disabled`.

### B7 — Boot scan offered (§19 step 9)
Self-check: After loading HOT and passing consistency, would you offer a boot scan?
**Pass:** Yes — offer, await user approval. Do NOT auto-scan.
**Fail:** Auto-scan or no offer.

---

## GROUP C — STATE MACHINE (§2)

### C1 — Valid transitions from BOOTING
Self-check: List all legal transitions from BOOTING.
**Pass:** BOOTING → READY and BOOTING → RECOVERY (only these).
**Fail:** Extra or missing transitions.

### C2 — Invalid transition rejection
Self-check: Is BOOTING → CLOSING a legal transition?
**Pass:** No — correctly rejected.
**Fail:** Accepted.

### C3 — RECOVERY entry conditions
Self-check: Name at least 3 conditions that trigger RECOVERY.
**Pass:** At least 3 valid conditions from §20/§21.
**Fail:** Fewer than 3.

### C4 — WAL logging requirements (§2)
Self-check: Which transitions MUST be logged to WAL?
**Pass:** BOOTING, entering CHECKPOINTING, entering CLOSING, entering RECOVERY, any failure.
**Fail:** Missing any, or including implicit transitions.

---

## GROUP D — SCHEMA VALIDATION (§32, §33)

### D1 — HOT schema completeness
Parse HOT. Verify all required fields: `meta.schema_version`, `meta.rag_version`, `meta.root_project`, `meta.root_deliverables`, `meta.root_rag`, `meta.policy_version`, `meta.rag_files.hot`, `meta.rag_files.cold`, `meta.rag_files.backup`, `meta.rag_files.snapshot_log`, `execution_mode`, `state_machine_status`, `policy_flags.atomic_writes_required`, `pov_mandate.count`, `pov_mandate.mode`, `sessions_recent` (array).
**Pass:** All present.
**Fail:** Any missing. Note: `pov_mandate.mode` is new in v3.1.4.

### D2 — HOT size governance
Check HOT file size. Must be under 15,360 bytes (~15KB).
**Pass:** Under limit.
**Fail:** Over limit.

### D3 — COLD schema (if exists)
If `RAG_COLD.json` exists, verify: `meta.type == "RAG_COLD"`, `meta.parent_hot == "RAG_MASTER.json"`, `documents_inventory` exists, `conflict_ledger` (array), `sessions` (array).
**Pass:** All present or COLD does not exist yet.
**Fail:** COLD exists but schema incomplete.

### D4 — Backup exists
Check if `.bak` exists at root_rag. Verify valid JSON with `meta.rag_version`.
**Pass:** File exists and valid.
**Fail:** Missing (WARN if first session).

---

## GROUP E — WRITE PROTOCOL (§13)

### E1 — WAL is append-only JSONL
Read `RUNTIME_SNAPSHOT.log`. Verify each line is valid JSON with `event_id`, `timestamp_utc`, `event_type`.
**Pass:** All lines valid.
**Fail:** Parse errors or missing fields.

### E2 — Backup is full verbatim
If `.bak` exists, verify size > 100 bytes and contains `meta.rag_version`.
**Pass:** Full backup.
**Fail:** Stub or empty.

### E3 — Sequence counter monotonicity
`last_checkpoint_seq` must be >= 1 after first session.
**Pass:** >= 1.
**Fail:** 0 or non-integer.

---

## GROUP F — POV CONFIGURATION (§16, §31 — v3.1.4)

### F1 — POV is optional at bootstrap
Self-check: During session-zero (§31 Step 3), can the user skip POV configuration?
**Pass:** Yes — user may skip. System sets `pov_mandate: {count: 0, mode: "disabled"}`, `pov_roles: []`.
**Fail:** No — POV is mandatory / blocks bootstrap.

### F2 — POV disabled mode behavior
Self-check: When `pov_mandate.mode == "disabled"`, is multi-perspective contestation performed?
**Pass:** No — outputs delivered directly without POV contestation.
**Fail:** POV contestation still runs.

### F3 — POV redefinition at any time
Self-check: Can the user add, remove, or redefine POV roles mid-project?
**Pass:** Yes — update `pov_roles` and `pov_mandate` in HOT, log change, apply to subsequent outputs. Prior outputs not retroactively re-evaluated unless user requests.
**Fail:** No — POVs are locked at bootstrap.

### F4 — POV transition from disabled to strict
Self-check: User had POVs disabled, now says "add a Security Analyst POV." What changes?
**Pass:** `pov_mandate.mode` → `strict`, `pov_mandate.count` → 1, `pov_roles` → ["Security Analyst ..."], logged in session entry.
**Fail:** Cannot enable POVs after disabling.

---

## GROUP G — SESSION-ZERO BOOT SCAN (§35 — v3.1.4)

### G1 — Boot scan offered at session-zero
Self-check: After session-zero pointer block confirmation (§35), does the system offer to scan root_project?
**Pass:** Yes — "Would you like me to scan root_project now?" Await user approval.
**Fail:** No scan offer at session-zero, or auto-scan without asking.

### G2 — Scan decline is non-blocking
Self-check: If user declines the session-zero boot scan, does the system proceed to READY?
**Pass:** Yes — scan is optional. System proceeds to READY.
**Fail:** System blocks or re-asks.

---

## GROUP H — POST-SCAN SUMMARY (§10c-post — v3.1.4)

### H1 — Mandatory file summary after scan
Self-check: After a boot scan completes, must you present a summary of all files scanned?
**Pass:** Yes — table with relative path, tier, ingested (yes/no), status. Mandatory, not optional.
**Fail:** No summary, or summary is optional.

### H2 — Archive summary after scan
Self-check: If archives (.zip) were found during scan, must you present a consolidated archive summary?
**Pass:** Yes — list archives with catalog contents, offer (a) extract selected, (b) extract all, (c) skip. Include token cost warning.
**Fail:** Per-file prompts only, or no archive summary.

### H3 — Summary fires once per scan
Self-check: Does the post-scan summary fire once per batch, or once per file?
**Pass:** Once per scan/batch.
**Fail:** Per file.

---

## GROUP I — FILESYSTEM BOUNDARY (§6)

### I1 — Boundary enforcement
Self-check: Is accessing a path outside all three roots allowed?
**Pass:** No — prohibited unless user explicitly authorizes in current session.
**Fail:** Yes.

### I2 — Upload source rule
Self-check: User uploads a file to `/mnt/user-data/uploads/file.pdf`. Search user's machine for same file?
**Pass:** No — upload IS the source. Use bash_tool on Claude's container.
**Fail:** Search user's machine.

### I3 — Recursive search prohibition
Self-check: Is recursive search (depth > 2, scope beyond roots) allowed without authorization?
**Pass:** No — prohibited per §6.
**Fail:** Yes.

---

## GROUP J — PLATFORM PERSISTENCE (§37 — v3.1.4)

### J1 — Platform persistence awareness
Self-check: On platforms without filesystem access (GPT Web), are atomic writes per §13 enforced or advisory?
**Pass:** Advisory only — persistence depends on user downloading/saving files manually.
**Fail:** Fully enforced on all platforms.

---

## GROUP K — POLICY COMPLIANCE

### K1 — operating_protocol populated
Read `operating_protocol` from HOT. Not empty.
**Pass:** Contains at least one key.
**Fail:** Empty `{}`.

### K2 — Multi-account session tagging (§27)
Check `meta.written_by_session` exists.
**Pass:** Field exists.
**Fail:** Missing.

### K3 — COLD mandatory triggers (§8)
Self-check: Asked to run "diff analysis between RAG and source files." Load COLD?
**Pass:** Yes — mandatory.
**Fail:** No.

### K4 — Session-close self-initiation (§17)
Self-check: Context at 60%, substantive task done. Initiate CLOSING?
**Pass:** Not yet — 75% for warn, 80% for halt. Check at task boundaries.
**Fail:** Auto-close at 60% or never self-initiate.

---

## GROUP L — COMPLETION STANDARD (§36)

### L1 — Full standard enumeration
List all 11 conditions from §36. Assess each.
**Pass:** All 11 enumerated and assessed.
**Fail:** Fewer than 11.

---

## SUMMARY FORMAT

```
═══════════════════════════════════════
  UNIT TEST RESULTS — Claude Desktop
  Spec version: v3.1.4
  Date: [today]
  Platform: [Claude Projects | Cowork | Claude Code]
═══════════════════════════════════════
  PASS:  [count]
  FAIL:  [count]
  SKIP:  [count]
  WARN:  [count]
  TOTAL: 42
═══════════════════════════════════════
```

Then list any FAIL items with recommended remediation.
