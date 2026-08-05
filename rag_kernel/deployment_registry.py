"""DEPLOYMENT REGISTRY -- authorized push destinations as FIELDS (S186).

WHY THIS MODULE EXISTS
----------------------
meta.deployments recorded root, rag, blueprint, runbook, born_runtime and the
verb that serves a deployment -- and nothing about WHERE that deployment may
push. An agent inheriting a pre-existing git ``origin`` therefore had no
governed fact to check it against. On 2026-08-04 a child deployment pushed its
governance state, brand assets and full site source into the kernel own
publishing account. Neither the child nor two sessions of parent audit could
detect it, because nothing in the RAG stated what the right destination was.

A pre-existing origin is an INHERITED ACCIDENT, not an authorization.

REFUSE-BY-DEFAULT
-----------------
A deployment with no declared ``authorized_remote`` is REFUSED, never allowed.
Absence of a declared destination is not permission. This is the GATE-OR-HOPE
distinction: the check lives in the verb, not in a rule that asks nicely.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from rag_kernel.drift_store import _touch_meta, load_hot
from rag_kernel.persistence import atomic_write_json

PARTITION = "deployments"

#: Fields a governed setter may write. Anything else is refused: the registry
#: is authoritative, so its shape is not open season.
SETTABLE_FIELDS = frozenset({
    "authorized_remote",
    "authorized_identity",
    "remote_url_pinned",
    "current_runtime",
    "last_verified_utc",
    "confirmed_by",
    "status",
    "note",
})


class DeploymentRegistryError(Exception):
    """Base for every refusal this module raises."""


class UnknownDeploymentError(DeploymentRegistryError):
    """The deployment key is not recorded in meta.deployments."""


class UnsettableFieldError(DeploymentRegistryError):
    """The field is outside SETTABLE_FIELDS."""


class UndeclaredDestinationError(DeploymentRegistryError):
    """No authorized_remote is declared. Refuse-by-default applies."""


class DestinationMismatchError(DeploymentRegistryError):
    """The actual remote is not the declared authorized_remote."""


class EmbeddedCredentialError(DeploymentRegistryError):
    """The remote URL carries a secret, so any command printing it leaks."""


@dataclass(frozen=True)
class PushCheck:
    """Outcome of a push-destination check. ``ok`` is the only pass."""

    ok: bool
    code: str
    message: str
    declared: Optional[str] = None
    actual: Optional[str] = None


def normalize_remote(url: str) -> tuple[str, bool, bool]:
    """Strip any userinfo. Returns ``(clean_url, had_secret, had_username)``.

    A username in a remote URL is a disambiguation hint and is fine. A token in
    a remote URL is a leak: every command that prints the URL prints the token,
    which is exactly how a PAT reached a log on 2026-08-04.
    """
    m = re.match(r"^(https?://)([^/@]+)@(.*)$", url)
    if not m:
        return url, False, False
    scheme, userinfo, rest = m.groups()
    return scheme + rest, ":" in userinfo, True


def owner_repo(url: str) -> tuple[Optional[str], Optional[str]]:
    """Extract ``(owner, repo)`` from a GitHub URL in any common form."""
    m = re.search(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    return (m.group(1), m.group(2)) if m else (None, None)


def load_deployments(hot: Mapping) -> dict:
    """Return the deployments registry from a loaded HOT dict."""
    meta = hot.get("meta") or {}
    dep = meta.get(PARTITION) or {}
    # A deployment RECORD is a dict. Prose annotations like _purpose are
    # strings. Do NOT filter on a leading underscore: the real deployment
    # keys are _ONLINE_BIZ_PROJECT and _CONTENT_FACTORY_PROJECT, so an
    # underscore filter would silently hide two of the three deployments.
    return {k: v for k, v in dep.items() if isinstance(v, dict)}


def get_deployment(rag_path: Path | str, key: str) -> dict:
    """Return one deployment record. Fail-loud when the key is unrecorded."""
    deps = load_deployments(load_hot(Path(rag_path)))
    if key not in deps:
        raise UnknownDeploymentError(
            f"deployment {key!r} is not recorded in meta.{PARTITION}; "
            f"known: {sorted(deps) or [None]}. A deployment the registry has "
            f"never heard of is refused, not trusted."
        )
    return dict(deps[key])


def set_deployment_field_in_file(
    rag_path: Path | str,
    key: str,
    field: str,
    value: Any,
    *,
    session: str,
    now: Optional[str] = None,
) -> dict:
    """Atomically set one field on one deployment record.

    Load -> validate -> mutate -> atomic write with .bak parity, the same write
    contract as every other governed setter. Refuses an unknown deployment and
    a field outside SETTABLE_FIELDS, and writes nothing when it refuses.
    """
    if field not in SETTABLE_FIELDS:
        raise UnsettableFieldError(
            f"field {field!r} is not settable; allowed: {sorted(SETTABLE_FIELDS)}"
        )
    p = Path(rag_path)
    hot = load_hot(p)
    meta = hot.setdefault("meta", {})
    dep = meta.setdefault(PARTITION, {})
    if key not in dep or not isinstance(dep.get(key), dict):
        raise UnknownDeploymentError(
            f"deployment {key!r} is not recorded in meta.{PARTITION}; "
            f"known: {sorted(k for k, v in dep.items() if isinstance(v, dict))}"
        )
    record = dict(dep[key])
    record[field] = value
    record["last_touched_by"] = session
    dep[key] = record
    _touch_meta(hot, now)
    atomic_write_json(p, hot, mirror_bak=True, guard_side_stores=True)
    return hot


def git_remote_url(root: Path | str, remote: str = "origin") -> str:
    """Read a remote URL from a working tree. Raises on an unreadable tree."""
    proc = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", remote],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise DeploymentRegistryError(
            f"cannot read remote {remote!r} in {root}: {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def check_push_destination(
    rag_path: Path | str,
    key: str,
    root: Path | str,
    *,
    remote: str = "origin",
    actual_url: Optional[str] = None,
) -> PushCheck:
    """Decide whether ``root`` may push. The ONLY pass is ``ok=True``.

    Raises rather than returning a soft answer, because a push gate that can be
    ignored is not a gate. ``actual_url`` is injectable so the decision is
    testable without a git tree.
    """
    record = get_deployment(rag_path, key)
    declared = record.get("authorized_remote")
    if not declared or declared == "UNDECLARED":
        raise UndeclaredDestinationError(
            f"deployment {key!r} declares no authorized_remote. "
            f"Absence of a declared destination is NOT permission."
        )

    url = actual_url if actual_url is not None else git_remote_url(root, remote)
    clean, had_secret, had_username = normalize_remote(url)
    if had_secret:
        raise EmbeddedCredentialError(
            f"remote {remote!r} carries an EMBEDDED CREDENTIAL. Every command "
            f"that prints this URL prints the secret. Use a credential helper."
        )

    got = owner_repo(clean)
    want = owner_repo(declared)
    if got != want:
        raise DestinationMismatchError(
            f"DESTINATION MISMATCH for {key!r}: declared {want[0]}/{want[1]}, "
            f"actual {got[0]}/{got[1]} ({clean}). A pre-existing origin is an "
            f"INHERITED ACCIDENT, not an authorization."
        )

    identity = record.get("authorized_identity")
    if identity and got[0] != identity:
        raise DestinationMismatchError(
            f"owner {got[0]!r} is not the authorized identity {identity!r} "
            f"for deployment {key!r}."
        )

    return PushCheck(
        ok=True,
        code="AUTHORIZED",
        message=f"destination authorized: {got[0]}/{got[1]}",
        declared=declared,
        actual=clean,
    )
