"""SCRATCH-OUTSIDE-ROOT-S205 — the host scratchpad is not project storage.

OPERATOR DIRECTIVE: nothing this project depends on may live outside the project
root. MEASURED S205: the successor wrote boot.txt / boot_status.txt / git.txt
into the Claude host scratchpad; S201-S202 did the same with gate_check.py and a
rule draft. NEITHER AGENT WAS BEING CARELESS — the host system prompt instructs
agents to put temporary files there, and no RAG rule said otherwise. A conflict
that is not encoded is decided by whoever speaks last, so the RAG now speaks
last, mechanically.

These tests pin the gate's PRECISION as hard as its bite, because a gate that
fires on a sibling project's files, or on the empty directory the harness itself
creates every session, is a gate that gets switched off:

  * the slug is the harness's own (abs path, non-alphanumerics -> '-');
  * an EMPTY scratchpad is clean — the harness makes it, the agent fills it;
  * another project's scratch tree is never read and never reported;
  * SEVERITY FOLLOWS WHO CAN ACT (S206 operator ruling): a dead session's
    leftovers are ONE WARNING per directory, because the agent may not delete
    outside root_project and the host recreates that tree every session — an
    ERROR there deadlocks both the seal and the next boot, permanently;
  * the roots are manifest-overridable and additive, never replacing the defaults;
  * the scan is bounded and survives an unreadable directory.
"""

from __future__ import annotations

import re
from pathlib import Path

from rag_kernel import drift_audit
from rag_kernel.drift_audit import (
    ERROR,
    WARNING,
    check_host_scratch_storage,
    find_host_scratch_files,
    host_scratch_roots,
    host_scratch_slug,
)


def _make_pad(base: Path, project: Path, session: str = "sess-1") -> Path:
    """Build <base>/claude/<slug-of-project>/<session>/scratchpad/ and return it."""
    pad = base / "claude" / host_scratch_slug(project) / session / "scratchpad"
    pad.mkdir(parents=True)
    return pad


def _hot(base: Path) -> dict:
    return {"meta": {"host_scratch_roots": [str(base)]}}


# ---------------------------------------------------------------------------
# the slug — the harness's contract, pinned
# ---------------------------------------------------------------------------

def test_slug_replaces_every_non_alphanumeric_with_a_dash(tmp_path):
    slug = host_scratch_slug(tmp_path)
    assert re.fullmatch(r"[A-Za-z0-9-]+", slug)


def test_slug_matches_the_live_harness_shape():
    """Verified S206 against the path this project actually boots from: the
    harness slugs the ABSOLUTE path, one dash per non-alphanumeric character —
    spaces, parentheses, separators and the drive colon alike."""
    got = re.sub(r"[^A-Za-z0-9]", "-", r"C:\Users\p\Desktop\GitHub Project (RAG Runtime Kernel)")
    assert got == "C--Users-p-Desktop-GitHub-Project--RAG-Runtime-Kernel-"


# ---------------------------------------------------------------------------
# precision
# ---------------------------------------------------------------------------

def test_empty_scratchpad_is_clean(tmp_path):
    """The harness creates this directory for EVERY session, used or not."""
    project = tmp_path / "proj"
    project.mkdir()
    _make_pad(tmp_path / "hosttmp", project)
    assert find_host_scratch_files(project, _hot(tmp_path / "hosttmp")) == []
    assert check_host_scratch_storage(project, _hot(tmp_path / "hosttmp")) == []


