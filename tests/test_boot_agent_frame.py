"""BOOT-RENDER-POV-ROLES (S176) — the boot path must render the agent's identity.

Root cause this locks down: session-start rendered the 40-rule digest and the
state briefing but never rendered ``pov_roles`` / ``pov_mandate``. The dual-POV
mandate therefore lived in the RAG unread, and reached the agent only via
operator-owned Project Instructions prose. These tests fail loud if the operating
frame ever stops being rendered on the governed boot path.
"""

import json

from rag_kernel.__main__ import _render_agent_frame


ROLES = [
    "AI/ML Engineer - LLM pipelines, RAG architectures, context optimization",
    "Senior CS Specialist - Deterministic state machines, DAG execution, WAL",
]


def _rag(roles=ROLES, mandate=None):
    return {
        "pov_roles": list(roles),
        "pov_mandate": mandate if mandate is not None else {"count": 2, "mode": "strict"},
    }


def test_frame_renders_every_role():
    out = _render_agent_frame(_rag())
    for r in ROLES:
        assert r in out, f"role missing from boot frame: {r}"
    assert "ROLE 1:" in out and "ROLE 2:" in out


def test_frame_announces_strict_mandate():
    out = _render_agent_frame(_rag())
    assert "mode STRICT" in out
    assert "reason EVERY deliverable from ALL the roles" in out


def test_frame_fails_loud_when_identity_absent():
    out = _render_agent_frame({})
    assert "identity is UNDEFINED" in out
    assert "ROLE 1:" not in out


def test_frame_carries_process_discipline():
    """E-081 + Rule 17: the disciplines most often loaded then ignored."""
    out = _render_agent_frame(_rag())
    assert "DETACHED" in out
    assert "NEVER poll" in out
    assert "E-081" in out
    assert "Bounded emissions" in out


def test_frame_reports_empty_asset_registry(tmp_path):
    (tmp_path / "RAG_CONTEXT.json").write_text(
        json.dumps({"baked_assets": {"assets": []}}), encoding="utf-8"
    )
    out = _render_agent_frame(_rag(), rag_dir=str(tmp_path))
    assert "registry EMPTY" in out


def test_frame_reports_registered_assets(tmp_path):
    (tmp_path / "RAG_CONTEXT.json").write_text(
        json.dumps({"baked_assets": {"assets": [{"id": "a"}, {"id": "b"}]}}),
        encoding="utf-8",
    )
    out = _render_agent_frame(_rag(), rag_dir=str(tmp_path))
    assert "2 registered" in out
    assert "reuse-check" in out


def test_frame_survives_unreadable_registry(tmp_path):
    (tmp_path / "RAG_CONTEXT.json").write_text("{not json", encoding="utf-8")
    out = _render_agent_frame(_rag(), rag_dir=str(tmp_path))
    assert "registry unreadable" in out


def test_frame_is_wired_into_the_boot_path():
    """Guard the CALL SITE, not just the renderer — an unrendered frame is the bug."""
    from pathlib import Path

    import rag_kernel.__main__ as m

    src = Path(m.__file__).read_text(encoding="utf-8")
    assert "_render_agent_frame(rag" in src, "boot path no longer calls _render_agent_frame"
