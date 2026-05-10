# UNIT TEST — RAG Runtime Kernel Init Prompt Validation
# Platform: GPT Web (ChatGPT with Code Interpreter only — no MCP)
# Version: For INIT_UNIVERSAL_RUNTIME_KERNEL v3.1.4
# Usage: Upload this file + the init prompt into a new GPT conversation.
#        Then say: "Run unit tests"

---

## INSTRUCTIONS FOR THE EXECUTING LLM

You are running a validation test suite against the INIT_UNIVERSAL_RUNTIME_KERNEL specification.
This suite is for GPT Web — AUTONOMOUS mode, no Filesystem MCP.
Tests requiring MCP are replaced with schema/logic/self-check equivalents.

For each test, report:
```
[TEST_ID] — [PASS|FAIL|SKIP] — [one-line reason]
```

Summary table at end. Do NOT modify any files.

---

## GROUP A — ENVIRONMENT DETECTION (§0, §3, §37)

### A1 — Execution mode detected
Self-check: No Filesystem MCP. What execution mode?
**Pass:** AUTONOMOUS mode per §0.
**Fail:** ENFORCED or "degraded."

### A2 — Autonomous mode is not degraded
Self-check: In autonomous mode, are any rules relaxed?
**Pass:** No — all rules apply with full force (§0).
**Fail:** Any rules relaxed.

### A3 — Tool inventory
List all tools available (Code Interpreter, browsing, DALL-E, etc.). Classify correctly.
**Pass:** Accurate inventory; none can access user's local filesystem.
**Fail:** Claim filesystem access.

### A4 — No-MCP fallback awareness
Self-check: How do you handle RAG persistence without filesystem tools?
**Pass:** RAG via upload/paste, saved via Code Interpreter download. Log the gap per §0.
**Fail:** Claim filesystem access, or refuse to operate.

### A5 — Hash validation capability
Self-check: Can you compute SHA-256 hashes on uploaded files?
**Pass:** Yes via Code Interpreter `hashlib`. Hash fields are optional placeholders in autonomous mode (§14).
**Fail:** Claim impossible.

---

## GROUP B — SPECIFICATION PARSING

### B1 — Section count
Using Code Interpreter, count `## §` headings in the init prompt.
**Pass:** 40 headings (§0–§38 + §3a).
**Fail:** Count mismatch.

### B2 — Version identification
Extract version from title line.
**Pass:** `v3.1.4`.
**Fail:** Wrong version.

### B3 — Schema version in HOT template
Find §32, extract `schema_version`.
**Pass:** Correct value from §32.
**Fail:** Wrong or not found.

### B4 — Cross-reference integrity
Using Code Interpreter, scan for all `§N` references. Verify each resolves to an existing heading.
**Pass:** All references valid.
**Fail:** Dangling references.

---

## GROUP C — STATE MACHINE LOGIC (§2)

### C1 — Transition table
Self-check: List every state and its legal transitions.
**Pass:** BOOTING→READY/RECOVERY, READY→WORKING/INGESTING/CLOSING, INGESTING→WORKING/CHECKPOINTING, WORKING→CHECKPOINTING/INGESTING, CHECKPOINTING→READY/CLOSING, CLOSING→terminal, RECOVERY→READY.
**Fail:** Missing or extra.

### C2 — READY → CLOSING validity
Self-check: Is READY → CLOSING valid?
**Pass:** Yes — session can close from READY.
**Fail:** Rejected.

### C3 — BOOTING → WORKING rejection
Self-check: Can you go BOOTING → WORKING directly?
**Pass:** No — must go through READY. "No substantive work before READY." (§2)
**Fail:** Accepted.

### C4 — WAL logging requirements
Self-check: Which transitions MUST be logged?
**Pass:** BOOTING, entering CHECKPOINTING, entering CLOSING, entering RECOVERY, any failure (§2).
**Fail:** Missing any or including implicit transitions.

---

## GROUP D — SCHEMA VALIDATION (§32, §33)

