# GPT Web Test Analysis — v3.1.3

> Source: Full end-to-end simulation on GPT Web (ChatGPT, autonomous mode, no MCP)
> Date: 2026-05-04
> Tests executed: 33 PASS, 0 FAIL, 3 SKIP
> Simulation coverage: Bootstrap → Boot → Ingestion → Recovery → Concurrency → Partitioning → Token Pressure → Conflict Ledger → Drift Detection → Adversarial Poisoning → Cross-Platform Desync → POV Loops → WAL Replay with Missing Segments

---

## 1. Defect / Error List

**DEF-001 — Step 5 POV Configuration is a HARD BLOCK**
- Severity: HIGH
- Section: §31 (Bootstrap)
- Current state: POV configuration is mandatory and blocks session-zero flow entirely
- Problem: Users who want to use the system without multi-POV validation are stuck — no skip path exists
- Impact: First-time users cannot proceed past bootstrap without defining POVs, even if their workflow doesn't need them
- Required fix: Make POV step optional (like Project Description at Step 4). Default to `pov_mandate.count: 0, mode: "disabled"` when skipped

**DEF-002 — Boot Scan Offer (§19 2.10) only appears in follow-up sessions**
- Severity: MEDIUM
- Section: §19 (Boot Sequence)
- Current state: Boot scan offer triggers at session 2+, but NOT during session-zero
- Problem: Session-zero creates the RAG but never offers to scan the project root for existing files
- Impact: User must manually trigger ingestion or wait until session 2 — lost opportunity for immediate value
- Required fix: Boot scan offer must trigger during session-zero, after RAG creation and pointer block confirmation

**DEF-003 — Archive handling lacks post-scan extraction prompt**
- Severity: LOW
- Section: §10c (Ingestion Pipeline)
- Current state: `.zip` files are cataloged (contents listed) but never extracted — correct per spec
- Problem: After scan completion, system does not offer to extract archive contents
- Impact: Archives remain opaque unless user explicitly requests extraction
- Required fix: After scan completes, detect archives in root, prompt user with option to scan/extract contents, include warning about token cost

---

## 2. Weakness / Risk List

**WEAK-001 — Token estimation is unreliable on GPT Web**
- Risk: MEDIUM
- Section: §15 (Token Economy)
- No real token counter exposed; system must estimate context usage
- False checkpoint triggers waste user time; missed triggers cause silent context truncation
- Mitigation: Conservative estimation bias (trigger early rather than late)

**WEAK-002 — Lazy-loading of COLD partitions is simulated, not real, on GPT Web**
- Risk: MEDIUM
- Section: §8 (HOT/COLD Memory)
- GPT Web cannot load partitions on demand — user must provide content manually
- Cross-reference resolution across partitions requires user intervention
- Impact: Partition-level token savings are theoretical on this platform

**WEAK-003 — Split-brain risk during partitioned COLD with multiple sessions**
- Risk: HIGH
- Section: §8 + §27
- Session A modifies partition 1, Session B modifies partition 2 — no global coordination
- Detection relies on checkpoint_seq matching across all partitions
- If user fails to sync files between sessions, inconsistency goes undetected until next full validation

**WEAK-004 — Recovery quality depends entirely on user discipline**
- Risk: HIGH
- Section: §20 (Recovery)
- WAL replay, .bak restore, and COLD reconstruction all require the user to have saved these files
- On GPT Web (no filesystem), if user loses any of RAG_MASTER.json, .bak, or RUNTIME_SNAPSHOT.log, recovery degrades or fails
- No automated backup — entirely manual

**WEAK-005 — Adversarial poisoning detection is heuristic, not formal**
- Risk: LOW-MEDIUM
- Section: §10 + §11
- System detects poisoning via consistency checks and POV review, but has no formal verification
- Sophisticated poisoning (subtle, plausible-looking data) may evade detection
- Mitigation: Conflict ledger preserves both sources; user always has final decision gate

**WEAK-006 — Concurrency guard is detect-and-halt, not detect-and-merge**
- Risk: LOW
- Section: §27
- Multi-session writes are detected and stopped, but never auto-merged
- For high-frequency multi-user workflows this creates friction
- By design (safety over convenience) — documented, not a bug

