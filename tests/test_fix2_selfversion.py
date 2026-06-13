"""FIX-2 (K4/K8): single self-version token + deterministic verify gate.

The COLD template historically hard-coded an ``init_prompt_reference`` version
(``3.1.9``) that ``init`` copied verbatim, birthing a COLD↔HOT version drift on
every fresh deploy — the root cause FIX-1 could only *detect* after the fact.

spec_parser now substitutes a single ``<SPEC_VERSION>`` token (parsed from the
spec header) across HOT + COLD and stamps the COLD reference from that same
source, so the two can never drift apart at init. ``rag_kernel verify`` fails
loud on any residual drift or unsubstituted token.
"""
from pathlib import Path
import json

import pytest

from rag_kernel.spec_parser import SpecParser, VERSION_PLACEHOLDER

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_322 = REPO_ROOT / "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.2.md"


@pytest.fixture
def parser():
    return SpecParser()


# ── Real v3.2.2 spec: end-to-end self-version coherence ──────────

class TestRealSpecSelfVersion:
    @pytest.fixture
    def parsed(self, parser):
        assert SPEC_322.exists(), f"spec missing: {SPEC_322}"
        return parser.parse_file(SPEC_322)

    def test_spec_version_detected(self, parsed):
        assert parsed.spec_version == "3.2.2"

    def test_hot_policy_version_stamped(self, parsed):
        assert parsed.merged["meta"]["policy_version"] == "3.2.2"

    def test_hot_init_prompt_stamped(self, parsed):
        assert (parsed.merged["meta"]["rag_files"]["init_prompt"]
                == "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.2.md")

    def test_cold_reference_stamped(self, parsed):
        ipr = parsed.cold_template["init_prompt_reference"]
        assert ipr["version"] == "3.2.2"
        assert ipr["filename"] == "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.2.md"

    def test_no_version_placeholder_survives(self, parsed):
        assert SpecParser._scan_placeholder(parsed.merged) == []
        assert SpecParser._scan_placeholder(parsed.cold_template) == []

    def test_no_version_parse_errors(self, parsed):
        assert [e for e in parsed.errors if e.section_id == "version"] == []

    def test_hot_cold_coherent(self, parsed):
        findings = SpecParser.verify_coherence(
            parsed.merged, parsed.cold_template, parsed.spec_version)
        assert findings == []

    def test_spec_templates_no_longer_hardcode_319(self):
        text = SPEC_322.read_text(encoding="utf-8")
        # The single self-version token must be present (parametrized)...
        assert VERSION_PLACEHOLDER in text
        # ...and the stale 3.1.9 literal must never appear as a JSON template
        # value (prose / version-history references are fine).
        assert '"version": "3.1.9"' not in text
        assert "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.9.md" not in text


# ── Substitution unit behavior ───────────────────────────────────

class TestSubstitution:
    def test_recursive_substitution(self):
        obj = {"a": VERSION_PLACEHOLDER,
               "b": ["x", f"v{VERSION_PLACEHOLDER}.md"],
               "c": {"d": VERSION_PLACEHOLDER}}
        out = SpecParser._substitute_version(obj, "9.9.9")
        assert out == {"a": "9.9.9", "b": ["x", "v9.9.9.md"],
                       "c": {"d": "9.9.9"}}

    def test_empty_version_is_noop(self):
        obj = {"a": VERSION_PLACEHOLDER}
        assert SpecParser._substitute_version(obj, "") == obj

    def test_session_zero_placeholders_untouched(self):
        # <ISO>, <from user> etc. are NOT version tokens.
        obj = {"created_utc": "<ISO>", "project_name": "<from user>",
               "root_project": "<absolute path>"}
        assert SpecParser._substitute_version(obj, "9.9.9") == obj


# ── Fail-loud when the spec header lacks a version ────────────────

class TestFailLoud:
    def test_unsubstituted_token_flagged(self, parser):
        spec = (
            "# INIT with no version token\n\n"
            "## §32 — HOT SCHEMA TEMPLATE\n\n"
            "```rag-config:template\n"
            '{"meta": {"policy_version": "<SPEC_VERSION>"}}\n'
            "```\n"
        )
        result = parser.parse_string(spec)
        assert result.spec_version == ""
        version_errs = [e for e in result.errors if e.section_id == "version"]
        assert version_errs, "expected a fail-loud version ParseError"

    def test_versioned_spec_has_no_survivor(self, parser):
        spec = (
            "# INIT v7.7.7\n\n"
            "## §32 — HOT SCHEMA TEMPLATE\n\n"
            "```rag-config:template\n"
            '{"meta": {"policy_version": "<SPEC_VERSION>",'
            ' "rag_files": {"init_prompt": "INIT_v<SPEC_VERSION>.md"}}}\n'
            "```\n"
        )
        result = parser.parse_string(spec)
        assert result.spec_version == "7.7.7"
        assert [e for e in result.errors if e.section_id == "version"] == []
        assert result.merged["meta"]["policy_version"] == "7.7.7"
        # _merge_blocks canonicalizes the HOT init_prompt filename from the
        # single spec version (the template literal is overwritten).
        assert (result.merged["meta"]["rag_files"]["init_prompt"]
                == "INIT_UNIVERSAL_RUNTIME_KERNEL_v7.7.7.md")


