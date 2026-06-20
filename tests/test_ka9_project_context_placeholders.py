"""KA-9: fail-loud on residual human-fill ``<...>`` placeholders in project_context.

The universal spec ships ``project_context`` (brief / domain / end_goal /
principals) with session-zero ``<from user>`` / ``<absolute path>`` tokens the
LLM must substitute at deploy. They are lowercase/spaced, so the UPPER_SNAKE
``check_placeholder_tokens`` scan (FIX-1, K3) never caught them — the eBay
Session-Zero deploy shipped ``brief``/``domain``/``end_goal`` as a verbatim
``"<from user>"`` and audited clean. ``check_project_context_placeholders``
(KA-9, part of the KA-10 GOVERNANCE-DETERMINISM arc) closes that gap. These
tests pin the new fail-loud invariant, prove it self-skips a healthy / absent
``project_context`` (no false positives), and prove it composes into the
``audit_hot`` boundary gate without double-reporting UPPER_SNAKE tokens.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from rag_kernel import drift_audit
from rag_kernel.__main__ import cmd_init
from rag_kernel.drift_audit import (
    check_placeholder_tokens,
    check_project_context_placeholders,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _checks(findings) -> set[str]:
    return {f.check for f in findings}


def _latest_spec() -> Path:
    specs = sorted(REPO_ROOT.glob("INIT_UNIVERSAL_RUNTIME_KERNEL_v*.md"))
    if not specs:
        pytest.skip("no INIT_UNIVERSAL_RUNTIME_KERNEL spec found in repo root")
    return specs[-1]


def _init_args(output: Path, **kw) -> argparse.Namespace:
    ns = argparse.Namespace(
        spec=_latest_spec(), output=output, root_project="", root_deliverables="",
        root_rag="", project_name="", dry_run=False, auto_ready=False,
        session="S0", path_style="auto", requirements=None, allow_void=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --------------------------------------------------------------------------
# the KA-9 gap: a lowercase/spaced human-fill placeholder
# --------------------------------------------------------------------------

def test_from_user_whole_value_is_error():
    hot = {"project_context": {"brief": "<from user>"}}
    f = check_project_context_placeholders(hot)
    assert len(f) == 1
    assert _checks(f) == {"project_context_placeholders"}
    assert "<from user>" in f[0].detail
    assert "project_context/brief" in f[0].detail


def test_every_unfilled_field_reported():
    # the exact eBay defect: brief + domain + end_goal all verbatim "<from user>"
    hot = {"project_context": {
        "brief": "<from user>",
        "domain": "<from user>",
        "end_goal": "<from user>",
    }}
    f = check_project_context_placeholders(hot)
    assert len(f) == 3


def test_absolute_path_placeholder_caught():
    hot = {"project_context": {"root": "<absolute path>"}}
    f = check_project_context_placeholders(hot)
    assert len(f) == 1
    assert "<absolute path>" in f[0].detail


def test_partial_fill_substring_caught():
    # a half-substituted value must still fail loud
    hot = {"project_context": {"brief": "Build <from user>'s storefront tooling"}}
    f = check_project_context_placeholders(hot)
    assert len(f) == 1
    assert "<from user>" in f[0].detail


def test_placeholder_nested_in_principals_caught():
    hot = {"project_context": {"principals": {"developer": "<your name>"}}}
    f = check_project_context_placeholders(hot)
    assert len(f) == 1
    assert "project_context/principals/developer" in f[0].detail


# --------------------------------------------------------------------------
# no false positives
# --------------------------------------------------------------------------

def test_clean_project_context_passes():
    hot = {"project_context": {
        "brief": "Filesystem-backed, event-sourced project memory system.",
        "domain": "AI systems engineering - LLM memory persistence",
        "end_goal": "Publish RAG Runtime Kernel as open-source GitHub project",
        "principals": {"github_account": "arcadamarket", "developer": "Artem Pakhol"},
    }}
    assert check_project_context_placeholders(hot) == []


def test_absent_project_context_self_skips():
    assert check_project_context_placeholders({"meta": {}}) == []
    assert check_project_context_placeholders({}) == []


def test_math_comparison_prose_not_flagged():
    # "a < b and c > d": the first '<' is followed by a space, not a letter, so the
    # placeholder anchor never matches — no false positive on technical prose.
    hot = {"project_context": {"brief": "guard fires when a < b and c > d holds"}}
    assert check_project_context_placeholders(hot) == []


def test_upper_snake_token_left_to_global_check_no_double_report():
    # a pure UPPER_SNAKE token in project_context is owned by check_placeholder_tokens;
    # the KA-9 scan must NOT also report it (no duplicate finding for one defect).
    hot = {"project_context": {"brief": "<SPEC_VERSION>"}}
    assert check_project_context_placeholders(hot) == []
    # ...but it is NOT lost: the global UPPER_SNAKE scan still fires.
    assert _checks(check_placeholder_tokens(hot)) == {"placeholder_tokens"}


# --------------------------------------------------------------------------
# composition into the boundary gate
# --------------------------------------------------------------------------

def test_fires_in_audit_hot():
    hot = {
        "meta": {"policy_version": "3.2.5", "written_by_session": "S94",
                 "last_checkpoint_seq": 1},
        "state_machine_status": "READY",
        "operating_protocol": {"tool_hierarchy": "..."},
        "sessions_recent": [{"id": "S94"}],
        "tracked_items": [],
        "project_context": {"brief": "<from user>"},
    }
    report = drift_audit.audit_hot(hot)
    assert not report.ok
    assert "project_context_placeholders" in _checks(report.errors)


# --------------------------------------------------------------------------
# born-clean: init resolves unfilled placeholders to null (spec §1182)
# --------------------------------------------------------------------------

def test_init_nulls_unfilled_project_context_placeholders(tmp_path):
    """A fresh init with no operator-supplied values must NULL the project_context
    "<from user>" tokens (spec §1182), not leave the literal placeholder."""
    out = tmp_path / "RAG"
    cmd_init(_init_args(out))
    hot = json.loads((out / "RAG_MASTER.json").read_text(encoding="utf-8"))
    pc = hot["project_context"]
    assert pc["brief"] is None
    assert pc["domain"] is None
    assert pc["end_goal"] is None
    # the UPPER_SNAKE <SPEC_VERSION> token is NOT a project_context human-fill token
    assert "<from user>" not in json.dumps(pc)


def test_fresh_auto_ready_is_born_clean_under_ka9(tmp_path):
    """The headline: a fresh `init --auto-ready` (the prescribed deploy path)
    passes the KA-9 gate by construction — the eBay defect cannot ship."""
    out = tmp_path / "RAG"
    cmd_init(_init_args(out, auto_ready=True))
    hot = json.loads((out / "RAG_MASTER.json").read_text(encoding="utf-8"))
    assert check_project_context_placeholders(hot) == []
