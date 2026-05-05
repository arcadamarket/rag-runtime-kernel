# UNIT TEST — RAG Runtime Kernel Init Prompt Validation
# Platform: GPT Web (ChatGPT with Code Interpreter only — no MCP)
# Version: For INIT_UNIVERSAL_RUNTIME_KERNEL v3.1.3
# Usage: Upload this file + the init prompt into a new GPT project/conversation.
#        Then say: "Run unit tests"

---

## INSTRUCTIONS FOR THE EXECUTING LLM

You are running a validation test suite against the INIT_UNIVERSAL_RUNTIME_KERNEL specification.
This suite is designed for GPT Web, which operates in AUTONOMOUS mode with no Filesystem MCP.
Tests that require MCP filesystem access are replaced with schema/logic/self-check equivalents.

For each test, execute the check described, then report:

```
[TEST_ID] — [PASS|FAIL|SKIP] — [one-line reason]
```

After all tests complete, output a summary table with pass/fail/skip counts.

---

## GROUP A — ENVIRONMENT DETECTION (§0, §3, §37)

### A1 — Execution mode detected
Self-check: You have no Filesystem MCP. What execution mode should you operate in?
**Pass:** AUTONOMOUS mode per §0 — "The default when operating inside ChatGPT."
**Fail:** ENFORCED mode, or "degraded mode."

### A2 — Autonomous mode is not degraded
Self-check: In autonomous mode, are any rules relaxed or optional?
**Pass:** No — "Autonomous mode is NOT degraded mode. All rules apply with full force." (§0)
**Fail:** Any rules described as relaxed.

### A3 — Tool inventory (GPT environment)
List all tools available to you in this session (Code Interpreter, web browsing, DALL-E, etc.).
**Pass:** Accurate inventory reported; tools correctly classified as unable to access user's local filesystem.
**Fail:** Claim to have filesystem access when you don't, or missing tools from inventory.

### A4 — No-MCP fallback awareness
Self-check: With no Filesystem MCP, how do you handle file reads/writes for RAG persistence?
**Pass:** Acknowledge you cannot directly read/write to user's filesystem. State that RAG must be provided via upload/paste and saved via code interpreter download or user manual copy. Per §0: "use the best available approximation, log the gap."
**Fail:** Claim filesystem access, or halt without offering any approximation.

### A5 — Hash validation in autonomous mode
Self-check: Can you compute and verify SHA-256 hashes on RAG files?
**Pass:** Yes — Code Interpreter has `hashlib`. In autonomous mode, hash fields are optional placeholders (§14), but you CAN compute them via Code Interpreter if files are uploaded.
**Fail:** Claim hash computation is impossible.

---

## GROUP B — SPECIFICATION PARSING

### B1 — Section count
Using Code Interpreter, count the number of `## §` headings in the init prompt file.
**Pass:** Count matches expected (40 section headings: §0–§38 plus §3a for v3.1.3).
**Fail:** Count mismatch.

### B2 — Version identification
Extract the version number from the init prompt title line.
**Pass:** Reports `v3.1.3`.
**Fail:** Wrong version or unable to extract.

### B3 — Schema version in HOT template
Find the HOT schema template (§32). Extract `schema_version`.
**Pass:** Reports the value defined in §32.
**Fail:** Wrong value or not found.

