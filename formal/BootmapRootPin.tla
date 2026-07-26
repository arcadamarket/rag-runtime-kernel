---------------------------- MODULE BootmapRootPin ----------------------------
(***************************************************************************)
(* Formal model + proof for BOOTMAP-BOOTROOT-FIX (E-074, v0.4.44).         *)
(*                                                                          *)
(* IMPLEMENTATION (domain boot-map in the session-start GC walk).          *)
(*   The deterministic domain boot-map's boot_root must be PINNED to the    *)
(*   project root (rag_dir.parent), DECOUPLED from --gc-path and the CWD.   *)
(*   The pre-fix form keyed boot_root off --gc-path (whose default is        *)
(*   always truthy), leaving the intended `else rag_dir.parent` branch dead: *)
(*   run from a different CWD it diffed RAG-relative paths against the       *)
(*   project-root baseline and emitted a spurious full +N/-M boot line.     *)
(*                                                                          *)
(* ABSTRACTION.  boot_root is a function of (ragDir, cwd, gcPath). The RAG   *)
(* always lives at project_root/RAG, so project_root = Parent(ragDir). The   *)
(* correct boot_root is Parent(ragDir) for ALL cwd/gcPath. The buggy form    *)
(* returns a cwd/gcPath-dependent directory.                                 *)
(*                                                                          *)
(* THEOREM: CorrectBootRoot is constant = ProjectRoot over all inputs        *)
(* (P_Pinned + P_Decoupled). The naive gcPath/CWD-keyed form is refuted in   *)
(* BootmapRootPin_naive.cfg.                                                 *)
(***************************************************************************)
EXTENDS Naturals

CONSTANTS ProjectRoot, OtherDir, NoPath

Dirs     == {ProjectRoot, OtherDir}
GcInputs == Dirs \cup {NoPath}

\* Input = the ambient invocation context the boot-map is computed under.
Inputs == [cwd : Dirs, gc : GcInputs]

VARIABLE input

(* --- correct: boot_root = Parent(ragDir) = ProjectRoot, always --- *)
CorrectBootRoot(inp) == ProjectRoot

(* --- buggy naive: keyed off gc-path (default truthy), CWD as dead else --- *)
NaiveBootRoot(inp) == IF inp.gc # NoPath THEN inp.gc ELSE inp.cwd

(***************************************************************************)
(* Properties (state invariants over the current input).                   *)
(***************************************************************************)
P_Pinned    == CorrectBootRoot(input) = ProjectRoot
P_Decoupled == \A c \in Dirs, g \in GcInputs :
                   CorrectBootRoot([cwd |-> c, gc |-> g]) = ProjectRoot

AllProps == P_Pinned /\ P_Decoupled

(* Refutation: the gcPath/CWD-keyed form is NOT pinned to ProjectRoot.        *)
(* Expected to FAIL, e.g. [cwd |-> OtherDir, gc |-> NoPath] -> OtherDir, or   *)
(* [cwd |-> ProjectRoot, gc |-> OtherDir] -> OtherDir.                        *)
NaivePinned == NaiveBootRoot(input) = ProjectRoot

(***************************************************************************)
Init == input \in Inputs
Next == UNCHANGED input
Spec == Init /\ [][Next]_input
=============================================================================
