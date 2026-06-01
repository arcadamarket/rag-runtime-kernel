"""Tests for the TLA+ -> Python guard generator (FV-PHASE3).

Coverage:
- Parser: states, terminal states, transition table, action discovery.
- EQUIVALENCE PROOF: generated transition table == the live runtime
  state_machine.TRANSITIONS. This is the core guarantee — the generated
  artifact is provably identical to the already-verified runtime table.
- Guard behavior: each generated guard enforces its model preconditions
  (positive and negative cases).
- Fail-loud: unrecognized preconditions raise UnsupportedPredicate.
- Determinism: regeneration is byte-stable; the committed file matches the model.
- Provenance: embedded SOURCE_SHA256 matches the .tla on disk.
- Registry: ACTION_GUARDS is complete and target-arity is correct.
"""

import hashlib
from pathlib import Path

import pytest

from rag_kernel import guardgen
from rag_kernel import generated_guards as gg
from rag_kernel.state_machine import TRANSITIONS as RUNTIME_TRANSITIONS

REPO_ROOT = Path(__file__).resolve().parent.parent
TLA_PATH = REPO_ROOT / "formal" / "RAGKernel.tla"
GENERATED_PATH = REPO_ROOT / "rag_kernel" / "generated_guards.py"

EXPECTED_ACTIONS = {
    "StageProposal", "CommitProposal", "RejectProposal", "ClearRejection",
    "DirectTransition", "Crash", "WALCompaction", "RecoveryComplete",
}


# ===== Fixtures =====

@pytest.fixture(scope="module")
def tla_text() -> str:
    return TLA_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def model(tla_text) -> guardgen.TlaModel:
    sha = hashlib.sha256(TLA_PATH.read_bytes()).hexdigest()
    return guardgen.parse_tla(tla_text, source_name="RAGKernel.tla",
                              source_sha256=sha)


# ===== Parser =====

class TestParser:
    def test_seven_states(self, model):
        assert set(model.states) == {
            "BOOTING", "READY", "INGESTING", "WORKING",
            "CHECKPOINTING", "CLOSING", "RECOVERY",
        }

    def test_terminal_states(self, model):
        assert model.terminal_states == ["CLOSING"]

    def test_all_actions_discovered(self, model):
        names = {a.name for a in model.actions}
        assert names == EXPECTED_ACTIONS

    def test_transition_table_keys(self, model):
        assert set(model.transitions.keys()) == set(model.states)

    def test_closing_is_terminal(self, model):
        assert model.transitions["CLOSING"] == frozenset()

    def test_ready_targets(self, model):
        assert model.transitions["READY"] == frozenset(
            {"INGESTING", "WORKING", "CHECKPOINTING", "CLOSING"}
        )

    def test_recovery_targets(self, model):
        assert model.transitions["RECOVERY"] == frozenset({"READY", "BOOTING"})

    def test_target_arity(self, model):
        by_name = {a.name: a for a in model.actions}
        # Actions whose preconditions reference `target`.
        assert by_name["StageProposal"].takes_target
        assert by_name["DirectTransition"].takes_target
        assert by_name["RecoveryComplete"].takes_target
        # Parameterless actions.
        assert not by_name["Crash"].takes_target
        assert not by_name["CommitProposal"].takes_target
        assert not by_name["WALCompaction"].takes_target

    def test_stage_proposal_preconditions(self, model):
        by_name = {a.name: a for a in model.actions}
        kinds = [p.kind for p in by_name["StageProposal"].preconditions]
        assert kinds == [
            "NOT_CRASHED", "PROPOSAL_STATUS_EQ", "STATE_IN_SET",
            "LEGAL_TRANSITION", "WALSEQ_LT_MAX",
        ]


# ===== EQUIVALENCE PROOF (core guarantee) =====

class TestEquivalenceWithRuntime:
    def test_generated_table_equals_runtime_table(self):
        """The generated transition table is identical to the live runtime
        TRANSITIONS dict (string-mapped). This proves the generator faithfully
        reproduces the already-verified runtime structure."""
        runtime_as_strings = {
            state.value: frozenset(t.value for t in targets)
            for state, targets in RUNTIME_TRANSITIONS.items()
        }
        assert gg.GENERATED_TRANSITIONS == runtime_as_strings

    def test_parsed_model_matches_generated_module(self, model):
        """The parsed model's transitions match what's emitted in the module."""
        assert gg.GENERATED_TRANSITIONS == dict(model.transitions)


