"""FIX-11 inc2 / U3 — the `context` CLI group over the sanctioned store.

inc1 introduced the sanctioned, NON-LOADED ``RAG_CONTEXT.json`` store
(``cold_manager.ProjectContextManager``) and allowlisted it so neither the live
pre-write guard nor ``audit`` flags it. inc2 wires the operator-facing CLI:

    python -m rag_kernel context set  <partition> '<json>'  [--value-file F] [--rag-dir DIR]
    python -m rag_kernel context get  <partition>           [--rag-dir DIR] [--json]
    python -m rag_kernel context list                       [--rag-dir DIR] [--json]

Contract pinned here:
  * ``set`` round-trips a JSON partition through the sanctioned file atomically
    and writes NO ``.bak`` (COLD-style — the FIX-11 contract, NOT the HOT FIX-4/K6
    parity rule),
  * ``get`` lazy-loads and prints a single partition; an unknown partition fails
    LOUD (exit 1) and prints nothing useful to stdout,
  * ``list`` renders partitions + loaded state + token budget (and an empty store
    reports cleanly rather than erroring),
  * bad JSON / a missing --value-file fail LOUD and write nothing,
  * ``--dry-run`` validates without writing,
  * the file the CLI produces is exactly the sanctioned store the inc1 auditor
    leaves alone, while a transient ``*_context.json`` stays flagged (integration
    guard so the CLI can never reintroduce the U3 side-store drift).

CLI-only increment: no new module, health stays 20/20.
"""

from __future__ import annotations

import json

import pytest

from rag_kernel.__main__ import main
from rag_kernel.cold_manager import CONTEXT_FILENAME
from rag_kernel.persistence import find_context_side_stores


def _store(tmp_path):
    return tmp_path / CONTEXT_FILENAME


