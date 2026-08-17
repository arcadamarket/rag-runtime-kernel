# CLAUDE.md — RAG Runtime Kernel

Transferred out of Cowork at S200 (2026-08-17). READ SECTION 2 FIRST: several
long-standing P1 defects in this project may be fixed by the mere fact that you
are Claude Code and not Cowork.

---

## 1. BOOT — your FIRST action every session, no exceptions

```bash
cd RAG && python3 -m rag_kernel session-start --rag RAG_MASTER.json
# then, with the token it prints:
python3 -m rag_kernel session-start S<NNN> --attest <TOKEN>
```

Non-negotiable:

- Do NOT read RAG_MASTER.json directly to load or report state. session-start is
  the only sanctioned boot read path; a direct read is an E-071 class violation.
- If session-start REFUSES, run the recovery it NAMES (session-resume, or
  session-end for the prior SID). Never --force unless the operator directs it.
- READ THE HEAD OF THE OUTPUT, NOT THE TAIL. The kernel prints its VERDICT first
  and its receipts after. S200 piped session-end to a file, read it back with
  tail, missed the refusal sitting on line 1, and reported a seal that had not
  happened. Bound output from the head.
- Long jobs: launch DETACHED to a file, then ONE blocking wait:
  python3 -m rag_kernel wait-for <file> --timeout N --contains DONE --emit 20
  Never poll a running command (E-081).
- Before authoring anything new: python3 -m rag_kernel reuse-check (Rule 25).
  129 assets are already baked.

## 2. WHY THIS TRANSFER MATTERS — read before planning anything

S195 through S199 built a full PreToolUse/PostToolUse enforcement layer and wired
it in .claude/settings.json. IT HAS NEVER FIRED ONCE. Cowork does not read that
file. YOU DO.

Same story for .mcp.json at the project root: it registers the rag-kernel MCP
server (13 tools: rag_boot, rag_wait, rag_checkpoint, rag_close, rag_commit, ...),
verified over stdio at S198, never loaded by any client. YOU READ IT.

So on your first session, VERIFY — do not assume:

1. Is mcp__rag-kernel__ in your tool list? If yes, ACTIVATION-GAP-S197 (P1, open
   since S197) can finally close. Its resolve condition is exact and unchanged:
   a live rag_wait call returning FOUND through the client. Never a config that
   merely looks right. MCP-REGISTRATION-SURFACE-S199 closes with it.
2. Do the hooks fire? Make one tool call, then read:
   cat ~/.rag_kernel_hooks/hook_heartbeat.json
   If it shows "source": "hook_entry", the layer is LIVE for the first time in
   its existence. Then DELETE the $hook_layer_status key from
   .claude/settings.json: that re-arms the hard ERROR gate and converts a
   withdrawn coverage claim back into real enforcement.
3. If 1 and 2 hold, E-128 (polling) and E-134 (transport allowlist) become
   GATEABLE without building RESIDENT-SUPERVISOR first. That reorders section 4.

## 3. STATE AS TRANSFERRED (measured, not asserted)

| Fact | Value |
|---|---|
| git HEAD | a0a10b9, pushed, worktree clean |
| suite | 2,818 green, measured at a0a10b9 |
| S200 | SEALED, transfer_ready=true, phase COMPLETE |
| audit | 0 errors, 2 warnings |
| live items | 83 (68 open tasks, 7 deferred), 12 at P1 |
| runtime | 0.4.59, spec 3.2.8 |
| baked assets | 129 |

Operator verb, one line, exit 0 or 1:

```bash
cd RAG && python3 -m rag_kernel status --verbose
```

WARNING: that verb is INCOMPLETE. Built at S200, it is missing two terms its own
specification (OPERATOR-ONE-NUMBER) requires: an unsealed-prior-session term and
the ungated-terminal term. It printed GREEN over an unsealed session at S200.
Do not trust it for transfer readiness until Wave 2 item 1 lands.

## 4. WHAT IS OWED

Wave 1 — RESIDENT-SUPERVISOR (P1). Kernel as a resident MCP server owning the
lock, so poll and transport discipline become kernel-side REFUSALS instead of
prose. A gate can only refuse a call that passes through it. RE-EVALUATE THIS
FIRST: if section 2 checks pass, the hook layer already gives you refusal and
this drops in priority behind the mechanical work.

Wave 2 — mechanical, no dependencies:
  1. operator_status.py: add term_seal (transfer_ready of the prior session) and
     the ungated-terminal term. Corrects known-incomplete S200 work.
  2. resolve must REFUSE terminal status unless the item cites a gate that has
     FIRED, or an explicit detector-only-cannot-be-gated-because-X. Forward-only
     from S201 so the count starts at 0. This is the rule under which
     AGENT-POLL-DISCIPLINE could never have been signed off.
  3. session-end ordering: it writes SESSION_DELTA_S<SID>.md AFTER the last
     bootmap reseal, so every clean close hands the next session a map_coverage
     ERROR. Reseal the map after the artifacts are written.
  4. GATE-OR-HOPE-PRINCIPLE: audit all 42 operating_protocol rules. Each is
     either gated or declared detector-only with a reason. Same disease as the
     hook layer, one level up.
  5. INFERENCE-LEDGER-NO-LIFECYCLE: 48 entries carry no status key.

Wave 3 — PLAN-FEASIBILITY-GATE, SESSION-END-RESUME-HANDOFF, E-117
re-adjudication, E-132 (S199 HANDOFF-CLAIMS-GATE may already satisfy it; needs
evidence, not assertion).

## 5. THE DIAGNOSIS THAT PRODUCED THIS FILE

Turn point measured from tracked_items history: S191 closed 126 items. S192
through S199 opened 72 and closed 14 — net +58. Progress stopped and errors
compounded for eight sessions.

Root cause, measured at S200: tests/test_hook_enforcement_layer.py called
hook_entry.main with no state-dir override, so EVERY pytest run stamped the
PRODUCTION liveness heartbeat that drift_audit.check_hook_layer_live reads. THE
SUITE WAS MANUFACTURING THE EVIDENCE THE AUDIT CONSUMED. A hook layer that had
never executed once reported full coverage for eight sessions, and running the
suite actively destroyed the evidence of its own inertness.

Closed at S200 by: heartbeat provenance (only source=hook_entry proves liveness,
unlabelled counts as forged); tests/conftest.py autouse fixture pinning
RAG_HOOK_STATE_DIR; and the DECLARED-INERT mechanism that lets a coverage claim
be withdrawn explicitly, dated and reasoned, and re-arms to ERROR when revoked.

THE RULE THAT FELL OUT OF IT, and the one to defend above all others:
A TEST MAY NEVER WRITE A FACT THE AUDIT READS.

## 6. TRAPS

- The deployed kernel is RAG/rag_kernel. The git worktree is GIT WORKTREES/
  rag-runtime-kernel. python -m rag_kernel run from RAG uses the DEPLOYED copy;
  the suite tests the WORKTREE copy. Mirror your edits or you will measure one
  program and ship another. status has a deploy-parity term for exactly this.
- transport_allowlist permits ^mcp__tmux-mcp__, ^mcp__rag-kernel__, the native
  file/Task/Web verbs, and Bash. NOTHING ELSE. S200 breached it about 20 times
  via Desktop_Commander and no gate stopped it (E-134, P1, open). Under Claude
  Code the hook layer can actually enforce this — see section 2.
- Uncommitted work is a defect, not a smaller version of committed work
  (E-109/E-123). The boot gate refuses on it.
- Do not close an item on a config that looks right. Close it on a live call
  that returns the expected result.