### D1 — HOT schema validation
If `RAG_MASTER.json` uploaded, parse and verify all required fields including `pov_mandate.mode` (v3.1.4).
If no RAG uploaded: SKIP.
**Pass:** All fields present.
**Fail:** Missing fields.

### D2 — HOT size governance
If RAG uploaded, check size < 15,360 bytes.
If no RAG uploaded: SKIP.
**Pass:** Under limit.
**Fail:** Over.

### D3 — COLD schema validation
If `RAG_COLD.json` uploaded, verify schema.
If no COLD uploaded: SKIP.
**Pass:** Schema valid.
**Fail:** Violations.

### D4 — HOT template reproducibility
Using Code Interpreter, generate blank HOT from §32. Validate JSON + all required fields.
**Pass:** Valid and complete.
**Fail:** Parse error or missing fields.

### D5 — COLD template reproducibility
Using Code Interpreter, generate blank COLD from §33. Validate.
**Pass:** Valid and complete.
**Fail:** Parse error or missing fields.

---

## GROUP E — PROPOSAL CONTRACT (§4)

### E1 — Proposal structure
Self-check: Required fields in a formal proposal?
**Pass:** `proposal_id`, `action`, `state_before`, `state_after`, `payload`, `risk`, `reasoning`.
**Fail:** Missing any.

### E2 — Risk-proportional application
Self-check: Does a minor status update need a full proposal?
**Pass:** No — low-risk uses internal validation (§4).
**Fail:** Yes to all, or no validation.

### E3 — Autonomous mode role
Self-check: In autonomous mode, who proposes and validates?
**Pass:** The model itself.
**Fail:** Only external wrapper.

---

## GROUP F — POV CONFIGURATION (§16, §31 — v3.1.4)

### F1 — POV is optional at bootstrap
Self-check: Can user skip POV at session-zero?
**Pass:** Yes — sets `pov_mandate: {count: 0, mode: "disabled"}`, `pov_roles: []`.
**Fail:** POV mandatory.

### F2 — POV disabled mode
Self-check: When `pov_mandate.mode == "disabled"`, is contestation performed?
**Pass:** No — outputs delivered directly.
**Fail:** Contestation still runs.

### F3 — POV redefinition at any time
Self-check: Can user redefine POVs mid-project?
**Pass:** Yes — update HOT, log change, apply to subsequent outputs. Prior outputs unchanged unless user requests.
**Fail:** Locked at bootstrap.

### F4 — POV transition disabled → strict
Self-check: User had disabled POVs, now says "add a Security Analyst POV." Result?
**Pass:** `mode` → `strict`, `count` → 1, `pov_roles` updated, logged.
**Fail:** Cannot enable after disabling.

---

## GROUP G — SESSION-ZERO BOOT SCAN (§35 — v3.1.4)

### G1 — Boot scan offered at session-zero
Self-check: After pointer block confirmation, is scan offered?
**Pass:** Yes — await user approval.
**Fail:** No offer or auto-scan.

### G2 — Scan decline non-blocking
Self-check: User declines scan — system blocked?
**Pass:** No — proceeds to READY.
**Fail:** Blocked.

---

## GROUP H — POST-SCAN SUMMARY (§10c-post — v3.1.4)

### H1 — Mandatory file summary
Self-check: After scan, must you present all-files summary?
**Pass:** Yes — table with relative path, tier, ingested, status. Mandatory.
**Fail:** No summary or optional.

### H2 — Archive summary
Self-check: If archives found, consolidated summary required?
**Pass:** Yes — list with catalog, offer extract selected/all/skip, token warning.
**Fail:** No archive summary.

### H3 — Summary frequency
Self-check: Summary fires once per scan or per file?
**Pass:** Once per scan/batch.
**Fail:** Per file.

---

## GROUP I — POLICY RULES

### I1 — Filesystem boundary paths
Self-check: What three paths define the boundary?
**Pass:** `root_project`, `root_deliverables`, `root_rag`.
**Fail:** Missing or extra.