def test_no_scratch_tree_at_all_is_clean(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    assert check_host_scratch_storage(project, _hot(tmp_path / "nothing-here")) == []


def test_another_projects_scratch_is_never_reported(tmp_path):
    project = tmp_path / "proj"
    other = tmp_path / "someone-elses-project"
    project.mkdir()
    other.mkdir()
    base = tmp_path / "hosttmp"
    (_make_pad(base, other) / "their_notes.txt").write_text("x", encoding="utf-8")
    assert check_host_scratch_storage(project, _hot(base)) == []


def test_files_outside_the_scratchpad_leaf_are_not_reported(tmp_path):
    """The harness's own bookkeeping (tasks/*.output) is not agent scratch."""
    project = tmp_path / "proj"
    project.mkdir()
    base = tmp_path / "hosttmp"
    _make_pad(base, project)
    tasks = base / "claude" / host_scratch_slug(project) / "sess-1" / "tasks"
    tasks.mkdir()
    (tasks / "job.output").write_text("x", encoding="utf-8")
    assert check_host_scratch_storage(project, _hot(base)) == []


# ---------------------------------------------------------------------------
# bite
# ---------------------------------------------------------------------------

def test_a_foreign_scratchpad_is_one_warning_per_directory_not_per_file(tmp_path):
    """S206 operator ruling: SEVERITY FOLLOWS WHO CAN ACT.

    One ERROR per file bricked the project — 20 leftover files in a DEAD host
    session produced 33 audit errors; session_end_protocol aborts the seal on a
    dirty audit and the carry-forward gate refuses the next boot, so the project
    could be neither opened nor closed until a human deleted files the agent is
    barred from touching. The host recreates that tree every session, so the
    deadlock was permanent. A guard only a human can clear is the manual rescue
    Rule 45 exists to abolish.
    """
    project = tmp_path / "proj"
    project.mkdir()
    base = tmp_path / "hosttmp"
    pad = _make_pad(base, project)
    for name in ("boot.txt", "git.txt", "census.txt"):
        (pad / name).write_text("x", encoding="utf-8")

    findings = check_host_scratch_storage(project, _hot(base))
    assert len(findings) == 1, "three files must collapse into ONE directory finding"
    f = findings[0]
    assert f.check == "host_scratch_storage"
    assert f.severity == WARNING, "the seal must not be hostage to a human-only fix"
    assert "3 leftover project file(s)" in f.detail
    assert str(pad) in f.detail


def test_error_branch_is_currently_unreachable_and_says_so(tmp_path):
    """HOST-SCRATCH-ERROR-BRANCH-IS-DEAD-S206, pinned so it cannot be forgotten.

    The ERROR half keys off meta.host_session_id. MEASURED S206: that key is
    absent from the RAG and NO code path writes it, so the branch never fires and
    every finding is a WARNING. This test fails the day someone starts setting
    the key — which is exactly when the branch stops being dead and the claim in
    the docstring must be re-read.
    """
    project = tmp_path / "proj"
    project.mkdir()
    base = tmp_path / "hosttmp"
    (_make_pad(base, project, "sess-1") / "boot.txt").write_text("x", encoding="utf-8")

    # no host_session_id declared -> everything is foreign -> WARNING only
    findings = check_host_scratch_storage(project, _hot(base))
    assert [f.severity for f in findings] == [WARNING]

    # declared and matching -> the ERROR branch is reachable in principle
    hot = _hot(base)
    hot["meta"]["host_session_id"] = "sess-1"
    findings = check_host_scratch_storage(project, hot)
    assert ERROR in [f.severity for f in findings]


def test_clause_survives_hot_none(tmp_path):
    """An auditor that raises is an auditor that gets switched off."""
    project = tmp_path / "proj"
    project.mkdir()
    assert check_host_scratch_storage(project, None) == []


def test_finding_carries_an_executable_operator_remediation(tmp_path):
    """Rule 43 retro_clarity: the AGENT cannot clear this — the files are outside
    root_project and filesystem_boundary/E-026 bars it from deleting there. A
    refusal the reader cannot act on is a wall, so the command is in the finding."""
    project = tmp_path / "proj"
    project.mkdir()
    base = tmp_path / "hosttmp"
    (_make_pad(base, project) / "gate_check.py").write_text("x", encoding="utf-8")

    detail = check_host_scratch_storage(project, _hot(base))[0].detail
    assert "RAG/.boot/" in detail                     # where scratch belongs
    assert "Remove-Item -Recurse -Force" in detail    # the exact command
    assert "success looks like" in detail             # how to check it worked
    assert "filesystem_boundary" in detail            # why a human is needed


def test_nested_and_multi_session_files_are_all_found(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    base = tmp_path / "hosttmp"
    pad1 = _make_pad(base, project, "sess-1")
    (pad1 / "a.txt").write_text("x", encoding="utf-8")
    (pad1 / "deep").mkdir()
    (pad1 / "deep" / "b.txt").write_text("x", encoding="utf-8")
    pad2 = _make_pad(base, project, "sess-2")
    (pad2 / "c.txt").write_text("x", encoding="utf-8")

    assert len(check_host_scratch_storage(project, _hot(base))) == 3


def test_scan_is_bounded(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    base = tmp_path / "hosttmp"
    pad = _make_pad(base, project)
    monkeypatch.setattr(drift_audit, "_HOST_SCRATCH_MAX_HITS", 5)
    for i in range(20):
        (pad / f"f{i}.txt").write_text("x", encoding="utf-8")
    assert len(find_host_scratch_files(project, _hot(base))) == 5


# ---------------------------------------------------------------------------
# roots resolution
# ---------------------------------------------------------------------------

def test_declared_roots_are_additive_not_replacing(tmp_path):
    """A deployment declaring its own temp dir must not silently switch off the
    universal defaults — the _secret_globs contract, for the same reason."""
    declared = host_scratch_roots({"meta": {"host_scratch_roots": [str(tmp_path)]}})
    default = host_scratch_roots({})
    assert tmp_path in declared
    assert len(declared) > len(default) or set(default).issubset(set(declared))


def test_wsl_root_also_yields_the_windows_slug_the_harness_actually_uses():
    """THE SECOND MISSING REGRESSION. Same root cause as the one below, other half.

    The harness slugs the WINDOWS path. Under WSL the kernel sees the project as
    /mnt/c/..., whose slug is '-mnt-c-Users-...' and matches no directory that
    exists. Measured S206: 22 real files, 0 findings, twice.
    """
    slugs = drift_audit.host_scratch_slugs(
        "/mnt/c/Users/pakhol/Desktop/GitHub Project (RAG Runtime Kernel)"
    )
    assert "C--Users-pakhol-Desktop-GitHub-Project--RAG-Runtime-Kernel-" in slugs, slugs


def test_windows_form_translation():
    assert drift_audit._windows_form("/mnt/c/Users/x/p") == r"C:\Users\x\p"
    assert drift_audit._windows_form("/mnt/d/a/b") == r"D:\a\b"
    assert drift_audit._windows_form("/home/x/p") is None
    assert drift_audit._windows_form(r"C:\already\windows") is None


def test_native_windows_root_yields_exactly_one_slug():
    slugs = drift_audit.host_scratch_slugs(r"C:\Users\pakhol\Desktop\proj")
    assert len(slugs) == 1


def test_windows_profile_temp_is_derived_from_the_root_under_wsl():
    """THE REGRESSION THAT WAS MISSING, and it cost a shipped-blind gate.

    MEASURED S206: the first cut of this clause reported 0 findings over 22 real
    files. The audit runs under WSL python, where gettempdir() is /tmp and
    LOCALAPPDATA is unset, so no env-derived default can ever name the Windows
    host scratchpad. Every test above passed because each one injected its root
    through meta.host_scratch_roots — so the DEFAULT resolution, the thing that
    actually runs in production, was never exercised once.

    The derivation must take its path space from the project root itself.
    """
    wsl_root = "/mnt/c/Users/pakhol/Desktop/GitHub Project (RAG Runtime Kernel)"
    roots = [str(p).replace("\\", "/") for p in host_scratch_roots({}, wsl_root)]
    assert any(
        r.endswith("/mnt/c/Users/pakhol/AppData/Local/Temp")
        or r == "/mnt/c/Users/pakhol/AppData/Local/Temp"
        for r in roots
    ), f"the Windows profile temp was not derived from the root: {roots}"


def test_windows_profile_temp_is_derived_from_a_native_windows_root():
    roots = [str(p).replace("\\", "/") for p in
             host_scratch_roots({}, r"C:\Users\pakhol\Desktop\proj")]
    assert any(r.lower().endswith("users/pakhol/appdata/local/temp") for r in roots)


def test_posix_home_root_yields_no_profile_temp_and_does_not_raise():
    """A /home/<user>/ layout has no Users component; env defaults are correct there."""
    assert drift_audit._profile_temp_from_root("/home/pakhol/proj") == []
    assert drift_audit._profile_temp_from_root(None) == []


def test_roots_are_deduplicated():
    roots = host_scratch_roots({})
    keys = [str(p).rstrip("\\/").lower() for p in roots]
    assert len(keys) == len(set(keys))


def test_malformed_declared_roots_do_not_raise():
    assert host_scratch_roots({"meta": {"host_scratch_roots": "not-a-list"}})
    assert host_scratch_roots({"meta": None})
    assert host_scratch_roots(None)


# ---------------------------------------------------------------------------
# wiring — a clause nothing calls is the disease this project is named after
# ---------------------------------------------------------------------------

def test_clause_runs_inside_the_full_audit(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    base = tmp_path / "hosttmp"
    (_make_pad(base, project) / "leak.txt").write_text("x", encoding="utf-8")

    called: list[Path] = []
    real = drift_audit.check_host_scratch_storage

    def spy(root, hot=None):
        called.append(Path(root))
        return real(root, hot)

    monkeypatch.setattr(drift_audit, "check_host_scratch_storage", spy)
    drift_audit.audit_hot(
        {"meta": {"host_scratch_roots": [str(base)]}}, root=project
    )
    assert called, "audit_hot never called the clause — it is a detector nothing runs"


def test_clause_is_exported():
    assert "check_host_scratch_storage" in drift_audit.__all__
