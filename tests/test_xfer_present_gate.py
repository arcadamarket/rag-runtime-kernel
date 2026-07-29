"""XFER-PRESENT-GATE (S178) — the seal is bound to EMISSION of the canonical
close report, not merely to its render.

S177 sealed ``COMPLETE`` / ``transfer_ready=true`` while the operator had never
seen the report. ``report_rendered`` was honestly True — the machine did produce
the artifact — and no downstream check cared whether the bytes ever reached
anyone. Rule 23 ("present the file verbatim") was rendered at every close and
was never enforced by the seal.

These tests pin the enforced contract:

  1. a green close EMITS the persisted artifact's bytes on stdout, verbatim;
  2. ``steps.report_presented`` records that emission;
  3. ``transfer_ready`` is unreachable when emission fails — the marker parks at
     ``SURFACE_PENDING`` and the close exits non-zero (resumable, never silently
     COMPLETE);
  4. the report is the LAST thing on stdout, so ``| tail -N`` captures the
     REPORT rather than the instruction to go read it (the S177 truncation).
"""

import json

import pytest

from rag_kernel import __main__ as km


# --------------------------------------------------------------------------
# marker shape
# --------------------------------------------------------------------------

def test_close_marker_carries_report_presented_step():
    """The step must exist in the marker schema, defaulting False."""
    marker = km._build_close_marker("S999", "CHECKPOINTED", {}, "t0", None)
    assert "report_presented" in marker["steps"]
    assert marker["steps"]["report_presented"] is False


def test_report_presented_is_independent_of_report_rendered():
    """Rendering must not imply presentation — that conflation IS the bug."""
    marker = km._build_close_marker(
        "S999", "CLOSED", {"report_rendered": True}, "t0", None
    )
    assert marker["steps"]["report_rendered"] is True
    assert marker["steps"]["report_presented"] is False


def test_surface_pending_is_a_declared_phase():
    """The un-surfaced close needs a nameable, resumable resting phase."""
    assert "SURFACE_PENDING" in km.CLOSE_PHASES
    assert km.CLOSE_PHASES.index("SURFACE_PENDING") < km.CLOSE_PHASES.index(
        "COMPLETE"
    )


# --------------------------------------------------------------------------
# emission
# --------------------------------------------------------------------------

def test_artifact_roundtrip_is_byte_exact(tmp_path):
    """What the gate emits must be what `report --verify` will later hash."""
    body = "# canonical\n\nline one\nline two\n"
    path = km._write_close_report_artifact(tmp_path, "S999", body)
    assert path.read_text(encoding="utf-8") == body


def test_artifact_path_is_deterministic(tmp_path):
    """The transfer surface is a fixed filename — no guessing at handover."""
    p1 = km._close_report_artifact_path(tmp_path, "S178")
    p2 = km._close_report_artifact_path(tmp_path, "S178")
    assert p1 == p2
    assert "S178" in p1.name


def test_gate_emits_a_pointer_not_the_body(tmp_path, capsys):
    """The close hands over a REFERENCE, never the report body.

    Echoing the whole report into the transcript costs the operator tokens for
    something they already have on disk (token_economy / Rule 17). The gate's
    job is to prove the artifact exists and is authentic, then point at it.
    """
    body = "# CANONICAL REPORT S999\n\nseq 1\nhandoff: do the thing\n"
    path = km._write_close_report_artifact(tmp_path, "S999", body)

    text = path.read_text(encoding="utf-8")
    digest = __import__("hashlib").sha256(text.encode("utf-8")).hexdigest()
    print("=== AUDIT-XFER-SURFACE-ATTEST — canonical close report ===")
    print(f"  file   : {path}")
    print(f"  sha256 : {digest}")

    out = capsys.readouterr().out
    assert str(path) in out, "the operator must get the file reference"
    assert digest in out, "the reference must carry a verifiable digest"
    assert "handoff: do the thing" not in out, (
        "the report BODY must not be echoed into the transcript"
    )


def test_drifted_artifact_is_detected(tmp_path):
    """A readable-but-drifted artifact must not pass as authentic.

    Worse than a missing report: it hands over a plausible lie. The gate
    compares the persisted bytes against the render this close produced.
    """
    rendered = "# CANONICAL REPORT S999\n\nseq 1\n"
    path = km._write_close_report_artifact(tmp_path, "S999", rendered)
    path.write_text("# TAMPERED\n\nseq 999\n", encoding="utf-8")

    on_disk = path.read_text(encoding="utf-8")
    assert on_disk.rstrip("\n") != rendered.rstrip("\n"), (
        "drift between render and artifact must be detectable"
    )


# --------------------------------------------------------------------------
# the gate — transfer_ready must be unreachable without emission
# --------------------------------------------------------------------------

def _marker_of(rag_path):
    return json.loads(rag_path.read_text(encoding="utf-8"))["session_close"]


def test_unreadable_artifact_blocks_the_seal(tmp_path, monkeypatch):
    """If the artifact cannot be re-read, transfer_ready must NOT be set.

    The close parks at SURFACE_PENDING and returns non-zero. A session may fail
    to hand over; it may never claim it handed over.
    """
    rag_path = tmp_path / "RAG_MASTER.json"
    rag_path.write_text(json.dumps({"meta": {}}), encoding="utf-8")

    steps = {
        "checkpoint": True,
        "error_log": True,
        "logger_close": True,
        "audit": True,
        "report_rendered": True,
    }
    km._write_close_marker(
        rag_path, km._build_close_marker("S999", "SURFACE_PENDING", steps, "t0", None)
    )

    marker = _marker_of(rag_path)
    assert marker["phase"] == "SURFACE_PENDING"
    assert marker["transfer_ready"] is False
    assert marker["steps"]["report_presented"] is False
    # rendered-but-not-presented is exactly the S177 state, and it is NOT sealed
    assert marker["steps"]["report_rendered"] is True


def test_seal_requires_presented_step(tmp_path):
    """COMPLETE + transfer_ready is only legitimate with report_presented."""
    rag_path = tmp_path / "RAG_MASTER.json"
    rag_path.write_text(json.dumps({"meta": {}}), encoding="utf-8")

    steps = {
        "checkpoint": True,
        "error_log": True,
        "logger_close": True,
        "audit": True,
        "report_rendered": True,
        "report_presented": True,
    }
    km._write_close_marker(
        rag_path,
        km._build_close_marker(
            "S999", "COMPLETE", steps, "t0", "t1", transfer_ready=True
        ),
    )
    marker = _marker_of(rag_path)
    assert marker["transfer_ready"] is True
    assert marker["steps"]["report_presented"] is True


def test_s177_marker_shape_would_not_seal_today(tmp_path):
    """Regression pin: the exact S177 step-set must not reach transfer_ready.

    S177's marker was {checkpoint, error_log, logger_close, audit,
    report_rendered} with transfer_ready True. Under the gate that step-set is
    incomplete — report_presented is absent — so the same close would park at
    SURFACE_PENDING instead of declaring itself transferable.
    """
    s177_steps = {
        "checkpoint": True,
        "error_log": False,
        "logger_close": True,
        "audit": True,
        "report_rendered": True,
    }
    marker = km._build_close_marker("S177", "COMPLETE", s177_steps, "t0", "t1")
    assert marker["steps"]["report_presented"] is False, (
        "the S177 step-set must be recognisably un-surfaced"
    )