### B4 — Cross-reference integrity
Using Code Interpreter, scan the init prompt for all `§N` references. For each reference, verify the referenced section actually exists as a `## §N` heading.
**Pass:** All cross-references resolve to existing sections.
**Fail:** Any dangling reference (references a section that doesn't exist).

---

## GROUP C — STATE MACHINE LOGIC (§2)

### C1 — Transition table completeness
Self-check: List every state and its allowed transitions.
**Pass:**
- BOOTING → READY, RECOVERY
- READY → WORKING, INGESTING, CLOSING
- INGESTING → WORKING, CHECKPOINTING
- WORKING → CHECKPOINTING, INGESTING
- CHECKPOINTING → READY, CLOSING
- CLOSING → (terminal)
- RECOVERY → READY
**Fail:** Missing or extra transitions.

### C2 — Invalid transition rejection
Self-check: Is READY → CLOSING a valid transition?
**Pass:** Yes — a session can close from READY (user says goodbye, nothing to do).
**Fail:** Incorrectly rejected.

### C3 — BOOTING → WORKING rejection
Self-check: Can you go directly from BOOTING to WORKING?
**Pass:** No — must pass through READY first. "No substantive work before READY." (§2)
**Fail:** Accepted.

### C4 — WAL logging requirements
Self-check: Which state transitions MUST be logged to the WAL?
**Pass:** BOOTING, entering CHECKPOINTING, entering CLOSING, entering RECOVERY, any failure. (§2)
**Fail:** Missing any of these, or including implicit transitions (READY→WORKING).

---

## GROUP D — SCHEMA VALIDATION (§32, §33)

### D1 — HOT schema validation via Code Interpreter
If a `RAG_MASTER.json` file has been uploaded, parse it and verify all required fields from §32 exist:
- `meta.schema_version`, `meta.rag_version`, `meta.root_project`, `meta.root_deliverables`, `meta.root_rag`
- `meta.policy_version`, `meta.rag_files` (with hot, cold, backup, snapshot_log)
- `execution_mode`, `state_machine_status`
- `policy_flags.atomic_writes_required`
- `pov_mandate.count`
- `sessions_recent` (array)
If no RAG uploaded: SKIP.
**Pass:** All fields present and correctly typed.
**Fail:** Any missing or wrong type.

### D2 — HOT size governance via Code Interpreter
If RAG uploaded, check file size. Must be under 15,360 bytes.
If no RAG uploaded: SKIP.
**Pass:** Under 15KB.
**Fail:** Over 15KB.

### D3 — COLD schema validation
If `RAG_COLD.json` uploaded, verify:
- `meta.type` == `"RAG_COLD"`
- `meta.parent_hot` == `"RAG_MASTER.json"`
- `documents_inventory` exists
- `conflict_ledger` exists (array)
- `sessions` exists (array)
If no COLD uploaded: SKIP.
**Pass:** Schema valid.
**Fail:** Schema violations.

### D4 — HOT template reproducibility
Using Code Interpreter, generate a blank HOT JSON from the §32 template. Validate it parses as valid JSON and contains all required fields.
**Pass:** Generated JSON is valid and complete.
**Fail:** Parse error or missing fields.

### D5 — COLD template reproducibility
Using Code Interpreter, generate a blank COLD JSON from the §33 template. Validate it.
**Pass:** Valid and complete.
**Fail:** Parse error or missing fields.

---

## GROUP E — PROPOSAL CONTRACT (§4)

### E1 — Proposal structure
Self-check: What fields must a formal proposal contain?
**Pass:** `proposal_id`, `action`, `state_before`, `state_after`, `payload`, `risk`, `reasoning`.
**Fail:** Missing any field.

### E2 — Risk-proportional application
Self-check: Does a minor status field update require a full formal JSON proposal?
**Pass:** No — low-risk actions use internal validation without formal ceremony (§4 risk-proportional section).
**Fail:** Yes to all, or no validation at all.

### E3 — Autonomous mode proposal handling
Self-check: In autonomous mode, who is both proposer and validator?
**Pass:** The model itself. "The prohibition applies to UNVALIDATED mutation — not to the model performing validated writes." (§4)
**Fail:** Only an external wrapper can validate.

---

## GROUP F — POLICY RULES (self-check)

### F1 — Filesystem boundary principle
Self-check: What three paths define the filesystem boundary?
**Pass:** `root_project`, `root_deliverables`, `root_rag`.
**Fail:** Missing or extra paths.

### F2 — Source hierarchy tiers
Self-check: List all tiers and their authority level.
**Pass:** Tier 0 (primary/authoritative), Tier 1 (filed/published), Tier 2 (processed AI), Tier 3 (working drafts). Primary overrides summaries.
**Fail:** Missing tiers or wrong hierarchy.

### F3 — Files Tab rule
Self-check: If a `RAG_MASTER.json` is found in chat context/uploads AND on the filesystem, which is authoritative?
**Pass:** Filesystem copy. Files Tab copy is ignored with a one-time warning (§7).
**Fail:** Files Tab copy, or merge both.

### F4 — Token economy thresholds
Self-check: At what context capacity do you warn? At what capacity do you HALT?
**Pass:** 75% = warn + checkpoint. 80% = HALT + must save.
**Fail:** Wrong thresholds.

### F5 — Session-close audit scope
Self-check: Does a discussion-only session (no file operations) still require a session-close audit?
**Pass:** Yes — "Applies to ALL sessions — including discussion-only sessions." (§17)
**Fail:** No.

### F6 — Conflict ledger rules
Self-check: When two sources disagree, what are the 4 rules?
**Pass:** (1) Preserve BOTH records, (2) Record structured conflict entry, (3) NEVER delete the losing record, (4) NEVER silently merge.
**Fail:** Missing any rule.

### F7 — COLD mandatory load triggers
Self-check: Name at least 3 mandatory COLD load triggers from §8.
**Pass:** Any 3 from: cross-reference tasks, diff/comparison/audit, status summary after prior ingestion, root cause analysis, multi-account environments.
**Fail:** Fewer than 3.

### F8 — operating_protocol is required
Self-check: Is `operating_protocol: {}` (empty) acceptable after session-zero?
**Pass:** No — it must be populated with behavioral rules at session-zero (§31).
**Fail:** Acceptable.

---

## GROUP G — RECOVERY PROTOCOL (§20)

### G1 — Recovery steps
Self-check: Enumerate the 7 recovery steps from §20.
**Pass:** (1) HALT, enter RECOVERY; (2) try .bak; (3) try WAL; (4) if .bak valid offer restore; (5) if .bak fails offer rebuild options A/B/C; (6) identify unsaved facts from WAL; (7) resume only after verification.
**Fail:** Missing steps.

### G2 — No silent proceed
Self-check: Can you proceed with substantive work if the RAG is missing or broken?
**Pass:** No — "NEVER silently proceed with a missing or broken RAG." (§20)
**Fail:** Yes, in any circumstance.

---

## GROUP H — CROSS-PLATFORM INTEROP (§37)

### H1 — GPT-specific capabilities
Self-check: In a GPT Web session, which tools from the §37 tool hierarchy are available?
**Pass:** None of the MCP tools. Code Interpreter only. Autonomous mode applies. User must upload/download files manually.
**Fail:** Claim MCP tools are available.

### H2 — Persistence approximation
Self-check: How do you persist RAG state in GPT Web without filesystem access?
**Pass:** Generate JSON content via Code Interpreter → offer as downloadable file → user saves to their filesystem manually. Log the non-MCP gap. On next session, user re-uploads updated RAG.
**Fail:** Claim it persists automatically, or refuse to operate.

---

## GROUP I — COMPLETION STANDARD (§36)

### I1 — Full standard enumeration
Self-check: List all 11 conditions from §36 and assess which ones can be validated in GPT Web.
**Pass:** All 11 listed. Clear distinction between validatable (schema, logic, policy) and not-validatable-without-MCP (filesystem persistence, atomic writes).
**Fail:** Fewer than 11 or no feasibility assessment.

---

## SUMMARY FORMAT

After all tests, output:

```
═══════════════════════════════════════
  UNIT TEST RESULTS — GPT Web
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