# ── verify_coherence ─────────────────────────────────────────────

class TestVerifyCoherence:
    @staticmethod
    def _hot(v="3.2.2"):
        return {"meta": {"policy_version": v, "rag_files": {
            "init_prompt": f"INIT_UNIVERSAL_RUNTIME_KERNEL_v{v}.md"}}}

    @staticmethod
    def _cold(v="3.2.2"):
        return {"init_prompt_reference": {
            "version": v,
            "filename": f"INIT_UNIVERSAL_RUNTIME_KERNEL_v{v}.md"}}

    def test_clean(self):
        # Narrative fields may legitimately *mention* the token name in prose;
        # only the structural self-version fields are scanned (field-targeted,
        # consistent with the FIX-1 audit invariant), so a coherent RAG whose
        # current_status / sessions_recent narrate the mechanism stays clean.
        hot = self._hot()
        hot["current_status"] = {
            "rag_kernel_version": "v0.4.5 — spec uses one <SPEC_VERSION> token"}
        hot["sessions_recent"] = [{"s": "parametrized to <SPEC_VERSION>"}]
        assert SpecParser.verify_coherence(
            hot, self._cold(), "3.2.2") == []

    def test_cold_hot_version_drift(self):
        findings = SpecParser.verify_coherence(
            self._hot("3.2.2"), self._cold("3.1.9"))
        assert any("version drift" in f for f in findings)

    def test_cold_hot_filename_drift(self):
        hot = self._hot("3.2.2")
        cold = self._cold("3.2.2")
        cold["init_prompt_reference"]["filename"] = "INIT_other.md"
        findings = SpecParser.verify_coherence(hot, cold)
        assert any("init_prompt drift" in f for f in findings)

    def test_spec_mismatch(self):
        findings = SpecParser.verify_coherence(
            self._hot("3.2.2"), self._cold("3.2.2"), "9.9.9")
        assert any("spec version" in f for f in findings)

    def test_placeholder_detected(self):
        hot = {"meta": {"policy_version": VERSION_PLACEHOLDER,
                        "rag_files": {"init_prompt": "x"}}}
        findings = SpecParser.verify_coherence(hot, None)
        assert any("placeholder unsubstituted" in f for f in findings)

    def test_hot_only_is_ok(self):
        assert SpecParser.verify_coherence(self._hot(), None) == []


# ── CLI: rag_kernel init → verify (integration) ──────────────────

class TestVerifyCLI:
    def _init(self, tmp_path):
        from rag_kernel.__main__ import main
        ragdir = tmp_path / "RAG"
        rc = main(["init", "--spec", str(SPEC_322), "--output", str(ragdir),
                   "--root-project", str(tmp_path), "--root-rag", str(ragdir),
                   "--auto-ready"])
        assert rc == 0
        return ragdir

    def test_init_then_verify_passes(self, tmp_path):
        from rag_kernel.__main__ import main
        ragdir = self._init(tmp_path)
        rc = main(["verify", "--rag", str(ragdir / "RAG_MASTER.json"),
                   "--spec", str(SPEC_322)])
        assert rc == 0

    def test_verify_detects_injected_drift(self, tmp_path):
        from rag_kernel.__main__ import main
        ragdir = self._init(tmp_path)
        coldp = ragdir / "RAG_COLD.json"
        c = json.loads(coldp.read_text(encoding="utf-8-sig"))
        c["init_prompt_reference"]["version"] = "3.1.9"
        coldp.write_text(json.dumps(c, indent=2), encoding="utf-8")
        rc = main(["verify", "--rag", str(ragdir / "RAG_MASTER.json")])
        assert rc == 1

    def test_verify_reads_bom_cold(self, tmp_path):
        # Production COLD files carry a UTF-8 BOM; verify must tolerate it.
        from rag_kernel.__main__ import main
        ragdir = self._init(tmp_path)
        coldp = ragdir / "RAG_COLD.json"
        data = coldp.read_text(encoding="utf-8-sig")
        coldp.write_text("﻿" + data, encoding="utf-8")
        rc = main(["verify", "--rag", str(ragdir / "RAG_MASTER.json")])
        assert rc == 0
