# DESIGN — Governed scaffold transplant (`transplant` verb)

Status: **DESIGN ONLY — not implemented.** Authored S159 at operator direction after
the S159 finding that `KA-SCHEMA-MIGRATE` (meta/schema versions) does NOT and never
did cover scaffold transplant, and that the S148 diversion plan assumed it would.
Implementation is S160+ work. Sibling of `SPEC-PROMOTION-DRIFT` (E-063).

---

## 1. Problem

The kernel is deployed onto other projects. Over time a deployment's
`operating_protocol` diverges from this kernel's in two *different* ways that must
never be conflated:

- **Universal scaffold drift** — the deploy is missing governance rules/guards this
  kernel has since authored (it cannot benefit from fixes it never received).
- **Legitimate project divergence** — the deploy authored its own rules for its own
  domain (the eBay clone's reprice/Temu operational rules). These are NOT drift.
  They are the deployment's own value and must survive untouched.

Live case: the eBay clone runs **51** `operating_protocol` rules against this
kernel's **35**. A naive "sync the rules" would destroy the 16-rule delta that is
precisely the clone's own work. `KA-SCHEMA-MIGRATE` deliberately refuses to go near
this (PRESERVE-IN-PLACE, operator ruling D2, S158).

## 2. The blocking design decision (operator input required)

**How is a rule classified as universal scaffold vs project-specific?**

Three candidate authorities, in descending order of determinism:

| # | Authority | Mechanism | Verdict |
|---|---|---|---|
| A | **Spec-derived** | A rule is universal **iff** it appears in the INIT spec (`INIT_UNIVERSAL_RUNTIME_KERNEL_v<spec>.md`) that both sides can name. The spec IS the definition of "universal" — that is what the word means in this project. | **Recommended.** Deterministic, zero-token, no tagging debt, and it ties transplant to spec adoption, closing `SPEC-PROMOTION-DRIFT` from the same mechanism. |
| B | **Provenance-tagged** | Each rule carries `universal: true` at authoring time; transplant moves tagged rules only. | Deterministic going forward, but every rule authored before the tag existed is unclassified — a migration problem of its own. |
| C | **Name-matching** | Rule keys present in the source kernel are treated as universal. | **Reject.** A deploy that independently authored a rule under a colliding key would have it silently overwritten. Exactly the failure mode this project exists to prevent. |

Option A implies a hard precondition: **this kernel must first self-adopt spec
v3.2.7** (operator ruling D4, S159), because a transplant that reads the spec as its
authority cannot run from a kernel that is behind its own spec.

## 3. Contract (all options)

1. **Additive only.** Transplant may ADD a missing universal rule. It may never
   delete, reorder, or rewrite an existing rule in the target — including a
   universal rule the target has locally amended.
2. **Collision is fail-loud, never overwrite.** A universal rule whose key exists in
   the target with different content HALTS the run and reports the pair. Resolution
   is an operator ruling, not an agent default.
3. **Project-specific rules are invisible.** Anything not classified universal by the
   chosen authority is never read, moved, or reported as drift.
4. **Dry-run first, always.** `--dry-run` renders every planned addition and every
   collision line by line (STRICT-OBEY rendering discipline — never a bare count).
   The operator sees the full plan before any write.
5. **Atomic + audited.** Reuses the FIX-4 `tmp → verify → .bak parity → rename` path
   and appends a `meta.transplants` entry: source kernel version, spec version,
   session, rule ids added, collisions skipped.
6. **Idempotent.** A second run over the same pair is a no-op with no write.
7. **Direction is never assumed.** As with `migrate`: read the TARGET's own meta and
   spec version; a target ahead of the source on spec is refused, not downgraded.

## 4. Proposed surface

```
rag_kernel transplant --rag <TARGET RAG> --source <SOURCE RAG> \
                      --spec <INIT spec .md> --session <SID> [--dry-run]
```

Exit 0 on success/no-op; exit 1 on any fail-loud condition (unknown spec, collision,
target ahead, unclassifiable rule).

## 5. Test obligations

Mirrors `tests/test_schema_migrate.py` in shape:

- classification: spec-listed rule => universal; target-only rule => untouched
- collision on differing content => fail loud, nothing written
- additive: target's project-specific rules byte-identical after a real run
- idempotence: second run is a no-op
- dry-run writes nothing; `.bak` never created
- target ahead on spec => refused
- audit trail entry shape
- CLI registration + line-by-line render

## 6. Why this is not `migrate`

`migrate` moves **version fields and structural keys** — the shape of the RAG.
`transplant` moves **governance content** — the rules inside it. Conflating them is
what made the S148 plan assume one verb covered both. They stay separate verbs with
separate guards.

## 7. Open question for the operator

Confirm authority **A (spec-derived)**, or name another. Everything else in this
design is settled and implementable; the classification authority is the one call
that cannot be defaulted safely, because getting it wrong silently damages another
project's canonical state.