# ===== Guard behavior =====

class TestStageProposalGuard:
    def test_enabled_in_ready_to_working(self):
        ctx = gg.KernelContext(state="READY", proposal_status="NONE")
        ok, reason = gg.guard_stage_proposal(ctx, "WORKING")
        assert ok and reason == ""

    def test_blocked_when_crashed(self):
        ctx = gg.KernelContext(state="READY", crashed=True)
        ok, reason = gg.guard_stage_proposal(ctx, "WORKING")
        assert not ok and "~crashed" in reason

    def test_blocked_when_proposal_pending(self):
        ctx = gg.KernelContext(state="READY", proposal_status="STAGED")
        ok, reason = gg.guard_stage_proposal(ctx, "WORKING")
        assert not ok and "proposalStatus = NONE" in reason

    def test_blocked_in_disallowed_state(self):
        ctx = gg.KernelContext(state="BOOTING")
        ok, reason = gg.guard_stage_proposal(ctx, "READY")
        assert not ok and "{READY, WORKING}" in reason

    def test_blocked_on_illegal_target(self):
        ctx = gg.KernelContext(state="READY")
        ok, reason = gg.guard_stage_proposal(ctx, "BOOTING")  # illegal from READY
        assert not ok and "IsLegalTransition" in reason

    def test_blocked_when_wal_full(self):
        ctx = gg.KernelContext(state="READY", wal_len=8, max_wal_seq=8)
        ok, reason = gg.guard_stage_proposal(ctx, "WORKING")
        assert not ok and "WALSeq < MaxWALSeq" in reason


class TestCommitProposalGuard:
    def test_enabled(self):
        ctx = gg.KernelContext(state="READY", proposal_status="STAGED",
                               proposal_target="WORKING")
        ok, _ = gg.guard_commit_proposal(ctx)
        assert ok

    def test_blocked_without_staged_proposal(self):
        ctx = gg.KernelContext(state="READY", proposal_status="NONE")
        ok, reason = gg.guard_commit_proposal(ctx)
        assert not ok and "proposalStatus = STAGED" in reason

    def test_blocked_on_illegal_staged_target(self):
        ctx = gg.KernelContext(state="READY", proposal_status="STAGED",
                               proposal_target="BOOTING")
        ok, reason = gg.guard_commit_proposal(ctx)
        assert not ok and "IsLegalTransition" in reason


class TestCrashGuard:
    def test_enabled_in_non_terminal(self):
        ctx = gg.KernelContext(state="WORKING")
        ok, _ = gg.guard_crash(ctx)
        assert ok

    def test_blocked_in_terminal(self):
        ctx = gg.KernelContext(state="CLOSING")
        ok, reason = gg.guard_crash(ctx)
        assert not ok and "CrashEligibleStates" in reason

    def test_blocked_when_already_crashed(self):
        ctx = gg.KernelContext(state="WORKING", crashed=True)
        ok, reason = gg.guard_crash(ctx)
        assert not ok and "~crashed" in reason


class TestRecoveryCompleteGuard:
    def test_enabled_to_ready(self):
        ctx = gg.KernelContext(state="RECOVERY", crashed=True)
        ok, _ = gg.guard_recovery_complete(ctx, "READY")
        assert ok

    def test_enabled_to_booting(self):
        ctx = gg.KernelContext(state="RECOVERY", crashed=True)
        ok, _ = gg.guard_recovery_complete(ctx, "BOOTING")
        assert ok

    def test_blocked_when_not_crashed(self):
        ctx = gg.KernelContext(state="RECOVERY", crashed=False)
        ok, reason = gg.guard_recovery_complete(ctx, "READY")
        assert not ok and "crashed" in reason

    def test_blocked_on_illegal_recovery_target(self):
        ctx = gg.KernelContext(state="RECOVERY", crashed=True)
        ok, reason = gg.guard_recovery_complete(ctx, "WORKING")
        assert not ok and "AllowedTargets(RECOVERY)" in reason


