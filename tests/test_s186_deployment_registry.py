"""S186 -- authorized push destinations as FIELDS, and the gate that reads them.

Regression origin: on 2026-08-04 a child deployment pushed its governance state,
brand assets and full site source into the kernel own publishing account. Nothing
detected it because meta.deployments never said where that deployment MAY push,
so an inherited git origin was indistinguishable from an authorization.
"""
import json

import pytest

from rag_kernel.deployment_registry import (
    DestinationMismatchError,
    EmbeddedCredentialError,
    UndeclaredDestinationError,
    UnknownDeploymentError,
    UnsettableFieldError,
    check_push_destination,
    get_deployment,
    load_deployments,
    normalize_remote,
    owner_repo,
    set_deployment_field_in_file,
)


def _rag(tmp_path, deployments):
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps({
        "meta": {"deployments": deployments, "last_updated_utc": "2026-01-01T00:00:00Z"},
        "tracked_items": [],
    }), encoding="utf-8")
    return p


def test_username_in_url_is_fine_but_a_token_is_a_leak():
    clean, secret, user = normalize_remote("https://alexxxschultz-ux@github.com/o/r.git")
    assert (clean, secret, user) == ("https://github.com/o/r.git", False, True)
    clean, secret, user = normalize_remote("https://user:ghp_deadbeef@github.com/o/r.git")
    assert secret is True and "ghp_deadbeef" not in clean


def test_owner_repo_parses_the_forms_we_actually_see():
    assert owner_repo("https://github.com/arcadamarket/PROJECTS.git") == ("arcadamarket", "PROJECTS")
    assert owner_repo("https://github.com/alexxxschultz-ux/PROJECTS") == ("alexxxschultz-ux", "PROJECTS")
    assert owner_repo("git@github.com:o/r.git") == ("o", "r")


def test_setter_refuses_an_unrecorded_deployment(tmp_path):
    p = _rag(tmp_path, {"KNOWN": {"root": "/x"}})
    with pytest.raises(UnknownDeploymentError):
        set_deployment_field_in_file(p, "GHOST", "authorized_remote", "u", session="S1")


def test_setter_refuses_a_field_outside_the_allowlist(tmp_path):
    p = _rag(tmp_path, {"K": {"root": "/x"}})
    with pytest.raises(UnsettableFieldError):
        set_deployment_field_in_file(p, "K", "root", "/somewhere-else", session="S1")


def test_setter_writes_the_field_and_stamps_the_session(tmp_path):
    p = _rag(tmp_path, {"K": {"root": "/x"}})
    set_deployment_field_in_file(
        p, "K", "authorized_remote", "https://github.com/o/r", session="S186")
    rec = get_deployment(p, "K")
    assert rec["authorized_remote"] == "https://github.com/o/r"
    assert rec["last_touched_by"] == "S186"


def test_prose_annotations_are_excluded_but_underscore_keys_are_not(tmp_path):
    """A record is a dict; _purpose is prose. The real deployment keys
    (_ONLINE_BIZ_PROJECT, _CONTENT_FACTORY_PROJECT) start with an underscore,
    so filtering on the underscore would hide two of the three deployments.
    """
    p = _rag(tmp_path, {
        "_purpose": "prose that is not a deployment",
        "_ONLINE_BIZ_PROJECT": {"root": "/x"},
        "RAG_RUNTIME_KERNEL": {"root": "/y"},
    })
    found = set(load_deployments(json.loads(p.read_text(encoding="utf-8"))))
    assert found == {"_ONLINE_BIZ_PROJECT", "RAG_RUNTIME_KERNEL"}
    assert get_deployment(p, "_ONLINE_BIZ_PROJECT")["root"] == "/x"


def test_undeclared_destination_is_refused_not_allowed(tmp_path):
    """REFUSE-BY-DEFAULT: absence of a declaration is not permission."""
    p = _rag(tmp_path, {"K": {"root": "/x"}})
    with pytest.raises(UndeclaredDestinationError):
        check_push_destination(p, "K", tmp_path, actual_url="https://github.com/o/r")


def test_the_2026_08_04_push_is_refused(tmp_path):
    """The exact incident: origin pointed at the KERNEL account, not the child."""
    p = _rag(tmp_path, {"_ONLINE_BIZ_PROJECT": {
        "authorized_remote": "https://github.com/alexxxschultz-ux/PROJECTS",
        "authorized_identity": "alexxxschultz-ux",
    }})
    with pytest.raises(DestinationMismatchError) as ex:
        check_push_destination(
            p, "_ONLINE_BIZ_PROJECT", tmp_path,
            actual_url="https://github.com/arcadamarket/PROJECTS.git")
    assert "INHERITED ACCIDENT" in str(ex.value)


def test_matching_destination_passes(tmp_path):
    p = _rag(tmp_path, {"K": {
        "authorized_remote": "https://github.com/alexxxschultz-ux/PROJECTS",
        "authorized_identity": "alexxxschultz-ux",
    }})
    res = check_push_destination(
        p, "K", tmp_path,
        actual_url="https://alexxxschultz-ux@github.com/alexxxschultz-ux/PROJECTS.git")
    assert res.ok and res.code == "AUTHORIZED"


def test_embedded_credential_in_remote_url_is_refused(tmp_path):
    """A token in a remote URL is echoed by anything that prints the URL."""
    p = _rag(tmp_path, {"K": {"authorized_remote": "https://github.com/o/r"}})
    with pytest.raises(EmbeddedCredentialError):
        check_push_destination(
            p, "K", tmp_path, actual_url="https://u:ghp_secret@github.com/o/r.git")


def test_identity_mismatch_is_refused_even_when_repo_path_matches(tmp_path):
    p = _rag(tmp_path, {"K": {
        "authorized_remote": "https://github.com/o/r",
        "authorized_identity": "someone-else",
    }})
    with pytest.raises(DestinationMismatchError):
        check_push_destination(p, "K", tmp_path, actual_url="https://github.com/o/r.git")


def test_unknown_deployment_is_refused(tmp_path):
    p = _rag(tmp_path, {"K": {"authorized_remote": "https://github.com/o/r"}})
    with pytest.raises(UnknownDeploymentError):
        check_push_destination(p, "NOPE", tmp_path, actual_url="https://github.com/o/r")
