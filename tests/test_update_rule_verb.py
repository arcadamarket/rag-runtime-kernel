"""UPDATE-RULE-VERB — governed re-set of dict/string ``operating_protocol`` rules.

The UPDATE counterpart to ``add-rule`` (FIX-5/P3). ``add-rule`` is string-only and
its default is ADD (fail-loud on an *existing* key). This verb is the inverse:

  * its default is UPDATE — the target MUST already exist (fail-loud) unless
    ``--create`` — so a typo'd key is refused, not silently minted;
  * ``value`` may be a string OR a JSON object/array/scalar, so structured rules
    (e.g. ``tool_hierarchy``) can be re-set wholesale; and
  * ``--subkey`` sets one sub-key of a dict-valued rule, which is how a dict rule
    is trimmed one sub-entry at a time (unblocks the ``tool_hierarchy`` dict-trim).

Same write contract as add-rule: validate -> atomic write (tmp -> verify -> .bak
parity -> rename). On any guard failure nothing is written.
"""
import json
from pathlib import Path

import pytest

from rag_kernel.drift_store import (
    DriftStoreError,
    OPERATING_PROTOCOL_KEY,
    set_operating_protocol_rule,
    set_operating_protocol_rule_file,
)
from rag_kernel.__main__ import main


def _bak(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def _make_rag(tmp_path, *, op=None) -> Path:
    rag = tmp_path / "RAG_MASTER.json"
    body = {
        "meta": {"last_checkpoint_seq": 1, "written_by_session": "T"},
        "operating_protocol": {} if op is None else dict(op),
        "tracked_items": [],
        "open_tasks": [],
        "deferred_items": [],
    }
    rag.write_text(json.dumps(body), encoding="utf-8")
    return rag


# ===========================================================================
# set_operating_protocol_rule (pure, on a dict) — whole-value
# ===========================================================================

def test_update_replaces_existing_string():
    hot = {"operating_protocol": {"strict_obey": "old", "other": "x"}}
    set_operating_protocol_rule(hot, "strict_obey", "new")
    assert hot["operating_protocol"]["strict_obey"] == "new"
    assert hot["operating_protocol"]["other"] == "x"  # neighbours untouched


def test_update_fail_loud_on_missing_key():
    hot = {"operating_protocol": {"a": "x"}}
    with pytest.raises(DriftStoreError):
        set_operating_protocol_rule(hot, "missing", "v")
    assert "missing" not in hot["operating_protocol"]  # nothing minted


def test_update_create_adds_missing_key():
    hot = {"operating_protocol": {}}
    set_operating_protocol_rule(hot, "new_rule", "v", create=True)
    assert hot["operating_protocol"]["new_rule"] == "v"


def test_update_accepts_dict_value():
    hot = {"operating_protocol": {"tool_hierarchy": {"a": "1", "b": "2"}}}
    set_operating_protocol_rule(
        hot, "tool_hierarchy", {"a": "1-trimmed", "b": "2"})
    assert hot["operating_protocol"]["tool_hierarchy"] == {"a": "1-trimmed", "b": "2"}


def test_update_absent_operating_protocol_raises():
    with pytest.raises(DriftStoreError):
        set_operating_protocol_rule({}, "k", "v", create=True)


def test_update_non_dict_operating_protocol_raises():
    with pytest.raises(DriftStoreError):
        set_operating_protocol_rule({"operating_protocol": ["x"]}, "k", "v", create=True)


@pytest.mark.parametrize("bad_key", ["", "   ", None, 7])
def test_update_rejects_bad_key(bad_key):
    with pytest.raises(DriftStoreError):
        set_operating_protocol_rule({"operating_protocol": {}}, bad_key, "v", create=True)


def test_update_rejects_null_value():
    with pytest.raises(DriftStoreError):
        set_operating_protocol_rule({"operating_protocol": {"k": "x"}}, "k", None)


@pytest.mark.parametrize("bad_value", ["", "   "])
def test_update_rejects_empty_string_value(bad_value):
    with pytest.raises(DriftStoreError):
        set_operating_protocol_rule({"operating_protocol": {"k": "x"}}, "k", bad_value)


# ===========================================================================
# set_operating_protocol_rule (pure) — sub-key of a dict rule
# ===========================================================================

def test_subkey_updates_existing_subkey():
    hot = {"operating_protocol": {"tool_hierarchy": {"file_rw": "long", "git": "g"}}}
    set_operating_protocol_rule(hot, "tool_hierarchy", "lean", subkey="file_rw")
    assert hot["operating_protocol"]["tool_hierarchy"]["file_rw"] == "lean"
    assert hot["operating_protocol"]["tool_hierarchy"]["git"] == "g"  # sibling untouched


def test_subkey_fail_loud_on_missing_subkey():
    hot = {"operating_protocol": {"tool_hierarchy": {"file_rw": "x"}}}
    with pytest.raises(DriftStoreError):
        set_operating_protocol_rule(hot, "tool_hierarchy", "v", subkey="nope")
    assert "nope" not in hot["operating_protocol"]["tool_hierarchy"]


def test_subkey_create_adds_missing_subkey():
    hot = {"operating_protocol": {"tool_hierarchy": {"file_rw": "x"}}}
    set_operating_protocol_rule(hot, "tool_hierarchy", "v", subkey="new", create=True)
    assert hot["operating_protocol"]["tool_hierarchy"]["new"] == "v"


def test_subkey_on_non_dict_rule_raises():
    hot = {"operating_protocol": {"strict_obey": "a string rule"}}
    with pytest.raises(DriftStoreError):
        set_operating_protocol_rule(hot, "strict_obey", "v", subkey="x", create=True)


def test_subkey_on_missing_rule_raises():
    hot = {"operating_protocol": {}}
    with pytest.raises(DriftStoreError):
        set_operating_protocol_rule(hot, "tool_hierarchy", "v", subkey="x", create=True)


# ===========================================================================
# set_operating_protocol_rule_file (atomic, .bak parity)
# ===========================================================================

def test_file_update_writes_and_mirrors_bak(tmp_path):
    rag = _make_rag(tmp_path, op={"strict_obey": "old"})
    set_operating_protocol_rule_file(rag, "strict_obey", "new")
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["operating_protocol"]["strict_obey"] == "new"
    assert _bak(rag).read_bytes() == rag.read_bytes()  # FIX-4 parity


def test_file_update_touches_meta(tmp_path):
    rag = _make_rag(tmp_path, op={"k": "v"})
    set_operating_protocol_rule_file(rag, "k", "v2")
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["meta"].get("last_updated_utc")


def test_file_missing_key_fail_loud_no_write(tmp_path):
    rag = _make_rag(tmp_path, op={"a": "x"})
    before = rag.read_text(encoding="utf-8")
    with pytest.raises(DriftStoreError):
        set_operating_protocol_rule_file(rag, "missing", "v")
    assert rag.read_text(encoding="utf-8") == before  # nothing written


def test_file_dict_value_roundtrips(tmp_path):
    rag = _make_rag(tmp_path, op={"tool_hierarchy": {"a": "1", "b": "2"}})
    set_operating_protocol_rule_file(rag, "tool_hierarchy", {"a": "1t", "b": "2"})
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["operating_protocol"]["tool_hierarchy"] == {"a": "1t", "b": "2"}


def test_file_subkey_trim_roundtrips_and_parity(tmp_path):
    rag = _make_rag(tmp_path, op={"tool_hierarchy": {"file_rw": "verbose", "git": "g"}})
    set_operating_protocol_rule_file(rag, "tool_hierarchy", "lean", subkey="file_rw")
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["operating_protocol"]["tool_hierarchy"]["file_rw"] == "lean"
    assert data["operating_protocol"]["tool_hierarchy"]["git"] == "g"
    assert _bak(rag).read_bytes() == rag.read_bytes()


# ===========================================================================
# CLI: rag_kernel update-rule
# ===========================================================================

def test_cli_update_dry_run_no_write(tmp_path, capsys):
    rag = _make_rag(tmp_path, op={"strict_obey": "old"})
    before = rag.read_text(encoding="utf-8")
    rc = main(["update-rule", "strict_obey", "new",
               "--rag", str(rag), "--session", "S116", "--dry-run"])
    assert rc == 0
    assert "[DRY RUN]" in capsys.readouterr().out
    assert rag.read_text(encoding="utf-8") == before


def test_cli_update_string_writes(tmp_path, capsys):
    rag = _make_rag(tmp_path, op={"strict_obey": "old"})
    rc = main(["update-rule", "strict_obey", "new",
               "--rag", str(rag), "--session", "S116"])
    assert rc == 0
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["operating_protocol"]["strict_obey"] == "new"
    assert _bak(rag).read_bytes() == rag.read_bytes()


def test_cli_update_missing_key_fails(tmp_path, capsys):
    rag = _make_rag(tmp_path, op={"a": "x"})
    rc = main(["update-rule", "missing", "v",
               "--rag", str(rag), "--session", "S116"])
    assert rc == 1
    assert "no rule" in capsys.readouterr().err


def test_cli_update_create_adds(tmp_path, capsys):
    rag = _make_rag(tmp_path)
    rc = main(["update-rule", "root_hygiene", "keep clean", "--create",
               "--rag", str(rag), "--session", "S116"])
    assert rc == 0
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["operating_protocol"]["root_hygiene"] == "keep clean"


def test_cli_update_json_dict_value(tmp_path, capsys):
    rag = _make_rag(tmp_path, op={"tool_hierarchy": {"a": "1", "b": "2"}})
    rc = main(["update-rule", "tool_hierarchy", '{"a": "1t", "b": "2"}', "--json",
               "--rag", str(rag), "--session", "S116"])
    assert rc == 0
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["operating_protocol"]["tool_hierarchy"] == {"a": "1t", "b": "2"}


def test_cli_update_bad_json_errors(tmp_path, capsys):
    rag = _make_rag(tmp_path, op={"tool_hierarchy": {"a": "1"}})
    rc = main(["update-rule", "tool_hierarchy", "{not json}", "--json",
               "--rag", str(rag), "--session", "S116"])
    assert rc == 1
    assert "not valid JSON" in capsys.readouterr().err


def test_cli_update_subkey_trim(tmp_path, capsys):
    rag = _make_rag(tmp_path, op={"tool_hierarchy": {"file_rw": "verbose", "git": "g"}})
    rc = main(["update-rule", "tool_hierarchy", "lean", "--subkey", "file_rw",
               "--rag", str(rag), "--session", "S116"])
    assert rc == 0
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["operating_protocol"]["tool_hierarchy"]["file_rw"] == "lean"
    assert data["operating_protocol"]["tool_hierarchy"]["git"] == "g"


def test_cli_update_subkey_missing_fails(tmp_path, capsys):
    rag = _make_rag(tmp_path, op={"tool_hierarchy": {"file_rw": "x"}})
    rc = main(["update-rule", "tool_hierarchy", "v", "--subkey", "nope",
               "--rag", str(rag), "--session", "S116"])
    assert rc == 1
    assert "no sub-key" in capsys.readouterr().err


def test_cli_update_subkey_on_non_dict_fails(tmp_path, capsys):
    rag = _make_rag(tmp_path, op={"strict_obey": "a string"})
    rc = main(["update-rule", "strict_obey", "v", "--subkey", "x",
               "--rag", str(rag), "--session", "S116"])
    assert rc == 1
    assert "not a JSON object" in capsys.readouterr().err


def test_cli_update_value_file(tmp_path, capsys):
    rag = _make_rag(tmp_path, op={"strict_obey": "old"})
    vf = tmp_path / "rule.txt"
    long_rule = "Rule 16. " + ("Obey exactly. " * 30)
    vf.write_text(long_rule, encoding="utf-8")
    rc = main(["update-rule", "strict_obey", "--value-file", str(vf),
               "--rag", str(rag), "--session", "S116"])
    assert rc == 0
    data = json.loads(rag.read_text(encoding="utf-8"))
    assert data["operating_protocol"]["strict_obey"] == long_rule.strip()


def test_cli_update_missing_value_errors(tmp_path, capsys):
    rag = _make_rag(tmp_path, op={"strict_obey": "old"})
    rc = main(["update-rule", "strict_obey",
               "--rag", str(rag), "--session", "S116"])
    assert rc == 1
    assert "provide the value" in capsys.readouterr().err
