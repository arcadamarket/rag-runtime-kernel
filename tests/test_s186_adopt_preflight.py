"""S186 -- adoption pre-flight: refuse an upgrade that deletes local work.

The 2026-08-04 regression was invisible at file level: the module survived and a
single CLI flag did not. So the CLI surface is compared, not just the file list.
"""
import shutil
from pathlib import Path

import pytest

from rag_kernel.adopt_preflight import (
    Divergence,
    LocalDivergenceError,
    ProbeFailedError,
    assert_safe_to_adopt,
    cli_surface,
    file_surface,
    preflight,
)

REPO = Path(__file__).resolve().parent.parent


def test_divergence_is_clean_only_when_every_axis_is_empty():
    assert Divergence().clean
    assert not Divergence(files=("x.py",)).clean
    assert not Divergence(commands=("verb",)).clean
    assert not Divergence(options=("verb --flag",)).clean


def test_render_names_each_axis():
    text = Divergence(files=("a.py",), commands=("v",), options=("v --f",)).render()
    assert "MODULES" in text and "VERBS" in text and "FLAGS" in text


def test_file_surface_lists_modules():
    names = file_surface(REPO / "rag_kernel")
    assert "adopt_preflight.py" in names and "deployment_registry.py" in names


def test_probe_failure_refuses_rather_than_reporting_an_empty_surface(tmp_path):
    """UNKNOWN is a refusal. An un-probed target would report zero losses."""
    (tmp_path / "rag_kernel").mkdir()
    with pytest.raises(ProbeFailedError):
        cli_surface(tmp_path)


def test_identical_packages_diverge_on_nothing():
    assert preflight(REPO, REPO).clean


def test_a_removed_module_is_reported(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(REPO / "rag_kernel", src / "rag_kernel",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (src / "rag_kernel" / "adopt_preflight.py").unlink()
    div = preflight(REPO, src)
    assert "adopt_preflight.py" in div.files


def test_a_removed_flag_is_reported_and_refused(tmp_path):
    """The shape of the real incident: module intact, one flag gone."""
    src = tmp_path / "src"
    shutil.copytree(REPO / "rag_kernel", src / "rag_kernel",
                    ignore=shutil.ignore_patterns("__pycache__"))
    main = src / "rag_kernel" / "__main__.py"
    text = main.read_text(encoding="utf-8")
    marker = "reg_asset_parser.add_argument(" + chr(34) + "--update"
    assert marker in text
    start = text.index(marker)
    end = text.index("reg_asset_parser.add_argument(" + chr(34) + "--rag-dir", start)
    main.write_text(text[:start] + text[end:], encoding="utf-8")

    div = preflight(REPO, src)
    assert "register-asset --update" in div.options
    assert not div.files
    with pytest.raises(LocalDivergenceError) as ex:
        assert_safe_to_adopt(REPO, src)
    assert "deletion mechanism" in str(ex.value)
    assert assert_safe_to_adopt(REPO, src, accept_local_loss=True) is not None
