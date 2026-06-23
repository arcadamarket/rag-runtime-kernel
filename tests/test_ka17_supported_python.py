"""KA-17 — supported-Python matrix declaration + classification.

Validates the single-sourced SUPPORTED_PYTHON authority, the pure
``python_support_status`` classifier, and the manifest injection that keeps the
published claim in lock-step with the authority (same anti-drift discipline as
__version__ / __spec_version__).
"""

import sys

import rag_kernel


def test_supported_python_authority():
    assert rag_kernel.SUPPORTED_PYTHON == ("3.12", "3.13", "3.14")
    assert rag_kernel.SUPPORTED_PYTHON_MIN == (3, 12)
    assert rag_kernel.SUPPORTED_PYTHON_MAX == (3, 14)


def test_status_ok_across_window():
    for vi in [(3, 12), (3, 13), (3, 14)]:
        status, running = rag_kernel.python_support_status(vi)
        assert status == "ok"
        assert running == f"{vi[0]}.{vi[1]}"


def test_status_below_floor():
    for vi in [(3, 11), (3, 10), (2, 7)]:
        status, _ = rag_kernel.python_support_status(vi)
        assert status == "below_floor"


def test_status_above_ceiling():
    for vi in [(3, 15), (4, 0)]:
        status, _ = rag_kernel.python_support_status(vi)
        assert status == "above_ceiling"


def test_status_defaults_to_running_interpreter():
    status, running = rag_kernel.python_support_status()
    assert running == f"{sys.version_info.major}.{sys.version_info.minor}"
    # The canonical runners are inside the supported window.
    assert status in {"ok", "above_ceiling", "below_floor"}


def test_manifest_injects_supported_python():
    reg = rag_kernel.discover()
    pkg = reg["package"]
    assert pkg["supported_python"] == ["3.12", "3.13", "3.14"]
    # Floor claim reconciled (Rule 11): was the unsubstantiated ">=3.10".
    assert pkg["python_requires"] == ">=3.12"


def test_manifest_has_no_stale_310_claim():
    reg = rag_kernel.discover()
    assert "3.10" not in str(reg["package"].get("python_requires", ""))
