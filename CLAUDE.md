<!-- GENERATED FROM THE RAG by RAG/scripts/render_claude_md.py. DO NOT HAND-EDIT: a hand-edit makes this a parallel rule store (Rule 13 / E-039). Change the RAG, then re-render. -->

# CLAUDE.md — RAG Runtime Kernel

> **INTEGRITY WARNING — read before trusting anything below.**
> The canonical state does not match its own stored checksum:
> state_hash: stored=bd27b925865d0de2... computed=2578fe7f8eecba7f...
> Tracked as `STATE-HASH-STALE-AND-UNCHECKED-S202` (P1). `audit` and `verify` do not call `verify_hashes`, so they report
> clean over this. Every number in section 2 is read from that state.

Rendered from the RAG. Every fact below was read from a governed
store or measured live; none of it is typed into this file.

---

## 1. BOOT — first action of every session

```bash
cd "C:/Users/pakhol/Desktop/GitHub Project (RAG Runtime Kernel)/RAG" && python -m rag_kernel session-start
```

Then run the `--attest <TOKEN>` line it prints, verbatim. You are
booted at `Session S<NNN> READY`.

MEASURED INTERPRETER: `C:\Python314\python.exe`. Use `python`, never `python3` —
`python3` on this host is the Microsoft Store alias and exits
non-zero. Authority for every tool path is `toolchain/toolchain.json`.

- **session_start_protocol** — HARD RULE -- GC-FIRST CARRY-FORWARD RITUAL.
- **session_start_shell_rule** — HARD RULE: the FIRST shell/git/test action of every session goes through tmux-mcp (NOT wsl-exec, NEVER the Cowork sandbox); wsl-exec is the ATOMIC single-command fallback only.
- **tool_hierarchy** — {'file_read_write_list': 'File tools (primary for file CONTENT read/write) > tmux-mcp (real WSL shell for listing/scan) > wsl-exec (atomic).
- **tool_contract** — ALLOWED: read/list/write within declared roots, compute checksum, append WAL, rotate backup.
- **circuit_breaker** — Rule 5.
- **token_economy** — Rule 17 (TOKEN-ECONOMY / CONTEXT-EMISSION DISCIPLINE — KA-15, S108; source: eBay Session-Zero field incident, triaged UNIVERSAL per deployment_test_result_triage / Rule 15).
- **reuse_registry_guard** — Rule 25 (REUSE-REGISTRY-GUARD -- S165; eBay S12 field evidence, Rule 15 lane-A + S165 E-069 design-level instance).
- **strict_obey** — Rule 16 (STRICT-OBEY — S70/S79 operator directive; encoded via the governed add-rule verb).
- **retro_clarity** — Rule 43 (RETRO-CLARITY - operator directive, raised repeatedly against the Cowork agent from S190 onward and NEVER baked; encoded S202 via the governed add-rule verb after a live grep proved 0 occurrences anywhere in the RAG).
- **context_window_management** — COMPRESSION/COMPACTION FORBIDDEN.
- **increment_status_honesty** — Rule 14 (migrated S46; codifies a principle Rule 12 only REFERENCED but never defined).
- **root_hygiene** — Rule 20 (ROOT-HYGIENE — S116 origin; UPDATED S135 by operator standing directive granting auto-purge authority).

All 57 operating_protocol rules are rendered in full by `session-start`; the list above is only the boot-critical subset.

## 2. STATE (read from the RAG, measured where stated)

| Fact | Value |
|---|---|
| git HEAD | cdbbfd1 |
| runtime | see current_status |
| test gate | 2818  (session S202 @ cdbbfd1) |
| written_by_session | S202 |
| active items | 100 |
| P1 | 21 |
| baked assets | 129 |
| posix shell | C:\Program Files\Git\usr\bin\bash.EXE |
| tmux transport | wsl:tmux |
| TLC jar | C:\Users\pakhol\Desktop\GitHub Project (RAG Runtime Kernel)\toolchain\tla2tools.jar |

## 3. P1 — what is owed, in ledger order

- `CONTEXT-COMPACTION-FORBIDDEN-BUT-UNGATED-S203`
- `E-116`
- `E-117`
- `E-128`
- `E-132`
- `E-134`
- `GATE-FALSE-POSITIVE-ON-PROSE-S201`
- `GATE-OR-HOPE-PRINCIPLE`
- `GC-IS-INVERTED-S202`
- `GC-WOULD-DELETE-LIVE-TESTS-S202`
- `INFERENCE-LEDGER-NO-LIFECYCLE`
- `MEASUREMENT-PROVENANCE-S201`
- `OPERATOR-ONE-NUMBER`
- `PLAN-FEASIBILITY-GATE`
- `PROBE-DEFINES-ITS-OWN-PROTOCOL-S201`
- `RESOLVE-EVIDENCE-GATE-NOT-ENFORCED-S202`
- `SEAL-NOT-INVALIDATED-BY-LATER-WRITES-S201`
- `SELF-CERTIFYING-EVIDENCE-GATE-S201`
- `SESSION-END-RESUME-HANDOFF`
- `STATE-HASH-STALE-AND-UNCHECKED-S202`
- `WAIT-FOR-USED-AS-A-POLL-S198`

Full backlog: `python -m rag_kernel items`. Distribution: {'P1': 21, 'P2': 36, 'P3': 30, 'P4': 9, 'P5': 4}.

## 5. TRAPS (from operating_protocol)

- **github_deploy_method** — GitHub deploy = `git push origin main` from the worktree via tmux-mcp (credential store ~/.git-credentials, configured S30); wsl-exec atomic fallback; if WSL transport down, ask user to run in PowerShell. NEVER read the PAT file or write temp push scripts. Full detail: RAG_CONTEXT.json[leaned_rules].
- **transport_allowlist** — TRANSPORT-ALLOWLIST (E-133, S197) — DECLARED HERE, ENFORCED BY THE hook_guard `transport` GATE ONCE IT IS WIRED. TWO HONEST STATUS NOTES BEFORE THE POLICY, because a rule that overstates its own enforcement is the defect E-132 names and this rule was guilty of it in its first draft: (1) NOT LIVE AT AUTHORING. The gate exists, is unit-tested and passes selftest, but `.claude/settings.json` still ca

