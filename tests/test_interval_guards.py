"""S192 interval guards — E-123, E-124, E-125, E-126.

The operator's framing, which is the correct diagnosis: when state fails to reach
the next session there are exactly two causes. Either the agent disregarded a rule
the RAG already carried, or the RAG could not carry the fact at all. Every guard
tested here is one of those two, converted into a non-zero exit code.

**E-123 / SEAL-INTERVAL-RECHECK.** S191 spent an entire session on "the running
kernel exists in no commit" (E-109), built ``CLOSE-TESTGATE-STALE-BLOCKS`` (E-115)
to stop it, passed that gate, then edited ``__main__.py`` while the close was still
running and sealed COMPLETE over an uncommitted kernel. E-115 was not wrong — it
evaluates the gate when the close BEGINS, and it was correct at that instant. It
simply says nothing about the interval between that instant and the write of
``transfer_ready``. The probes therefore run a second time with nothing between
them and the seal.

**E-124 / BOOT-TESTGATE-STALE-BLOCKS.** S191 told the operator, in prose, that the
S192 boot would refuse on a stale test gate. It would not have. The staleness check
existed only in ``grand_audit`` axis 2, which the boot does not run. The fact was in
canonical state (``meta.test_gate``) the entire time and nothing on the startup path
ever asked for it. This is the DISREGARDED half: a rule that only prints is a rule
an agent can decline to read.

**E-125 / ORPHAN-ENUM-BLOCKS.** S191 wrote E-111, E-114 and E-115 into ERROR_LOG.md
and cited them in source comments and commit messages without ever creating the
tracked items. Prose does not transfer between sessions; tracked items do. This is
the UNCARRIED half: three identifiers that pointed at nothing for a full session.

**E-126 / DEPLOY-PARITY.** ``RAG/rag_kernel/`` (what executes when the agent types
``python3 -m rag_kernel``) and the git worktree's ``rag_kernel/`` are two separate
copies of the same source. The RAG had no representation of their divergence at all,
so a clean ``git status`` could certify a tree that was not the one running.

Every probe FAILS CLOSED, and the first test class below exists because the first
implementation did not: passing a relative ``rag_path`` made ``_resolve_git_head``
and ``resolve_repo_root`` return nothing, and all four probes silently returned
"no findings" against a RAG that was provably stale AND provably dirty. A guard
that answers "nothing wrong" when it cannot look is worse than no guard, because it
manufactures the evidence of safety.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from rag_kernel.__main__ import (
    _carry_forward_gate,
    _declares_git_deployment,
    _finding_is_repairable,
    _interval_probes,
    _probe_deploy_parity,
    _probe_orphan_enums,
    _probe_test_gate,
    _probe_worktree_clean,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _write_rag(path, *, stamp=None, docs_root=None, items=()):
    meta = {"written_by_session": "S192", "runtime_version": "0.4.46"}
    if stamp is not None:
        meta["test_gate"] = stamp
    if docs_root is not None:
        meta["reconciliation_docs_root"] = docs_root
    path.write_text(
        json.dumps({"tracked_items": list(items), "meta": meta}), encoding="utf-8"
    )
    return path


def _green_stamp(head="abc1234", passed=2569):
    return {
        "passed": passed, "failed": 0, "skipped": 0, "collected": passed,
        "exit_code": 0, "measured_utc": "2026-08-10T20:00:00Z", "session": "S191",
        "runtime": "0.4.46", "git_head": head,
    }


@pytest.fixture()
def kernel_repo(tmp_path):
    """A real git worktree holding tests/ and rag_kernel/, plus a RAG/ deployment.

    Mirrors the live layout: the project root holds BOTH a ``RAG/`` deployment and
    a separate worktree, each with its own copy of ``rag_kernel/``.
    """
    root = tmp_path / "project"
    repo = root / "worktree"
    (repo / "tests").mkdir(parents=True)
    (repo / "rag_kernel").mkdir(parents=True)
    (repo / "rag_kernel" / "__main__.py").write_text("VERSION = 1\n", encoding="utf-8")
    rag_dir = root / "RAG"
    (rag_dir / "rag_kernel").mkdir(parents=True)
    (rag_dir / "rag_kernel" / "__main__.py").write_text("VERSION = 1\n", encoding="utf-8")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=str(repo), capture_output=True, text=True, check=False
        )

    git("init", "-q")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    head = git("rev-parse", "--short", "HEAD").stdout.strip()
    rag = _write_rag(rag_dir / "RAG_MASTER.json", stamp=_green_stamp(head),
                     docs_root="worktree")
    return {"root": root, "repo": repo, "rag_dir": rag_dir, "rag": rag,
            "head": head, "git": git}


# ---------------------------------------------------------------------------
# The bug the guards were born with: failing OPEN when they cannot look.
# ---------------------------------------------------------------------------
class TestProbesFailClosed:
    def test_relative_rag_path_still_detects_a_dirty_tree(self, kernel_repo, monkeypatch):
        """The exact first-run defect: relative path -> unresolvable -> silent pass."""
        (kernel_repo["repo"] / "rag_kernel" / "__main__.py").write_text(
            "VERSION = 2\n", encoding="utf-8"
        )
        monkeypatch.chdir(kernel_repo["rag_dir"])
        from pathlib import Path

        findings = _interval_probes(Path("RAG_MASTER.json"), rag_dir=Path("."))
        assert any(f.startswith("worktree:") for f in findings), (
            "a relative rag_path must not disarm the probes — that is how the first "
            "implementation reported a clean tree over an uncommitted kernel"
        )

    def test_unresolvable_repo_refuses_when_a_kernel_is_deployed(self, tmp_path):
        """A kernel package beside the RAG is the claim; an unreachable repo is a defect."""
        (tmp_path / "rag_kernel").mkdir()
        (tmp_path / "rag_kernel" / "__main__.py").write_text("x = 1\n", encoding="utf-8")
        rag = _write_rag(tmp_path / "RAG_MASTER.json", stamp=_green_stamp())
        assert _declares_git_deployment(rag) is True
        finding = _probe_worktree_clean(rag)
        assert finding is not None and finding.startswith("worktree:")
        assert "refuses instead of passing" in finding

    def test_no_deployed_kernel_means_nothing_to_prove(self, tmp_path):
        """Fixture RAGs with no kernel beside them stay bootable — by physical fact.

        The predicate deliberately does NOT read a declaration. Two earlier
        versions keyed off ``meta.reconciliation_docs_root``, which names the docs
        tree the close reconciles against — a different claim entirely — and so
        refused every KA-13 fixture. Asking the RAG to describe itself and then
        trusting the description is the same shape as trusting a stamp without
        checking the head.
        """
        rag = _write_rag(tmp_path / "RAG_MASTER.json", stamp=_green_stamp(),
                         docs_root="/some/where")
        assert _declares_git_deployment(rag) is False
        assert _probe_worktree_clean(rag) is None
        assert _probe_deploy_parity(rag) is None
        assert _probe_test_gate(rag) is None, (
            "the gate probe asks whether the kernel deployed HERE was proven; "
            "where none is deployed there is nothing to answer for"
        )

    def test_unresolvable_head_refuses_rather_than_grading_against_nothing(
        self, tmp_path, monkeypatch
    ):
        """`verdict` only compares when GIVEN a live head; None would grade True."""
        import rag_kernel.__main__ as M

        (tmp_path / "rag_kernel").mkdir()
        (tmp_path / "rag_kernel" / "__main__.py").write_text("x = 1\n", encoding="utf-8")
        rag = _write_rag(tmp_path / "RAG_MASTER.json", stamp=_green_stamp())
        monkeypatch.setattr(M, "_resolve_git_head", lambda *_a, **_k: None)
        finding = M._probe_test_gate(rag)
        assert finding is not None
        assert "unresolvable" in finding


# ---------------------------------------------------------------------------
# E-124 — the boot refuses on a stale gate (the promise S191 made and missed)
# ---------------------------------------------------------------------------
class TestBootTestGate:
    def test_stale_stamp_is_a_finding(self, kernel_repo):
        _write_rag(kernel_repo["rag"], stamp=_green_stamp(head="deadbee"),
                   docs_root="worktree")
        finding = _probe_test_gate(kernel_repo["rag"])
        assert finding is not None
        assert "STALE" in finding
        assert "tests --run" in finding, "a refusal must name its repair"

    def test_unmeasured_stamp_is_a_finding(self, kernel_repo):
        _write_rag(kernel_repo["rag"], docs_root="worktree")
        finding = _probe_test_gate(kernel_repo["rag"])
        assert finding is not None and "UNMEASURED" in finding

    def test_red_stamp_is_a_finding(self, kernel_repo):
        stamp = _green_stamp(kernel_repo["head"])
        stamp["failed"] = 3
        _write_rag(kernel_repo["rag"], stamp=stamp, docs_root="worktree")
        assert _probe_test_gate(kernel_repo["rag"]) is not None

    def test_green_and_current_passes(self, kernel_repo):
        assert _probe_test_gate(kernel_repo["rag"]) is None

    def test_the_boot_gate_actually_consults_it(self, kernel_repo):
        """The whole point of E-124: not that a checker exists, that the BOOT asks."""
        _write_rag(kernel_repo["rag"], stamp=_green_stamp(head="deadbee"),
                   docs_root="worktree")
        ok, findings = _carry_forward_gate(
            kernel_repo["rag"], rag_dir=kernel_repo["rag_dir"], new_sid="S193"
        )
        assert ok is False
        assert any("test gate:" in f for f in findings)


# ---------------------------------------------------------------------------
# E-125 — a cited E-number with no tracked item behind it
# ---------------------------------------------------------------------------
class TestOrphanEnums:
    def test_cited_but_unbanked_is_a_finding(self, kernel_repo):
        (kernel_repo["rag_dir"] / "ERROR_LOG.md").write_text(
            "**E-111 — auditor calls charged to the agent.**\n"
            "**E-115 — CLOSE-TESTGATE-STALE-BLOCKS.**\n",
            encoding="utf-8",
        )
        finding = _probe_orphan_enums(kernel_repo["rag"], kernel_repo["rag_dir"])
        assert finding is not None
        assert "E-111" in finding and "E-115" in finding
        assert "rag_kernel add" in finding, "a refusal must name its repair"

    def test_banked_ids_pass(self, kernel_repo):
        (kernel_repo["rag_dir"] / "ERROR_LOG.md").write_text(
            "**E-111 — banked.**\n", encoding="utf-8"
        )
        _write_rag(kernel_repo["rag"], stamp=_green_stamp(kernel_repo["head"]),
                   docs_root="worktree",
                   items=[{"id": "E-111", "status": "RESOLVED", "title": "banked"}])
        assert _probe_orphan_enums(kernel_repo["rag"], kernel_repo["rag_dir"]) is None

    def test_absent_error_log_is_not_a_failure(self, kernel_repo):
        assert _probe_orphan_enums(kernel_repo["rag"], kernel_repo["rag_dir"]) is None


# ---------------------------------------------------------------------------
# E-123 — the uncommitted worktree, measured at the instant it matters
# ---------------------------------------------------------------------------
class TestWorktreeClean:
    def test_clean_tree_passes(self, kernel_repo):
        assert _probe_worktree_clean(kernel_repo["rag"]) is None

    def test_modified_file_is_a_finding(self, kernel_repo):
        (kernel_repo["repo"] / "rag_kernel" / "__main__.py").write_text(
            "VERSION = 2\n", encoding="utf-8"
        )
        finding = _probe_worktree_clean(kernel_repo["rag"])
        assert finding is not None
        assert "__main__.py" in finding
        assert "E-109/E-123" in finding, "the finding must name its own history"

    def test_untracked_file_is_a_finding(self, kernel_repo):
        (kernel_repo["repo"] / "rag_kernel" / "new_module.py").write_text(
            "x = 1\n", encoding="utf-8"
        )
        assert _probe_worktree_clean(kernel_repo["rag"]) is not None


# ---------------------------------------------------------------------------
# E-126 — the deployed kernel is not the committed kernel
# ---------------------------------------------------------------------------
class TestDeployParity:
    def test_identical_trees_pass(self, kernel_repo):
        assert _probe_deploy_parity(kernel_repo["rag"]) is None

    def test_divergent_content_is_a_finding(self, kernel_repo):
        """The scenario a clean `git status` cannot see: only the DEPLOYED copy moved."""
        (kernel_repo["rag_dir"] / "rag_kernel" / "__main__.py").write_text(
            "VERSION = 99\n", encoding="utf-8"
        )
        assert _probe_worktree_clean(kernel_repo["rag"]) is None, (
            "precondition: git is clean, because the edit landed outside the worktree"
        )
        finding = _probe_deploy_parity(kernel_repo["rag"])
        assert finding is not None
        assert "__main__.py" in finding

    def test_a_file_present_in_only_one_tree_is_a_finding(self, kernel_repo):
        (kernel_repo["rag_dir"] / "rag_kernel" / "extra.py").write_text(
            "y = 2\n", encoding="utf-8"
        )
        finding = _probe_deploy_parity(kernel_repo["rag"])
        assert finding is not None and "extra.py" in finding


# ---------------------------------------------------------------------------
# None of the four may ever be auto-repaired
# ---------------------------------------------------------------------------
class TestNeverAutoRepairable:
    @pytest.mark.parametrize(
        "finding",
        [
            "test gate: 2,569 green — STALE (measured @ d5cd8e6, live @ a999735)",
            "orphan enums: ERROR_LOG.md cites 3 of 121 E-numbers with no tracked item",
            "worktree: 1 uncommitted change(s) in rag-runtime-kernel — M __main__.py",
            "deploy parity: 1 kernel source(s) differ between the DEPLOYED tree",
        ],
    )
    def test_interval_findings_are_asserted_state(self, finding):
        """Auto-repairing any of these converts a refusal into a cover-up.

        Re-running a suite, banking an id, committing a tree and syncing a
        deployment are acts with content. GATE-AUTO-RECONCILE exists to repair
        DERIVED state — things canonical already holds — and none of these is that.
        """
        assert _finding_is_repairable(finding) is False
