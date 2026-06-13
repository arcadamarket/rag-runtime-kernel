"""FIX-3 (K3/K5/K7): init/configure build-time hygiene.

FIX-1 added fail-loud *detection* for three classes of init defect the eBay
Session-Zero deploy exposed; FIX-3 *prevents* them at build, so a fresh deploy
is born clean rather than merely caught after the fact (the same root-cause-vs-
symptom move FIX-2 made for the COLD↔HOT version drift):

  * K3 — spec_parser substitutes the build-deterministic ``<ISO>`` placeholder
         with the build timestamp across HOT + COLD.
  * K5 — spec_parser strips ``_``-prefixed template/placeholder keys
         (``_required`` / ``_note``) from ``operating_protocol``.
  * K7 — KernelApp mints a canonical ``S<int>`` session id (not ``S-{pid}-...``)
         and stamps ``meta.written_by_session`` on every persisted checkpoint.

Each test pairs the build-prevention with the matching FIX-1 audit invariant so
prevention and detection can never silently disagree (DRY).
"""
from pathlib import Path
import json

import pytest

from rag_kernel.spec_parser import SpecParser, ISO_PLACEHOLDER, VERSION_PLACEHOLDER
from rag_kernel.api import KernelApp, generate_session_id
from rag_kernel import drift_audit

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_322 = REPO_ROOT / "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.2.md"


@pytest.fixture
def parser():
    return SpecParser()


# ── K3 + K5: real v3.2.2 spec is born clean (dogfood) ─────────────

class TestRealSpecBornClean:
    @pytest.fixture
    def parsed(self, parser):
        assert SPEC_322.exists(), f"spec missing: {SPEC_322}"
        return parser.parse_file(SPEC_322)

    def test_no_iso_placeholder_survives_in_hot(self, parsed):
        # The eBay defect was sessions_recent[].d == "<ISO>".
        for loc, val in drift_audit._walk_strings(parsed.merged):
            assert val.strip() != ISO_PLACEHOLDER, f"<ISO> survived at {loc}"

    def test_no_iso_placeholder_survives_in_cold(self, parsed):
        if parsed.cold_template is None:
            pytest.skip("spec has no COLD template")
        for loc, val in drift_audit._walk_strings(parsed.cold_template):
            assert val.strip() != ISO_PLACEHOLDER, f"<ISO> survived at {loc}"

    def test_no_template_keys_in_operating_protocol(self, parsed):
        op = parsed.merged.get("operating_protocol", {})
        assert [k for k in op if k.startswith("_")] == []

    def test_fix1_placeholder_audit_clean(self, parsed):
        # K3 build-prevention must satisfy the FIX-1 detection invariant.
        assert drift_audit.check_placeholder_tokens(parsed.merged) == []

    def test_fix1_template_key_audit_clean(self, parsed):
        # K5 build-prevention must satisfy the FIX-1 detection invariant.
        assert drift_audit.check_template_keys(parsed.merged) == []

    def test_version_substitution_still_works(self, parsed):
        # FIX-3 must not regress FIX-2.
        assert SpecParser._scan_placeholder(parsed.merged) == []


# ── K3: <ISO> substitution unit behavior ─────────────────────────

class TestIsoSubstitution:
    def test_recursive_substitution(self):
        obj = {"a": ISO_PLACEHOLDER,
               "b": ["x", ISO_PLACEHOLDER],
               "c": {"d": ISO_PLACEHOLDER}}
        out = SpecParser._substitute_iso(obj, "2026-01-01T00:00:00+00:00")
        assert out == {"a": "2026-01-01T00:00:00+00:00",
                       "b": ["x", "2026-01-01T00:00:00+00:00"],
                       "c": {"d": "2026-01-01T00:00:00+00:00"}}

    def test_empty_timestamp_is_noop(self):
        obj = {"a": ISO_PLACEHOLDER}
        assert SpecParser._substitute_iso(obj, "") == obj

    def test_non_iso_strings_untouched(self):
        # <from user> / <absolute path> need human input, not a build timestamp.
        obj = {"x": "<from user>", "y": "<absolute path>", "z": "plain"}
        assert SpecParser._substitute_iso(obj, "NOW") == obj

    def test_version_token_untouched_by_iso(self):
        obj = {"v": VERSION_PLACEHOLDER}
        assert SpecParser._substitute_iso(obj, "NOW") == obj


# ── K5: template-key stripping unit behavior ─────────────────────

class TestStripTemplateKeys:
    def test_strips_underscore_keys(self):
        rag = {"operating_protocol": {
            "_required": ["a"], "_note": "x", "real_rule": "keep"}}
        SpecParser._strip_template_keys(rag)
        assert rag["operating_protocol"] == {"real_rule": "keep"}

    def test_noop_without_operating_protocol(self):
        rag = {"meta": {}}
        SpecParser._strip_template_keys(rag)  # must not raise
        assert rag == {"meta": {}}

    def test_noop_on_non_dict(self):
        SpecParser._strip_template_keys(None)  # must not raise


# ── K7: canonical session id + written_by_session stamping ───────

class TestSessionIdK7:
    def test_generated_id_is_canonical_shape(self):
        sid = generate_session_id()
        assert sid.startswith("S")
        assert sid[1:].isdigit()
        # Must NOT match the malformed/negative shape the auditor flags.
        assert not drift_audit._BAD_SESSION_ID_RE.match(sid)

    def test_old_negative_shape_would_have_been_flagged(self):
        # Guards the regression: the OLD default form is exactly what FIX-1 flags.
        assert drift_audit._BAD_SESSION_ID_RE.match("S-12488-1781260490")

    @pytest.fixture
    def project_dir(self, tmp_path):
        d = tmp_path / "RAG"
        d.mkdir()
        (d / "RAG_MASTER.json").write_text(
            json.dumps({
                "meta": {"session_id": "", "written_by_session": "",
                         "state_hash": ""},
                "current_status": {"phase": "idle"},
            }),
            encoding="utf-8",
        )
        (d / "RAG_COLD.json").write_text(
            json.dumps({"meta": {"type": "RAG_COLD"}}), encoding="utf-8")
        return d

    def test_default_session_id_is_canonical(self, project_dir):
        app = KernelApp(project_dir)  # no session_id → auto-mint
        assert not drift_audit._BAD_SESSION_ID_RE.match(app.session_id)
        assert app.session_id.startswith("S") and app.session_id[1:].isdigit()

    def test_checkpoint_stamps_written_by_session(self, project_dir):
        app = KernelApp(project_dir)  # auto-mint canonical id
        app.boot()
        app.checkpoint()
        app.close()
        hot = json.loads((project_dir / "RAG_MASTER.json").read_text("utf-8"))
        meta = hot["meta"]
        assert meta["written_by_session"] == app.session_id
        assert meta["session_id"] == app.session_id
        # And the persisted lineage passes the FIX-1 audit invariants.
        assert drift_audit.check_written_by_session(hot) == []
        assert drift_audit.check_session_id_coherence(hot) == []
