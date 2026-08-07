"""META-SETTER-GAP proper (S186 opened, S188 closed).

``refresh-current-status`` reaches ``current_status`` TOKENS and
``prune-current-status`` reaches ``current_status`` KEYS — but nothing reached
``meta``. A wrong ``meta.written_by_session`` or a drifted ``meta.policy_version``
had exactly one repair available: the hand edit ``tool_contract`` forbids.

These tests pin the governed setter and, more importantly, its REFUSALS. A setter
that can write anything is not a governed setter; the value here is in what it says
no to, and in the fact that a refusal writes nothing at all.
"""

from __future__ import annotations

import json

import pytest

from rag_kernel.meta_setter import (
    CONTAINER_KEYS,
    SETTABLE,
    MetaSetterError,
    coerce,
    get_meta_scalar,
    set_meta_scalar_file,
)
from rag_kernel.__main__ import main


def _hot():
    return {
        "tracked_items": [],
        "meta": {
            "last_updated_utc": "2020-01-01T00:00:00Z",
            "written_by_session": "S1",
            "last_checkpoint_seq": 10,
            "policy_version": "3.2.8",
            "rag_files": {"hot": "RAG_MASTER.json"},
        },
    }


@pytest.fixture()
def rag(tmp_path):
    p = tmp_path / "RAG_MASTER.json"
    p.write_text(json.dumps(_hot()), encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# coercion — typed, fail-loud, no silent success
# --------------------------------------------------------------------------

class TestCoerce:
    def test_int_field_takes_int_and_numeric_string(self):
        assert coerce("last_checkpoint_seq", 291) == 291
        assert coerce("last_checkpoint_seq", " 291 ") == 291

    def test_int_field_refuses_prose(self):
        with pytest.raises(MetaSetterError):
            coerce("last_checkpoint_seq", "two hundred")

    def test_int_field_refuses_bool(self):
        """True coercing to 1 is the silent success that produces a wrong seq."""
        with pytest.raises(MetaSetterError):
            coerce("last_checkpoint_seq", True)

    def test_str_field_stringifies(self):
        assert coerce("written_by_session", "S188") == "S188"

    def test_undeclared_key_refused(self):
        with pytest.raises(MetaSetterError):
            coerce("not_a_field", "x")


# --------------------------------------------------------------------------
# the write contract
# --------------------------------------------------------------------------

class TestSetMetaScalarFile:
    def test_sets_and_reports_old_new(self, rag):
        old, new, wrote = set_meta_scalar_file(
            rag, "written_by_session", "S188", session="S188"
        )
        assert (old, new, wrote) == ("S1", "S188", True)
        assert get_meta_scalar(json.loads(rag.read_text()), "written_by_session") == "S188"

    def test_int_coercion_persists_as_int_not_string(self, rag):
        set_meta_scalar_file(rag, "last_checkpoint_seq", "291", session="S188")
        val = json.loads(rag.read_text())["meta"]["last_checkpoint_seq"]
        assert val == 291 and isinstance(val, int)

    def test_no_op_when_already_correct_writes_nothing(self, rag):
        before = rag.read_bytes()
        old, new, wrote = set_meta_scalar_file(
            rag, "written_by_session", "S1", session="S188"
        )
        assert wrote is False
        assert rag.read_bytes() == before, "a no-op must not perturb HOT/.bak parity"

    def test_dry_run_writes_nothing(self, rag):
        before = rag.read_bytes()
        old, new, wrote = set_meta_scalar_file(
            rag, "written_by_session", "S999", session="S188", dry_run=True
        )
        assert (old, new, wrote) == ("S1", "S999", False)
        assert rag.read_bytes() == before

    def test_touches_last_updated(self, rag):
        set_meta_scalar_file(rag, "written_by_session", "S188", session="S188")
        assert json.loads(rag.read_text())["meta"]["last_updated_utc"] != \
            "2020-01-01T00:00:00Z"

    def test_session_required(self, rag):
        with pytest.raises(MetaSetterError):
            set_meta_scalar_file(rag, "written_by_session", "S188", session="")


class TestRefusals:
    def test_undeclared_key_refused_and_nothing_written(self, rag):
        before = rag.read_bytes()
        with pytest.raises(MetaSetterError) as ex:
            set_meta_scalar_file(rag, "rag_type", "COLD", session="S188")
        assert "not a declared settable scalar" in str(ex.value)
        assert rag.read_bytes() == before

    def test_container_key_refused_by_name_with_its_owner(self, rag):
        with pytest.raises(MetaSetterError) as ex:
            set_meta_scalar_file(rag, "rag_files", "x", session="S188")
        assert "container" in str(ex.value)

    def test_every_container_key_is_refused(self, rag):
        for key in CONTAINER_KEYS:
            with pytest.raises(MetaSetterError):
                set_meta_scalar_file(rag, key, "x", session="S188")

    def test_declared_and_container_sets_are_disjoint(self):
        """A key that is both settable and refused would make the verb undecidable."""
        assert not (set(SETTABLE) & set(CONTAINER_KEYS))

    def test_bad_int_refused_and_nothing_written(self, rag):
        before = rag.read_bytes()
        with pytest.raises(MetaSetterError):
            set_meta_scalar_file(rag, "last_checkpoint_seq", "abc", session="S188")
        assert rag.read_bytes() == before


# --------------------------------------------------------------------------
# CLI surface
# --------------------------------------------------------------------------

class TestCli:
    def test_list_names_every_declared_key(self, rag, capsys):
        assert main(["meta", "--list", "--rag", str(rag)]) == 0
        out = capsys.readouterr().out
        for k in SETTABLE:
            assert k in out

    def test_get_prints_value(self, rag, capsys):
        assert main(["meta", "--get", "written_by_session", "--rag", str(rag)]) == 0
        assert capsys.readouterr().out.strip() == "S1"

    def test_set_round_trip(self, rag, capsys):
        rc = main(["meta", "--set", "written_by_session=S188",
                   "--session", "S188", "--rag", str(rag)])
        assert rc == 0
        assert json.loads(rag.read_text())["meta"]["written_by_session"] == "S188"

    def test_set_without_session_exits_1(self, rag):
        assert main(["meta", "--set", "written_by_session=S188",
                     "--rag", str(rag)]) == 1

    def test_set_undeclared_exits_1(self, rag):
        assert main(["meta", "--set", "rag_type=COLD",
                     "--session", "S188", "--rag", str(rag)]) == 1

    def test_malformed_set_exits_1(self, rag):
        assert main(["meta", "--set", "written_by_session",
                     "--session", "S188", "--rag", str(rag)]) == 1