class TestWALCompactionGuard:
    def test_enabled(self):
        ctx = gg.KernelContext(state="WORKING", wal_len=2)
        ok, _ = gg.guard_wal_compaction(ctx)
        assert ok

    def test_blocked_with_too_few_entries(self):
        ctx = gg.KernelContext(state="WORKING", wal_len=1)
        ok, reason = gg.guard_wal_compaction(ctx)
        assert not ok and "Len(wal) >= 2" in reason

    def test_blocked_in_terminal(self):
        ctx = gg.KernelContext(state="CLOSING", wal_len=5)
        ok, reason = gg.guard_wal_compaction(ctx)
        assert not ok and "TerminalStates" in reason


class TestLegalTransitionHelper:
    def test_legal(self):
        assert gg.legal_transition("READY", "WORKING")

    def test_illegal(self):
        assert not gg.legal_transition("READY", "BOOTING")

    def test_terminal_has_no_exits(self):
        for s in gg.STATES:
            assert not gg.legal_transition("CLOSING", s)


# ===== Registry =====

class TestActionRegistry:
    def test_registry_complete(self):
        assert set(gg.ACTION_GUARDS.keys()) == EXPECTED_ACTIONS

    def test_registry_target_arity(self):
        target_takers = {n for n, (_, t) in gg.ACTION_GUARDS.items() if t}
        assert target_takers == {"StageProposal", "DirectTransition",
                                 "RecoveryComplete"}

    def test_registry_callables(self):
        for name, (fn, takes_target) in gg.ACTION_GUARDS.items():
            ctx = gg.KernelContext(state="READY")
            result = fn(ctx, "WORKING") if takes_target else fn(ctx)
            assert isinstance(result, tuple) and len(result) == 2
            assert isinstance(result[0], bool)


# ===== Fail-loud grammar =====

class TestFailLoud:
    def test_unknown_predicate_raises(self):
        with pytest.raises(guardgen.UnsupportedPredicate):
            guardgen._parse_predicate("someUnknownVar = 5")

    def test_unknown_named_set_raises(self):
        with pytest.raises(guardgen.UnsupportedPredicate):
            guardgen._py_expr(guardgen.Predicate("STATE_IN_NAMED", ("MadeUpSet",)))

    def test_recognized_predicates_do_not_raise(self):
        for conj in ["~crashed", "crashed", "proposalStatus = NONE",
                     "state = RECOVERY", "state \\in {READY, WORKING}",
                     "state \\in CrashEligibleStates",
                     "state \\notin TerminalStates",
                     "IsLegalTransition(state, target)",
                     "WALSeq < MaxWALSeq", "Len(wal) >= 2",
                     "target \\in AllowedTargets(RECOVERY)"]:
            guardgen._parse_predicate(conj)  # must not raise


# ===== Determinism + provenance =====

class TestDeterminismAndProvenance:
    def test_regeneration_is_byte_stable(self, tla_text):
        m1 = guardgen.parse_tla(tla_text, source_name="RAGKernel.tla",
                                source_sha256="abc")
        m2 = guardgen.parse_tla(tla_text, source_name="RAGKernel.tla",
                                source_sha256="abc")
        assert guardgen.emit_module(m1) == guardgen.emit_module(m2)

    def test_committed_file_matches_model(self, model):
        """The checked-in generated_guards.py is in sync with the model
        (this is the same check `guardgen --check` performs in CI)."""
        regenerated = guardgen.emit_module(model)
        on_disk = GENERATED_PATH.read_text(encoding="utf-8")
        assert on_disk == regenerated, (
            "generated_guards.py is stale — run "
            "`python -m rag_kernel.guardgen --tla formal/RAGKernel.tla "
            "--out rag_kernel/generated_guards.py`"
        )

    def test_embedded_sha_matches_source(self):
        disk_sha = hashlib.sha256(TLA_PATH.read_bytes()).hexdigest()
        assert gg.SOURCE_SHA256 == disk_sha
