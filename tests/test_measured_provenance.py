"""RUNBOOK-TABLE-NO-INVARIANT (S187) — measured tables that go stale loudly.

``RUNBOOK_CLONE_INIT_S179.md`` carries a table of measured facts and a §0.4 titled
"RE-MEASURE BEFORE YOU TRUST THIS DOCUMENT". That instruction is prose, and prose
caught none of the four staleness events it was written for:

  * rev-2 shipped stale inside its own session (E-091);
  * rev-3 went stale when S181–S182 moved all four counts;
  * rev-4 declares runtime v0.4.49 while the live runtime is 0.4.53.

The fix does NOT re-derive the numbers — that would mean running ``init`` into a
temp dir on every audit. It checks PROVENANCE: a measured table is trustworthy only
while the runtime/spec it was measured against are still live. That is decidable in
constant time and catches all four historical failures.
"""

from __future__ import annotations

import pytest

from rag_kernel.drift_audit import WARNING, check_measured_doc_provenance
from rag_kernel.measured import (
    MEASURED_DOC_VERSION,
    Measurement,
    format_stamp,
    scan_measurements,
    stale_measurements,
)


STAMP = "<!-- MEASURED: session=S183 runtime=0.4.49 spec=3.2.8 -->"


def _doc(tmp_path, name="RUNBOOK.md", body=STAMP):
    p = tmp_path / name
    p.write_text(f"# Title\n{body}\n\n| a | b |\n|---|---|\n| 29 | 52 |\n",
                 encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# scanning
# --------------------------------------------------------------------------- #

def test_version_pin():
    assert MEASURED_DOC_VERSION == "1.0.0"


def test_scan_reads_every_field():
    m = scan_measurements(STAMP, path="d.md")[0]
    assert (m.session, m.runtime, m.spec) == ("S183", "0.4.49", "3.2.8")
    assert m.line == 1


def test_scan_is_order_free():
    m = scan_measurements("<!-- MEASURED: spec=3.2.8 runtime=0.4.49 session=S1 -->")[0]
    assert (m.session, m.runtime, m.spec) == ("S1", "0.4.49", "3.2.8")


def test_scan_ignores_ordinary_prose():
    assert scan_measurements("We measured 29 keys against spec 3.2.8.\n") == []
    assert scan_measurements("<!-- a normal comment -->\n") == []


def test_scan_finds_multiple_stamps_in_line_order():
    text = f"x\n{STAMP}\ny\n<!-- MEASURED: session=S2 runtime=0.5.0 -->\n"
    lines = [m.line for m in scan_measurements(text)]
    assert lines == [2, 4]


# --------------------------------------------------------------------------- #
# the staleness rule
# --------------------------------------------------------------------------- #

def test_live_runtime_ahead_is_stale():
    m = Measurement(path="d", line=1, session="S183", runtime="0.4.49", spec="3.2.8")
    reasons = m.staleness(live_runtime="0.4.53", live_spec="3.2.8")
    assert len(reasons) == 1
    assert "0.4.49" in reasons[0] and "0.4.53" in reasons[0]


def test_live_spec_ahead_is_stale():
    m = Measurement(path="d", line=1, runtime="0.4.53", spec="3.2.8")
    assert m.staleness(live_runtime="0.4.53", live_spec="3.3.0")


def test_equal_versions_are_fresh():
    m = Measurement(path="d", line=1, runtime="0.4.53", spec="3.2.8")
    assert m.staleness(live_runtime="0.4.53", live_spec="3.2.8") == []


def test_live_runtime_behind_is_not_a_document_defect():
    """A clone running an older kernel is a deployment question, not staleness —
    flagging it would turn every downstream deployment's audit red."""
    m = Measurement(path="d", line=1, runtime="0.4.53")
    assert m.staleness(live_runtime="0.4.49") == []


def test_semver_compares_numerically_not_lexically():
    """'0.4.9' vs '0.4.10' is the classic string-compare trap."""
    m = Measurement(path="d", line=1, runtime="0.4.9")
    assert m.staleness(live_runtime="0.4.10")


def test_unknown_live_version_cannot_assert_staleness():
    m = Measurement(path="d", line=1, runtime="0.4.49")
    assert m.staleness(live_runtime="") == []


# --------------------------------------------------------------------------- #
# file-level scan
# --------------------------------------------------------------------------- #

def test_stale_measurements_reports_the_stale_doc(tmp_path):
    d = _doc(tmp_path)
    out = stale_measurements([d], live_runtime="0.4.53", live_spec="3.2.8")
    assert len(out) == 1 and out[0][0].path == str(d)


def test_fresh_doc_reports_nothing(tmp_path):
    d = _doc(tmp_path, body="<!-- MEASURED: session=S187 runtime=0.4.53 spec=3.2.8 -->")
    assert stale_measurements([d], live_runtime="0.4.53", live_spec="3.2.8") == []


def test_unstamped_doc_is_not_scanned_into_a_finding(tmp_path):
    d = tmp_path / "plain.md"
    d.write_text("# no stamp here\n", encoding="utf-8")
    assert stale_measurements([d], live_runtime="9.9.9") == []


def test_an_unanchored_stamp_is_reported_as_undetectable(tmp_path):
    """A stamp with no version anchor can never go stale — which is the defect."""
    d = _doc(tmp_path, body="<!-- MEASURED: session=S183 -->")
    out = stale_measurements([d], live_runtime="0.4.53")
    assert len(out) == 1
    assert "can never be detected as stale" in out[0][1][0]


def test_unreadable_path_is_skipped_not_raised(tmp_path):
    assert stale_measurements([tmp_path / "nope.md"], live_runtime="1.0.0") == []


# --------------------------------------------------------------------------- #
# auditor wiring
# --------------------------------------------------------------------------- #

def test_auditor_warns_and_names_the_document(tmp_path):
    d = _doc(tmp_path)
    findings = check_measured_doc_provenance(
        [d], live_runtime="0.4.53", live_spec="3.2.8")
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "measured_doc_provenance"
    assert f.severity == WARNING          # unverified, not proven wrong
    assert "RUNBOOK.md" in f.detail
    assert "S183" in f.detail             # who stamped it
    assert "UNVERIFIED" in f.detail


def test_auditor_self_skips_without_docs():
    """The default audit path must be unchanged for a tree that opted out."""
    assert check_measured_doc_provenance(None, live_runtime="9.9.9") == []
    assert check_measured_doc_provenance([], live_runtime="9.9.9") == []


# --------------------------------------------------------------------------- #
# the stamp emitter + CLI
# --------------------------------------------------------------------------- #

def test_format_stamp_round_trips_through_the_scanner():
    stamp = format_stamp(session="S187", runtime="0.4.53", spec="3.2.8")
    m = scan_measurements(stamp)[0]
    assert (m.session, m.runtime, m.spec) == ("S187", "0.4.53", "3.2.8")


def test_format_stamp_omits_an_absent_spec():
    assert "spec=" not in format_stamp(session="S1", runtime="0.4.53")


class TestMeasuredCLI:
    def test_stamp_mode_prints_a_scannable_stamp(self, capsys):
        from rag_kernel.__main__ import main
        assert main(["measured", "--stamp", "--session", "S187"]) == 0
        out = capsys.readouterr().out
        assert scan_measurements(out)[0].session == "S187"

    def test_exit_1_when_a_scanned_doc_is_stale(self, tmp_path, capsys):
        from rag_kernel.__main__ import main
        _doc(tmp_path)
        rc = main(["measured", "--roots", str(tmp_path)])
        assert rc == 1
        assert "STALE" in capsys.readouterr().out

    def test_exit_0_when_nothing_is_stamped(self, tmp_path, capsys):
        from rag_kernel.__main__ import main
        (tmp_path / "plain.md").write_text("# nothing\n", encoding="utf-8")
        assert main(["measured", "--roots", str(tmp_path)]) == 0
        assert "no document" in capsys.readouterr().out

    def test_json_output_is_machine_readable(self, tmp_path, capsys):
        import json as _json
        from rag_kernel.__main__ import main
        _doc(tmp_path)
        main(["measured", "--roots", str(tmp_path), "--json"])
        rows = _json.loads(capsys.readouterr().out)
        assert rows[0]["session"] == "S183" and rows[0]["stale"] is True