### I2 — Source hierarchy tiers
Self-check: List all tiers.
**Pass:** Tier 0 (primary), Tier 1 (filed/published), Tier 2 (AI-processed), Tier 3 (drafts). Primary overrides.
**Fail:** Wrong hierarchy.

### I3 — Files Tab rule
Self-check: RAG in chat context AND filesystem — which authoritative?
**Pass:** Filesystem. Chat copy ignored with warning.
**Fail:** Chat copy used.

### I4 — Token thresholds
Self-check: Warn at? HALT at?
**Pass:** 75% warn, 80% halt.
**Fail:** Wrong.

### I5 — Session-close audit scope
Self-check: Discussion-only session needs close audit?
**Pass:** Yes — all sessions (§17).
**Fail:** No.

### I6 — Conflict ledger rules
Self-check: Two sources disagree — 4 rules?
**Pass:** Preserve both, record conflict, never delete loser, never silently merge.
**Fail:** Missing any.

### I7 — COLD mandatory triggers
Self-check: Name 3+ mandatory COLD load triggers.
**Pass:** 3+ from §8 list.
**Fail:** Fewer than 3.

### I8 — operating_protocol required at session-zero
Self-check: Is `operating_protocol: {}` acceptable after bootstrap?
**Pass:** No — must be populated (§31).
**Fail:** Acceptable.

---

## GROUP J — PLATFORM PERSISTENCE (§37 — v3.1.4)

### J1 — GPT Web atomic writes
Self-check: On GPT Web, are atomic writes enforced or advisory?
**Pass:** Advisory — persistence depends on user downloading/saving files manually.
**Fail:** Enforced.

### J2 — WAL on GPT Web
Self-check: Are WAL entries written to disk automatically on GPT Web?
**Pass:** No — generated in-context but not persisted unless user saves snapshot log.
**Fail:** Auto-persisted.

### J3 — Recovery prerequisites on GPT Web
Self-check: What must user have saved for recovery to work on GPT Web?
**Pass:** RAG_MASTER.json, .bak, and RUNTIME_SNAPSHOT.log.
**Fail:** Only RAG_MASTER.json, or "recovery works automatically."

---

## GROUP K — RECOVERY PROTOCOL (§20)

### K1 — Recovery steps
Self-check: Enumerate the 7 recovery steps.
**Pass:** (1) HALT, enter RECOVERY; (2) try .bak; (3) try WAL; (4) if .bak valid → offer restore; (5) if .bak fails → options A/B/C; (6) unsaved facts from WAL; (7) resume only after verification.
**Fail:** Missing steps.

### K2 — No silent proceed
Self-check: Can you proceed with missing/broken RAG?
**Pass:** No — "NEVER silently proceed" (§20).
**Fail:** Yes.

---

## GROUP L — CROSS-PLATFORM (§37)

### L1 — GPT-specific tools
Self-check: Which §37 tool hierarchy tools are available in GPT Web?
**Pass:** None of the MCP tools. Code Interpreter only. Autonomous mode.
**Fail:** Claim MCP tools.

### L2 — Persistence approximation
Self-check: How do you persist RAG state without filesystem?
**Pass:** Generate JSON via Code Interpreter → downloadable file → user saves. Re-upload next session.
**Fail:** Auto-persists or refuse to operate.

---

## GROUP M — COMPLETION STANDARD (§36)

### M1 — Full standard enumeration
List all 11 conditions from §36. Assess which can be validated in GPT Web.
**Pass:** All 11 listed with feasibility assessment.
**Fail:** Fewer than 11.

---

## SUMMARY FORMAT

```
═══════════════════════════════════════
  UNIT TEST RESULTS — GPT Web
  Spec version: v3.1.4
  Date: [today]
═══════════════════════════════════════
  PASS:  [count]
  FAIL:  [count]
  SKIP:  [count]
  WARN:  [count]
  TOTAL: 43
═══════════════════════════════════════
```

Then list any FAIL items with recommended remediation.