**WEAK-007 — Drift detection requires session history depth**
- Risk: LOW
- Section: §14
- Drift across 2 sessions is obvious; drift across 10+ sessions requires full COLD load
- Token cost of deep drift analysis can be significant
- Mitigation: Mandatory COLD load triggers for analytical tasks (§8)

---

## 3. Troubleshooting Playbook (User-Resolvable)

**TS-001 — RAG not loading at session start (GPT Web)**
- Cause: Pointer block not pasted into Project Instructions, or RAG file not uploaded
- Fix: Verify pointer block is in Project Instructions. Upload RAG_MASTER.json at session start. If using ChatGPT custom instructions, paste the pointer text directly.

**TS-002 — "State machine stuck in BOOTING" on GPT Web**
- Cause: Tool verification step detects no filesystem tools, but fallback chain activation stalls
- Fix: Respond to any blocking prompt. If system asks about tool availability, confirm "no filesystem access — use user-assisted mode." The system will proceed.

**TS-003 — Checkpoint fails / no file downloaded**
- Cause: GPT Web requires manual download of RAG files; system cannot write to disk
- Fix: When system says "checkpoint complete," immediately download the generated JSON. Save to your local RAG folder. Do not close the session before downloading.

**TS-004 — COLD partition not loading**
- Cause: System requests a specific partition file, but user hasn't uploaded it
- Fix: Upload the requested partition file when prompted. System will tell you exactly which file it needs (e.g., RAG_COLD_inventory.json).

**TS-005 — Conflict ledger growing but not resolving**
- Cause: User keeps deferring conflict resolution (choosing "keep active")
- Fix: Periodically review active conflicts. Choose investigate or resolve. Unresolved conflicts accumulate token cost every time COLD loads.

**TS-006 — Session closes without audit**
- Cause: User closes browser tab or session times out before §17 audit completes
- Fix: Always use explicit session close. Say "close session" to trigger the audit protocol. System will checkpoint and produce the session summary before closing.

**TS-007 — Cross-platform RAG mismatch (Claude ↔ GPT)**
- Cause: RAG files modified on one platform but not synced to the other
- Fix: After every session, ensure the latest RAG_MASTER.json (and any modified COLD files) are copied to your shared project folder. Both platforms must read from the same source of truth.

---

## 4. Known Limitations (Non-Resolvable by Users)

**LIM-001 — No atomic writes on GPT Web**
- Platform constraint. GPT Web has no filesystem access. All writes are simulated via download/upload.
- Cannot guarantee write-then-verify pattern. If browser crashes mid-download, state is lost.
- Resolution path: v3.2 OS-Level Runtime will provide filesystem bridge.

**LIM-002 — No real token counter on GPT Web**
- Platform constraint. ChatGPT does not expose context window usage to the model.
- Token pressure thresholds (75% warn, 80% halt) are estimates only.
- Resolution path: Platform-level API improvement (external dependency).

**LIM-003 — Context window ceiling (~128K tokens) limits operational depth**
- Architectural constraint. Spec itself consumes ~16K tokens. Large COLD loads + conversation history compress the working space.
- Deep analytical tasks with full COLD + active reasoning can hit ceiling.
- Resolution path: v3.2 OS-Level Runtime offloads COLD management to system RAM.

**LIM-004 — No background persistence on GPT Web**
- Platform constraint. Every state change requires explicit user action (download file).
- Between sessions, no process maintains state integrity.
- Resolution path: v3.2 background daemon.

**LIM-005 — WAL is advisory-only on GPT Web**
- Platform constraint. WAL entries are generated but cannot be written to disk automatically.
- WAL replay at recovery depends on user having saved the snapshot log.
- Resolution path: v3.2 OS-Level Runtime with real WAL filesystem writes.

**LIM-006 — Cross-platform partition sync is manual**
- Architectural limitation. No built-in sync mechanism between Claude and GPT instances.
- Users must manually ensure both platforms read from identical files.
- Resolution path: v3.2 centralized filesystem bridge or v4.0 shared state server.

---

## 5. Enhancement / Improvement Opportunities

**ENH-001 — Formal verification of state transitions**
- Current: State machine transitions are spec-defined but LLM-enforced (honor system in autonomous mode)
- Enhancement: Implement formal transition guards as executable predicates in the Python runtime kernel
- Benefit: Provably correct state transitions — eliminates entire class of spec-violation bugs
- Priority: HIGH — foundational for ENFORCED mode

