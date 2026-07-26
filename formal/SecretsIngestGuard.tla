-------------------------- MODULE SecretsIngestGuard --------------------------
(***************************************************************************)
(* Formal model + proof for SECRETS-INGEST-GUARD (P1/G2, S144).            *)
(*                                                                          *)
(* IMPLEMENTATION (api.py).                                                 *)
(*   KernelApp.validate_secrets_ingest(proposal): the PROACTIVE half of     *)
(*   KA-SECRETS-BOUNDARY. It intercepts the INGESTING path (`propose`) so a  *)
(*   live credential can never be committed into HOT / loaded into context.  *)
(*   Mechanism: serialize the proposal payload, then test each DECLARED-     *)
(*   SECRET VALUE for verbatim (substring) membership in the serialized      *)
(*   payload. The candidate set comes from                                  *)
(*   drift_audit.collect_declared_secret_values(hot, project_root) -- the    *)
(*   SAME source of truth the audit-time check_secrets_boundary uses, so the *)
(*   proactive (ingest) and reactive (audit) boundaries can NEVER disagree   *)
(*   on what a secret is. On a hit the guard emits only the source location  *)
(*   + a `sha256:<12>` fingerprint -- NEVER the secret value itself, so the  *)
(*   guard cannot become the exfiltration path it defends against.          *)
(*                                                                          *)
(* ABSTRACTION.  A payload is modeled as a finite sequence of tokens. Each   *)
(* token is one of:                                                          *)
(*   Plain      an ordinary payload field (no secret)                        *)
(*   SecretVal  a field whose value equals a declared-secret VALUE verbatim  *)
(*   SecretLike a field that merely RESEMBLES a secret but is not a declared *)
(*              value (decoy -- must NOT trip the guard: no false positive)  *)
(* "Serialize + verbatim-membership over the whole payload" is modeled as    *)
(* existential membership of a SecretVal token anywhere in the sequence.     *)
(*                                                                          *)
(* GUARD  == scan the WHOLE serialized payload; refuse iff any declared-     *)
(*           secret VALUE is present verbatim.                               *)
(*                                                                          *)
(* MASTER THEOREM (P_Equiv): IngestOK(p) <=> Clean(p) for EVERY payload p,   *)
(* where Clean == carries no declared-secret value. Plus:                    *)
(*   P_BoundaryAgree : IngestOK(p) <=> AuditOK(p)  (proactive == reactive,   *)
(*                     the KA-SECRETS-BOUNDARY single-source-of-truth claim)  *)
(*   P_Redacted      : the emitted diagnostic never contains the secret.     *)
(* The naive first-field-only guard is refuted in SecretsIngestGuard_naive.  *)
(***************************************************************************)
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS Plain, SecretVal, SecretLike, Fingerprint, MaxLen

Tokens   == {Plain, SecretVal, SecretLike}
AllPayloads == UNION { [1..n -> Tokens] : n \in 0..MaxLen }

VARIABLE payload

(* --- ground-truth semantics --- *)
ContainsSecret(p) == \E i \in DOMAIN p : p[i] = SecretVal
Clean(p)          == ~ContainsSecret(p)

(* --- the chosen guard: scan the WHOLE serialized payload --- *)
IngestOK(p) == \A i \in DOMAIN p : p[i] # SecretVal

(* --- audit-time boundary: SAME source of truth => SAME predicate --- *)
AuditOK(p)  == \A i \in DOMAIN p : p[i] # SecretVal

(* --- redaction-safe diagnostic: on reject, emit only a fingerprint --- *)
Diagnostic(p) == IF IngestOK(p) THEN {} ELSE {Fingerprint}

(* --- the discarded naive guard: only checks the FIRST field --- *)
NaiveIngestOK(p) == (Len(p) = 0) \/ p[1] # SecretVal

(***************************************************************************)
(* Properties (checked as state invariants over the current payload).      *)
(***************************************************************************)
P_Sound         == ContainsSecret(payload) => ~IngestOK(payload)  \* secret never accepted
P_NoFalsePos    == Clean(payload)          => IngestOK(payload)    \* clean/decoy never blocked
P_Equiv         == IngestOK(payload) <=> Clean(payload)            \* MASTER characterization
P_BoundaryAgree == IngestOK(payload) <=> AuditOK(payload)          \* proactive == reactive
P_Redacted      == Fingerprint # SecretVal /\ SecretVal \notin Diagnostic(payload)

AllProps == P_Sound /\ P_NoFalsePos /\ P_Equiv /\ P_BoundaryAgree /\ P_Redacted

(* Refutation target: the naive first-field-only guard is UNSOUND -- a       *)
(* secret in any field other than the first slips through. Expected to FAIL  *)
(* with a counterexample in SecretsIngestGuard_naive.cfg.                    *)
NaiveSound == ContainsSecret(payload) => ~NaiveIngestOK(payload)

(***************************************************************************)
Init == payload \in AllPayloads
Next == UNCHANGED payload
Spec == Init /\ [][Next]_payload
=============================================================================
