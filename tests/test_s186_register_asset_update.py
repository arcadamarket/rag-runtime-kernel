"""S186 -- register-asset --update, restored after the v0.4.50 adoption deleted it.

The verb was authored in a CHILD deployment (its S5) and removed when that
deployment adopted the parent v0.4.50 package byte-identically. Byte-identity IS
the deletion mechanism, so a local verb cannot survive an adoption unless it
lives upstream. It lives upstream now.

test_cli_still_exposes_update is the ANTI-REGRESSION guard: the affordance is the
contract, so its ABSENCE is what must fail a test. That is the same shape as the
anti-poll guard on run --detach --await.
"""
from pathlib import Path

import pytest

from rag_kernel.asset_registry import (
    AssetRebindError,
    AssetRegistryError,
    compute_sha256,
    list_assets,
    register_asset,
)


def _asset(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _reg(root, **kw):
    base = dict(asset_id="a", path="a.py", purpose="do a", session="S186",
                project_root=root)
    base.update(kw)
    return register_asset(root, **base)

def test_update_rehashes_in_place_and_keeps_lineage(tmp_path):
    _asset(tmp_path, "a.py", "v1\n")
    first, _ = _reg(tmp_path)
    _asset(tmp_path, "a.py", "v2\n")
    rec, action = _reg(tmp_path, session="S187", update=True)
    assert action == "updated"
    assert rec.sha256 == compute_sha256(tmp_path / "a.py") != first.sha256
    assert len(rec.supersedes) == 1
    assert rec.supersedes[-1]["sha256"] == first.sha256
    assert len(list_assets(tmp_path)) == 1


def test_content_change_without_update_stays_fail_loud(tmp_path):
    _asset(tmp_path, "a.py", "v1\n")
    _reg(tmp_path)
    _asset(tmp_path, "a.py", "v2\n")
    with pytest.raises(AssetRegistryError):
        _reg(tmp_path)


def test_update_refuses_to_reaim_id_at_a_different_path(tmp_path):
    _asset(tmp_path, "a.py", "v1\n")
    _reg(tmp_path)
    _asset(tmp_path, "b.py", "v1\n")
    with pytest.raises(AssetRebindError):
        _reg(tmp_path, path="b.py", update=True)


def test_update_on_unchanged_content_appends_no_revision(tmp_path):
    _asset(tmp_path, "a.py", "v1\n")
    _reg(tmp_path)
    rec, action = _reg(tmp_path, session="S187", update=True)
    assert action == "idempotent"
    assert not rec.supersedes


def test_repeated_updates_retain_every_prior_sha(tmp_path):
    _asset(tmp_path, "a.py", "v1\n")
    r1, _ = _reg(tmp_path)
    _asset(tmp_path, "a.py", "v2\n")
    r2, _ = _reg(tmp_path, update=True)
    _asset(tmp_path, "a.py", "v3\n")
    r3, _ = _reg(tmp_path, update=True)
    assert [s["sha256"] for s in r3.supersedes] == [r1.sha256, r2.sha256]


def test_cli_still_exposes_update(tmp_path):
    """ANTI-REGRESSION: an adoption must never silently drop this flag again."""
    from rag_kernel.__main__ import build_parser
    actions = build_parser()._subparsers._group_actions[0].choices["register-asset"]._actions
    assert any("--update" in (a.option_strings or []) for a in actions), (
        "register-asset --update vanished from the CLI; see ADOPT-DESTROYS-LOCAL-DIVERGENCE"
    )