**ENH-002 — MCP bridge layer for GPT Web**
- Current: GPT Web has no filesystem access — all persistence is manual
- Enhancement: Build an MCP server that runs locally, exposing filesystem operations to GPT via API
- Benefit: Eliminates LIM-001 through LIM-005 for GPT Web users
- Priority: HIGH — directly addresses most GPT Web limitations
- Note: This is effectively the v3.2 OS-Level Runtime

**ENH-003 — Automated COLD partition routing**
- Current: System knows which partition to load but relies on user to provide it (GPT Web)
- Enhancement: Partition manifest in HOT that maps query types to partition files, with pre-fetch hints
- Benefit: Reduces user round-trips on platforms without filesystem access

**ENH-004 — Graduated POV enforcement**
- Current: POV is all-or-nothing (strict or not configured)
- Enhancement: Support "advisory" mode where POVs generate analysis but don't block decisions
- Benefit: Lower barrier to entry for new users; addresses DEF-001

**ENH-005 — Conflict auto-categorization**
- Current: All conflicts require manual user decision
- Enhancement: Auto-classify conflicts by type (temporal drift, source disagreement, data quality) with suggested resolution paths
- Benefit: Faster conflict throughput; reduces user decision fatigue

**ENH-006 — Session checkpoint compression**
- Current: Full HOT state saved at each checkpoint
- Enhancement: Delta-only checkpoints (save only what changed since last checkpoint)
- Benefit: Reduced token cost for checkpoint operations; faster save cycles

**ENH-007 — Integrity hash verification in autonomous mode**
- Current: SHA-256 hashes computed but not automatically verified cross-session on GPT Web
- Enhancement: Hash verification step at boot that compares computed hash vs stored hash
- Benefit: Detects file tampering or corruption at load time, not at use time

**ENH-008 — Archive extraction with depth control**
- Current: Archives cataloged but not extracted (DEF-003)
- Enhancement: Post-scan prompt offering extraction with configurable depth (top-level only, recursive, selective)
- Benefit: Users can explore archive contents without manual extraction; token warning included

---

## 6. Test Coverage Summary

| Domain | Tests Run | Result | Notes |
|---|---|---|---|
| Environment detection | 5 | 5 PASS | Autonomous mode correctly identified |
| Spec parsing | 4 | 4 PASS | All 40 sections, version, schema validated |
| State machine | 4 | 4 PASS | Transitions, invalid paths, WAL rules correct |
| Schema validation | 5 | 2 PASS, 3 SKIP | SKIPs expected (no RAG uploaded for D1-D3) |
| Proposal contract | 3 | 3 PASS | Fields, risk logic, autonomous role correct |
| Policy rules | 8 | 8 PASS | Roots, tiers, thresholds, conflicts, triggers |
| Recovery | 2 | 2 PASS | All 7 steps correct, no silent proceed |
| Cross-platform | 2 | 2 PASS | Persistence mode correctly identified |
| Completion standard | 1 | 1 PASS | All 11 conditions enumerated |
| **Simulation** | | | |
| Bootstrap e2e | Full | PASS | Session-zero through pointer block |
| Session 2 boot | Full | PASS | HOT load, tool verify, fallback chain |
| Ingestion pipeline | Full | PASS | Scan, classify, extract, inventory |
| Backup recovery | Full | PASS | Corruption detect, .bak restore, WAL verify |
| Concurrency conflict | Full | PASS | Detect, halt, diff, resolve |
| COLD partitioning | Full | PASS | Split, cross-ref, lazy load, chopping |
| Token pressure | Full | PASS | Threshold, checkpoint, compression |
| Conflict ledger | Full | PASS | Detect, register, POV evaluate, resolve |
| Drift detection | Full | PASS | Timeline, classification, model update |
| Adversarial poisoning | Full | PASS | Quarantine, POV review, reject |
| Cross-platform desync | Full | PASS | Detect divergence, halt, resync |
| POV loop resolution | Full | PASS | Stagnation detect, escalation, data injection |
| WAL partial replay | Full | PASS | Missing segment detect, COLD reconstruct |