def _read(tmp_path):
    return json.loads(_store(tmp_path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------

def test_set_creates_partition_and_persists(tmp_path, capsys):
    rc = main(["context", "set", "comps", '{"sku-1": {"median": 42.0}}',
               "--rag-dir", str(tmp_path)])
    assert rc == 0
    assert _read(tmp_path)["comps"]["sku-1"]["median"] == 42.0
    out = capsys.readouterr().out
    assert "comps" in out


def test_set_writes_no_bak_mirror(tmp_path):
    # The sanctioned store follows COLD, not the HOT FIX-4/K6 .bak parity contract.
    main(["context", "set", "p", '{"a": 1}', "--rag-dir", str(tmp_path)])
    assert _store(tmp_path).exists()
    assert not (tmp_path / (CONTEXT_FILENAME + ".bak")).exists()


def test_set_replaces_existing_partition(tmp_path, capsys):
    main(["context", "set", "p", '{"a": 1}', "--rag-dir", str(tmp_path)])
    rc = main(["context", "set", "p", '{"a": 2}', "--rag-dir", str(tmp_path)])
    assert rc == 0
    assert _read(tmp_path)["p"]["a"] == 2
    assert "replace" in capsys.readouterr().out.lower()


def test_set_from_value_file(tmp_path):
    vf = tmp_path / "value.json"
    vf.write_text('{"loaded": "from-file"}', encoding="utf-8")
    rc = main(["context", "set", "blob", "--value-file", str(vf),
               "--rag-dir", str(tmp_path)])
    assert rc == 0
    assert _read(tmp_path)["blob"]["loaded"] == "from-file"


def test_set_accepts_scalar_and_array_json(tmp_path):
    assert main(["context", "set", "n", "7", "--rag-dir", str(tmp_path)]) == 0
    assert main(["context", "set", "arr", "[1, 2, 3]", "--rag-dir", str(tmp_path)]) == 0
    data = _read(tmp_path)
    assert data["n"] == 7
    assert data["arr"] == [1, 2, 3]


def test_set_invalid_json_fails_loud_and_writes_nothing(tmp_path, capsys):
    rc = main(["context", "set", "bad", "{not json}", "--rag-dir", str(tmp_path)])
    assert rc == 1
    assert "not valid JSON" in capsys.readouterr().err
    assert not _store(tmp_path).exists()


def test_set_missing_value_file_fails_loud(tmp_path, capsys):
    rc = main(["context", "set", "p", "--value-file", str(tmp_path / "nope.json"),
               "--rag-dir", str(tmp_path)])
    assert rc == 1
    assert "not found" in capsys.readouterr().err
    assert not _store(tmp_path).exists()


def test_set_no_value_at_all_fails_loud(tmp_path, capsys):
    rc = main(["context", "set", "p", "--rag-dir", str(tmp_path)])
    assert rc == 1
    assert "provide the value" in capsys.readouterr().err.lower()


def test_set_dry_run_writes_nothing(tmp_path, capsys):
    rc = main(["context", "set", "p", '{"a": 1}', "--rag-dir", str(tmp_path), "--dry-run"])
    assert rc == 0
    assert "[DRY RUN]" in capsys.readouterr().out
    assert not _store(tmp_path).exists()


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

def test_get_round_trips_set(tmp_path, capsys):
    main(["context", "set", "comps", '{"x": 10}', "--rag-dir", str(tmp_path)])
    capsys.readouterr()  # drain the set output so we json.loads only the get output
    rc = main(["context", "get", "comps", "--rag-dir", str(tmp_path), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"x": 10}


def test_get_human_output_has_header(tmp_path, capsys):
    main(["context", "set", "comps", '{"x": 10}', "--rag-dir", str(tmp_path)])
    rc = main(["context", "get", "comps", "--rag-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "# comps" in out
    assert '"x": 10' in out


def test_get_unknown_partition_fails_loud(tmp_path, capsys):
    main(["context", "set", "present", '{"a": 1}', "--rag-dir", str(tmp_path)])
    rc = main(["context", "get", "absent", "--rag-dir", str(tmp_path)])
    assert rc == 1
    assert "absent" in capsys.readouterr().err


def test_get_on_missing_store_fails_loud(tmp_path, capsys):
    rc = main(["context", "get", "anything", "--rag-dir", str(tmp_path)])
    assert rc == 1


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_list_empty_store_reports_cleanly(tmp_path, capsys):
    rc = main(["context", "list", "--rag-dir", str(tmp_path)])
    assert rc == 0
    assert "no project-context partitions" in capsys.readouterr().out


def test_list_shows_partitions_on_disk(tmp_path, capsys):
    main(["context", "set", "alpha", '{"a": 1}', "--rag-dir", str(tmp_path)])
    main(["context", "set", "beta", '{"b": 2}', "--rag-dir", str(tmp_path)])
    rc = main(["context", "list", "--rag-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "alpha" in out and "beta" in out
    # A fresh manager has loaded nothing — non-loaded is the whole point.
    assert "on-disk" in out


def test_list_json_summary(tmp_path, capsys):
    main(["context", "set", "alpha", '{"a": 1}', "--rag-dir", str(tmp_path)])
    capsys.readouterr()  # drain the set output so we json.loads only the list output
    rc = main(["context", "list", "--rag-dir", str(tmp_path), "--json"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["total_partitions"] == 1
    assert "alpha" in summary["partition_names"]
    # Nothing loaded in a read-only list => zero tokens consumed.
    assert summary["estimated_tokens"] == 0


# ---------------------------------------------------------------------------
# no sub-action
# ---------------------------------------------------------------------------

def test_bare_context_prints_usage(capsys):
    rc = main(["context"])
    assert rc == 1
    assert "Usage" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# integration: the CLI-produced file is the sanctioned store, not a side store
# ---------------------------------------------------------------------------

def test_cli_output_is_not_flagged_as_side_store(tmp_path):
    main(["context", "set", "comps", '{"a": 1}', "--rag-dir", str(tmp_path)])
    # inc1 sanction: the file the CLI just wrote is never flagged...
    assert find_context_side_stores(tmp_path) == []


def test_transient_context_json_still_flagged_beside_cli_store(tmp_path):
    main(["context", "set", "comps", '{"a": 1}', "--rag-dir", str(tmp_path)])
    (tmp_path / "ebay_context.json").write_text("{}", encoding="utf-8")
    hits = {p.name for p in find_context_side_stores(tmp_path)}
    assert hits == {"ebay_context.json"}


# ---------------------------------------------------------------------------
# KA-CTX-RAGFLAG: `--rag <FILE>` must not crash the context verb
# ---------------------------------------------------------------------------
# Every other verb takes `--rag <RAG_MASTER.json>` (a FILE). The context verb
# takes `--rag-dir <DIR>`, but argparse prefix-matches the operator's habitual
# `--rag` to `--rag-dir` (the only `--rag*` option in this subparser). Before
# the fix, a file-valued rag_dir made the manager build `<file>/RAG_CONTEXT.json`
# and crash FileExistsError at mkdir. The verb must instead fall back to the
# file's containing directory (robustness, Rule 15 lane A).

def test_ka_ctx_ragflag_abbrev_rag_flag_on_existing_file_routes_to_parent(tmp_path, capsys):
    # Authentic reproduction: operator types the abbreviated `--rag` (prefix of
    # `--rag-dir`) pointing at a real RAG file. Must succeed, storing beside it.
    rag_file = tmp_path / "RAG_MASTER.json"
    rag_file.write_text("{}", encoding="utf-8")
    rc = main(["context", "set", "comps", '{"a": 1}', "--rag", str(rag_file)])
    assert rc == 0
    # The store lands in the parent dir, NOT at <file>/RAG_CONTEXT.json.
    assert _read(tmp_path)["comps"]["a"] == 1
    assert not (rag_file / CONTEXT_FILENAME).exists()
    assert "using its directory" in capsys.readouterr().err


def test_ka_ctx_ragflag_json_suffix_path_routes_to_parent(tmp_path, capsys):
    # The `.json` suffix branch: even a not-yet-existing file path that names a
    # .json file routes to its parent rather than being treated as a directory.
    rag_file = tmp_path / "RAG_MASTER.json"  # note: never created on disk
    rc = main(["context", "set", "comps", '{"a": 1}', "--rag-dir", str(rag_file)])
    assert rc == 0
    assert _read(tmp_path)["comps"]["a"] == 1
    assert "using its directory" in capsys.readouterr().err


def test_ka_ctx_ragflag_real_directory_still_works_silently(tmp_path, capsys):
    # Guard against over-eager redirection: a genuine directory must be used
    # as-is, with no redirect note.
    rc = main(["context", "set", "comps", '{"a": 1}', "--rag-dir", str(tmp_path)])
    assert rc == 0
    assert _read(tmp_path)["comps"]["a"] == 1
    assert "using its directory" not in capsys.readouterr().err
