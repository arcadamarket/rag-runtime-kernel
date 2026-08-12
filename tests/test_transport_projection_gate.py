"""PROJECTION-DRIFT-UNGATED (S198): the transport allowlist projection is GATED.

S197 shipped the projection and a ``--check`` that could prove it agreed with
the rule, then called that check from nowhere. These tests pin the wiring, not
the comparison: the comparison already worked, which is exactly why nobody
noticed it was never run. Each test therefore asserts through
``drift_audit.audit_hot`` — the surface the session boundary actually calls —
rather than through the checker in isolation.
"""

from __future__ import annotations

import json

import pytest

from rag_kernel import drift_audit, transport_projection as tp


RULE_TEXT = (
    "TRANSPORT ALLOWLIST. Only declared agent-facing transports may run.\n"
    "DECLARED PATTERNS (3 PATTERNS):\n"
    "  ^mcp__tmux-mcp__\n"
    "  ^(Read|Edit|Write)$\n"
    "  ^Bash$\n"
    "ADDING A TRANSPORT is a governed write to this rule, never to the projection.\n"
)


def _hot(rule: "str | None" = RULE_TEXT) -> dict:
    hot: dict = {"operating_protocol": {}}
    if rule is not None:
        hot["operating_protocol"][tp.RULE_KEY] = rule
    return hot


def _write_projection(root, payload: dict) -> None:
    path = tp.projection_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _drift_findings(hot, root):
    report = drift_audit.audit_hot(hot, root=root)
    return [f for f in report.findings if f.check == "transport_projection_drift"]


# --------------------------------------------------------------------------
# the parser, kept honest (regression cover carried over from the renderer)
# --------------------------------------------------------------------------

def test_extract_patterns_keeps_alternation_groups():
    """The original parser dropped every ``^(A|B)$`` form and narrowed the policy."""
    assert tp.extract_patterns(RULE_TEXT) == [
        "^mcp__tmux-mcp__",
        "^(Read|Edit|Write)$",
        "^Bash$",
    ]


def test_declared_count_mismatch_refuses():
    bad = RULE_TEXT.replace("3 PATTERNS", "9 PATTERNS")
    with pytest.raises(tp.ProjectionError, match="declares 9 patterns"):
        tp.extract_patterns(bad)


def test_zero_patterns_refuses_rather_than_denying_everything():
    empty = "DECLARED PATTERNS:\nnone yet\nADDING A TRANSPORT is governed.\n"
    with pytest.raises(tp.ProjectionError, match="zero patterns"):
        tp.extract_patterns(empty)


def test_projection_error_is_not_systemexit():
    """A library that calls sys.exit cannot be called by an auditor."""
    assert issubclass(tp.ProjectionError, ValueError)
    assert not issubclass(tp.ProjectionError, SystemExit)


# --------------------------------------------------------------------------
# the gate itself — this is the part S197 did not have
# --------------------------------------------------------------------------

def test_audit_is_clean_when_projection_matches(tmp_path):
    tp.render(RULE_TEXT, tmp_path)
    assert _drift_findings(_hot(), tmp_path) == []


def test_audit_self_skips_when_no_rule_is_declared(tmp_path):
    """Most clones declare no transport allowlist; that is not a defect."""
    assert _drift_findings(_hot(rule=None), tmp_path) == []


def test_audit_errors_when_rule_declared_but_no_projection_rendered(tmp_path):
    """Declared-but-not-running is the ACTIVATION-GAP shape, so it is drift."""
    findings = _drift_findings(_hot(), tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == drift_audit.ERROR
    assert "no projection exists" in findings[0].detail


def test_audit_errors_when_rule_text_changed_under_the_projection(tmp_path):
    tp.render(RULE_TEXT, tmp_path)
    edited = RULE_TEXT.replace("^Bash$", "^Bash$  # widened by hand")
    findings = _drift_findings(_hot(edited), tmp_path)
    assert findings and all(f.severity == drift_audit.ERROR for f in findings)
    assert any("different rule text" in f.detail for f in findings)


def test_audit_errors_when_the_projection_was_hand_edited(tmp_path):
    """Editing the cache instead of the authority is the DEC-0009 failure."""
    tp.render(RULE_TEXT, tmp_path)
    payload = json.loads(tp.projection_path(tmp_path).read_text(encoding="utf-8"))
    payload["allowlist"].append("^mcp__anything__")
    _write_projection(tmp_path, payload)

    findings = _drift_findings(_hot(), tmp_path)
    assert findings
    assert any("allowlist differs" in f.detail for f in findings)


def test_audit_errors_when_the_projection_is_unparseable(tmp_path):
    tp.render(RULE_TEXT, tmp_path)
    tp.projection_path(tmp_path).write_text("{not json", encoding="utf-8")
    findings = _drift_findings(_hot(), tmp_path)
    assert findings
    assert any("unparseable" in f.detail for f in findings)


def test_drift_is_an_error_not_a_warning(tmp_path):
    """Warnings do not fail the seal; this class must."""
    report = drift_audit.audit_hot(_hot(), root=tmp_path)
    with pytest.raises(Exception):
        drift_audit.assert_clean(report)


# --------------------------------------------------------------------------
# the checker travels — the reason the logic moved into the package
# --------------------------------------------------------------------------

def test_checker_lives_in_the_installed_package_not_in_tools():
    """The S197 renderer existed only in one deployment's untracked tools/ dir.

    Importing it from ``rag_kernel`` is the assertion that it now ships with the
    kernel, is covered by this gate, and reaches every clone.
    """
    import importlib

    module = importlib.import_module("rag_kernel.transport_projection")
    assert module.__name__ == "rag_kernel.transport_projection"
    for attr in ("rule_text_from_hot", "drift_reasons", "render", "extract_patterns"):
        assert callable(getattr(module, attr))


def test_render_is_atomic_and_leaves_no_tmp(tmp_path):
    out = tp.render(RULE_TEXT, tmp_path)
    assert out.exists()
    assert not out.with_suffix(".json.tmp").exists()
    assert json.loads(out.read_text(encoding="utf-8"))["rule_key"] == tp.RULE_KEY
