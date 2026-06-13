"""
RAG Runtime Kernel — Specification Parser (v3.3)

Deterministic parser for structured init prompt Markdown files.
Extracts fenced `rag-config` and `rag-config:template` blocks,
deep-merges them in document order, and produces RAG_MASTER.json.

Zero external dependencies. Python 3.10+ stdlib only.

Block types:
    ```rag-config          — JSON fragment, deep-merged into RAG
    ```rag-config:template — Base HOT/COLD schema skeleton

Usage:
    from rag_kernel.spec_parser import SpecParser
    parser = SpecParser()
    rag = parser.parse_file("INIT_UNIVERSAL_RUNTIME_KERNEL_v3.1.8.md")
    parser.write_rag(rag, "RAG/RAG_MASTER.json")

CLI:
    python -m rag_kernel.spec_parser --spec path/to/init.md --output RAG/RAG_MASTER.json

@rag-kernel-manifest
{
  "module": "rag_kernel.spec_parser",
  "capability": "spec_parsing",
  "description": "Deterministic MD→RAG parser — zero tokens, zero LLM involvement",
  "exports": ["SpecParser", "ParsedBlock", "ParseError", "ParseResult", "VOID_RAG", "deep_merge"],
  "use_when": "Initializing a new project RAG from an init prompt MD file",
  "never_bypass": false
}
"""

from __future__ import annotations

import json
import re
import sys
import os
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ── Constants ──────────────────────────────────────────────────

# Regex for fenced code blocks with rag-config info string
# Matches: ```rag-config or ```rag-config:template or ```rag-config:cold-template
_FENCE_OPEN = re.compile(r'^```rag-config(?::(\w[\w-]*))?(?:\s|$)')
_FENCE_CLOSE = re.compile(r'^```\s*$')

# Section header regex: ## §N — TITLE
_SECTION_HEADER = re.compile(r'^##\s+§(\S+)\s*[—–-]\s*(.+)$')

# Single self-version placeholder token embedded in the HOT/COLD templates.
# spec_parser substitutes it with the spec's own version (parsed from the
# document header) so HOT policy_version / init_prompt and the COLD
# init_prompt_reference can never drift apart on a fresh deploy (FIX-2, K4).
# NOTE: this is deliberately distinct from the session-zero placeholders
# (<ISO>, <from user>, <absolute path>) which are filled by the LLM, NOT the
# parser, and must therefore NOT trigger the fail-loud survivor scan.
VERSION_PLACEHOLDER = "<SPEC_VERSION>"

# Minimal void RAG — valid structure with empty fields
VOID_RAG: dict[str, Any] = {
    "meta": {
        "schema_version": "5.3",
        "rag_version": "0.1.0",
        "rag_type": "HOT",
        "project_name": "",
        "created_utc": "",
        "last_updated_utc": "",
        "root_project": "",
        "root_deliverables": "",
        "root_rag": "",
        "policy_version": "",
        "state_hash": "",
        "inventory_hash": "",
        "last_checkpoint_seq": 0,
        "last_ingest_seq": 0,
        "written_by_session": "",
        "rag_files": {
            "hot": "RAG_MASTER.json",
            "cold": "RAG_COLD.json",
            "backup": "RAG_MASTER.json.bak",
            "snapshot_log": "RUNTIME_SNAPSHOT.log",
            "init_prompt": ""
        }
    },
    "execution_mode": "autonomous",
    "state_machine_status": "BOOTING",
    "policy_flags": {
        "atomic_writes_required": True,
        "hash_validation_required": True,
        "load_cold_on_demand_only": True,
        "session_close_audit_required": True,
        "proposal_validation_commit_required": True
    },
    "operating_protocol": {},
    "pov_mandate": {"count": 0, "mode": "strict"},
    "pov_roles": [],
    "project_context": {
        "brief": "",
        "principals": {},
        "domain": "",
        "end_goal": ""
    },
    "current_status": {},
    "active_conflicts_count": 0,
    "priority_actions": [],
    "open_tasks": [],
    "deliverables": {},
    "sessions_recent": []
}


# ── Data structures ───────────────────────────────────────────

class ParsedBlock:
    """A single extracted rag-config block with its source context."""
    __slots__ = ("block_type", "section_id", "section_title",
                 "line_start", "line_end", "raw_json", "data")

    def __init__(self, block_type: str, section_id: str,
                 section_title: str, line_start: int, line_end: int,
                 raw_json: str, data: dict):
        self.block_type = block_type      # "config" | "template" | "cold-template"
        self.section_id = section_id      # "0", "3a", "32", etc.
        self.section_title = section_title
        self.line_start = line_start
        self.line_end = line_end
        self.raw_json = raw_json
        self.data = data

    def __repr__(self) -> str:
        return (f"ParsedBlock(type={self.block_type!r}, "
                f"section=§{self.section_id}, "
                f"lines={self.line_start}-{self.line_end})")


class ParseError:
    """A non-fatal parse error — logged and skipped."""
    __slots__ = ("section_id", "line", "message")

    def __init__(self, section_id: str, line: int, message: str):
        self.section_id = section_id
        self.line = line
        self.message = message

    def __repr__(self) -> str:
        return f"ParseError(§{self.section_id}, line {self.line}: {self.message})"


class ParseResult:
    """Complete result of parsing a spec file."""
    __slots__ = ("blocks", "errors", "template", "cold_template",
                 "merged", "source_file", "spec_version", "sections_found")

    def __init__(self):
        self.blocks: list[ParsedBlock] = []
        self.errors: list[ParseError] = []
        self.template: Optional[dict] = None
        self.cold_template: Optional[dict] = None
        self.merged: dict = {}
        self.source_file: str = ""
        self.spec_version: str = ""
        self.sections_found: list[str] = []


# ── Deep merge ─────────────────────────────────────────────────

def deep_merge(base: dict, overlay: dict) -> dict:
    """
    Recursively merge overlay into base. Returns a new dict.
    - Dicts are recursively merged.
    - Lists are replaced (not appended).
    - Scalars are overwritten by overlay.
    """
    result = deepcopy(base)
    for key, value in overlay.items():
        if (key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


# ── Parser ─────────────────────────────────────────────────────

class SpecParser:
    """
    Deterministic parser for structured init prompt Markdown.
    Extracts rag-config blocks, merges them, produces RAG dict.
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def parse_file(self, filepath: str | Path) -> ParseResult:
        """Parse a Markdown spec file and return the result."""
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"Spec file not found: {filepath}")

        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        result = self._parse_lines(lines)
        result.source_file = str(filepath)

        # Extract spec version from first header or title line
        result.spec_version = self._extract_version(lines)

        # Merge all blocks into final RAG
        result.merged = self._merge_blocks(result)

        # Substitute the single self-version token across HOT + COLD,
        # stamp the COLD reference, and fail-loud on any survivor (FIX-2).
        self._postprocess(result)

        return result

    def parse_string(self, content: str) -> ParseResult:
        """Parse Markdown content from a string."""
        lines = content.splitlines(keepends=True)
        result = self._parse_lines(lines)
        result.spec_version = self._extract_version(lines)
        result.merged = self._merge_blocks(result)
        self._postprocess(result)
        return result

    def _parse_lines(self, lines: list[str]) -> ParseResult:
        """Extract all rag-config blocks from lines."""
        result = ParseResult()

        current_section_id = "preamble"
        current_section_title = "Preamble"
        in_fence = False
        fence_type = ""       # "config" | "template" | "cold-template"
        fence_start = 0
        fence_lines: list[str] = []

        for i, line in enumerate(lines, start=1):
            stripped = line.rstrip("\n\r")

            # Track section headers
            m = _SECTION_HEADER.match(stripped)
            if m and not in_fence:
                current_section_id = m.group(1)
                current_section_title = m.group(2).strip()
                result.sections_found.append(f"§{current_section_id}")
                continue

            # Check for fence open
            if not in_fence:
                m = _FENCE_OPEN.match(stripped)
                if m:
                    in_fence = True
                    subtype = m.group(1)  # None, "template", "cold-template"
                    if subtype is None:
                        fence_type = "config"
                    else:
                        fence_type = subtype
                    fence_start = i
                    fence_lines = []
                    continue
            else:
                # Check for fence close
                if _FENCE_CLOSE.match(stripped):
                    # Parse the accumulated JSON
                    raw_json = "\n".join(fence_lines)
                    try:
                        data = json.loads(raw_json)
                        if not isinstance(data, dict):
                            result.errors.append(ParseError(
                                current_section_id, fence_start,
                                f"rag-config block must be a JSON object, "
                                f"got {type(data).__name__}"
                            ))
                        else:
                            block = ParsedBlock(
                                block_type=fence_type,
                                section_id=current_section_id,
                                section_title=current_section_title,
                                line_start=fence_start,
                                line_end=i,
                                raw_json=raw_json,
                                data=data
                            )
                            result.blocks.append(block)

                            if fence_type == "template":
                                result.template = data
                            elif fence_type == "cold-template":
                                result.cold_template = data

                    except json.JSONDecodeError as e:
                        result.errors.append(ParseError(
                            current_section_id, fence_start,
                            f"Invalid JSON in rag-config block: {e}"
                        ))

                    in_fence = False
                    fence_lines = []
                    continue

                # Accumulate fence content
                fence_lines.append(stripped)

        # Handle unclosed fence
        if in_fence:
            result.errors.append(ParseError(
                current_section_id, fence_start,
                "Unclosed rag-config block (missing closing ```)"
            ))

        return result

    def _merge_blocks(self, result: ParseResult) -> dict:
        """
        Merge all parsed blocks into a single RAG dict.

        Order:
        1. Start with template block (if found) or VOID_RAG
        2. Apply all config blocks in document order (deep merge)
        3. Stamp metadata (timestamps, version, source)
        """
        # Start with template or void
        if result.template:
            rag = deepcopy(result.template)
        else:
            rag = deepcopy(VOID_RAG)

        # Apply config blocks in order
        for block in result.blocks:
            if block.block_type == "config":
                rag = deep_merge(rag, block.data)
            # template and cold-template blocks are not merged into HOT
            # (template is already the base; cold-template is separate)

        # Stamp metadata
        now_utc = datetime.now(timezone.utc).isoformat()
        if "meta" not in rag:
            rag["meta"] = {}

        rag["meta"]["created_utc"] = now_utc
        rag["meta"]["last_updated_utc"] = now_utc

        if result.spec_version:
            rag["meta"]["policy_version"] = result.spec_version
            if "rag_files" not in rag["meta"]:
                rag["meta"]["rag_files"] = {}
            rag["meta"]["rag_files"]["init_prompt"] = (
                f"INIT_UNIVERSAL_RUNTIME_KERNEL_v{result.spec_version}.md"
            )

        # Ensure required top-level keys exist
        for key, default in VOID_RAG.items():
            if key not in rag:
                rag[key] = deepcopy(default)

        return rag

    # ── Self-version parametrization (FIX-2, K4) ───────────────

    def _postprocess(self, result: ParseResult) -> None:
        """Resolve the single self-version token across HOT + COLD.

        1. Substitute every ``<SPEC_VERSION>`` with the parsed spec version
           in both the merged HOT and the COLD template.
        2. Stamp the COLD ``init_prompt_reference`` (version + filename) from
           that same single source — belt-and-suspenders even if the template
           omits the placeholder.
        3. Fail loud (append a ParseError) if any ``<SPEC_VERSION>`` survives,
           which means the spec header carried no parseable version.
        """
        sv = result.spec_version
        result.merged = self._substitute_version(result.merged, sv)
        if result.cold_template is not None:
            result.cold_template = self._substitute_version(result.cold_template, sv)
            self._stamp_cold(result.cold_template, sv)
        self._check_version_placeholder(result)

    @classmethod
    def _substitute_version(cls, obj: Any, spec_version: str) -> Any:
        """Recursively replace the VERSION_PLACEHOLDER token with spec_version.

        No-op when spec_version is empty (the survivor scan then flags it).
        """
        if not spec_version:
            return obj
        if isinstance(obj, dict):
            return {k: cls._substitute_version(v, spec_version)
                    for k, v in obj.items()}
        if isinstance(obj, list):
            return [cls._substitute_version(v, spec_version) for v in obj]
        if isinstance(obj, str):
            return obj.replace(VERSION_PLACEHOLDER, spec_version)
        return obj

    @staticmethod
    def _stamp_cold(cold: dict, spec_version: str) -> dict:
        """Stamp the COLD init_prompt_reference from the single spec version."""
        if not cold or not spec_version:
            return cold
        ipr = cold.get("init_prompt_reference")
        if isinstance(ipr, dict):
            ipr["version"] = spec_version
            ipr["filename"] = (
                f"INIT_UNIVERSAL_RUNTIME_KERNEL_v{spec_version}.md"
            )
        return cold

    @staticmethod
    def _scan_placeholder(obj: Any, path: str = "$") -> list[str]:
        """Return JSON-ish paths where VERSION_PLACEHOLDER still appears."""
        hits: list[str] = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                hits += SpecParser._scan_placeholder(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                hits += SpecParser._scan_placeholder(v, f"{path}[{i}]")
        elif isinstance(obj, str) and VERSION_PLACEHOLDER in obj:
            hits.append(path)
        return hits

    def _check_version_placeholder(self, result: ParseResult) -> None:
        """Append a (fatal-class) ParseError for each surviving version token."""
        for label, blob in (("HOT", result.merged),
                            ("COLD", result.cold_template)):
            if blob is None:
                continue
            for p in self._scan_placeholder(blob):
                result.errors.append(ParseError(
                    "version", 0,
                    f"Unsubstituted {VERSION_PLACEHOLDER} survived in "
                    f"{label} at {p} (spec_version="
                    f"{result.spec_version or '(none)'!r}) — the spec header "
                    f"has no parseable version token."
                ))

    @staticmethod
    def verify_coherence(rag: dict, cold: Optional[dict] = None,
                         spec_version: str = "") -> list[str]:
        """Deterministic post-init coherence check (powers `rag_kernel verify`).

        Asserts the self-version is consistent across HOT and COLD and that no
        version placeholder leaked through. Returns a list of findings
        (empty = coherent). Zero LLM, zero tokens.
        """
        findings: list[str] = []
        meta = rag.get("meta", {}) if isinstance(rag, dict) else {}
        hot_pv = meta.get("policy_version")
        hot_ip = meta.get("rag_files", {}).get("init_prompt")

        # No version placeholder may survive anywhere in HOT/COLD.
        for label, blob in (("HOT", rag), ("COLD", cold)):
            if blob is None:
                continue
            for p in SpecParser._scan_placeholder(blob):
                findings.append(
                    f"{VERSION_PLACEHOLDER} placeholder unsubstituted in "
                    f"{label} at {p}"
                )

        cold_v = cold_fn = None
        if isinstance(cold, dict):
            ipr = cold.get("init_prompt_reference", {}) or {}
            cold_v = ipr.get("version")
            cold_fn = ipr.get("filename")
            if hot_pv and cold_v and hot_pv != cold_v:
                findings.append(
                    f"COLD↔HOT version drift: HOT policy_version={hot_pv!r} "
                    f"!= COLD init_prompt_reference.version={cold_v!r}"
                )
            if hot_ip and cold_fn and hot_ip != cold_fn:
                findings.append(
                    f"COLD↔HOT init_prompt drift: HOT rag_files.init_prompt="
                    f"{hot_ip!r} != COLD init_prompt_reference.filename="
                    f"{cold_fn!r}"
                )

        if spec_version:
            if hot_pv and hot_pv != spec_version:
                findings.append(
                    f"HOT policy_version={hot_pv!r} != spec version="
                    f"{spec_version!r}"
                )
            if cold_v and cold_v != spec_version:
                findings.append(
                    f"COLD init_prompt_reference.version={cold_v!r} != spec "
                    f"version={spec_version!r}"
                )
        return findings

    def _extract_version(self, lines: list[str]) -> str:
        """Extract spec version from the document title or header."""
        for line in lines[:10]:
            # Match patterns like: v3.1.7, v3.1.8, etc.
            m = re.search(r'v(\d+\.\d+\.\d+)', line)
            if m:
                return m.group(1)
        return ""

    # ── Output ─────────────────────────────────────────────────

    def write_rag(self, rag: dict, filepath: str | Path,
                  atomic: bool = True) -> str:
        """
        Write RAG dict to a JSON file. Returns the file path.

        If atomic=True, writes to a temp file first then renames
        (best-effort atomic write on the platform).
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        content = json.dumps(rag, indent=2, ensure_ascii=False)

        if atomic:
            tmp_path = filepath.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            tmp_path.replace(filepath)
        else:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

        return str(filepath)

    def write_cold(self, cold: dict, filepath: str | Path) -> str:
        """Write COLD template to a JSON file."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        content = json.dumps(cold, indent=2, ensure_ascii=False)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return str(filepath)

    # ── Validation ─────────────────────────────────────────────

    @staticmethod
    def validate_rag(rag: dict) -> list[str]:
        """
        Validate a RAG dict against required structure.
        Returns list of validation errors (empty = valid).
        """
        errors = []

        # Required top-level keys
        required_keys = [
            "meta", "execution_mode", "state_machine_status",
            "policy_flags", "operating_protocol", "pov_mandate",
            "project_context", "priority_actions", "open_tasks",
        ]
        for key in required_keys:
            if key not in rag:
                errors.append(f"Missing required top-level key: {key}")

        # Meta validation
        meta = rag.get("meta", {})
        meta_required = [
            "schema_version", "rag_type", "root_project",
            "root_deliverables", "root_rag", "rag_files"
        ]
        for key in meta_required:
            if key not in meta:
                errors.append(f"Missing required meta key: meta.{key}")

        # Policy flags validation
        pf = rag.get("policy_flags", {})
        pf_required = [
            "atomic_writes_required", "hash_validation_required",
            "load_cold_on_demand_only", "session_close_audit_required",
            "proposal_validation_commit_required"
        ]
        for key in pf_required:
            if key not in pf:
                errors.append(f"Missing required policy flag: policy_flags.{key}")

        # Execution mode validation
        em = rag.get("execution_mode", "")
        if em not in ("autonomous", "enforced", ""):
            errors.append(
                f"Invalid execution_mode: {em!r} "
                f"(expected 'autonomous' or 'enforced')"
            )

        # State machine status validation
        valid_states = {
            "BOOTING", "READY", "INGESTING", "WORKING",
            "CHECKPOINTING", "CLOSING", "RECOVERY", ""
        }
        sms = rag.get("state_machine_status", "")
        if sms not in valid_states:
            errors.append(
                f"Invalid state_machine_status: {sms!r} "
                f"(expected one of {valid_states})"
            )

        return errors

    # ── Report ─────────────────────────────────────────────────

    def report(self, result: ParseResult) -> str:
        """Generate a human-readable parse report."""
        lines = [
            "=" * 60,
            "RAG Runtime Kernel — Spec Parser Report",
            "=" * 60,
            f"Source: {result.source_file or '(string input)'}",
            f"Spec version: {result.spec_version or '(not detected)'}",
            f"Sections found: {len(result.sections_found)}",
            f"rag-config blocks found: {len(result.blocks)}",
            f"Parse errors: {len(result.errors)}",
            "",
        ]

        if result.blocks:
            lines.append("Blocks:")
            for b in result.blocks:
                lines.append(
                    f"  §{b.section_id} ({b.block_type}) "
                    f"lines {b.line_start}-{b.line_end}: "
                    f"{b.section_title}"
                )

        if result.errors:
            lines.append("")
            lines.append("Errors:")
            for e in result.errors:
                lines.append(f"  §{e.section_id} line {e.line}: {e.message}")

        # Validation
        validation_errors = self.validate_rag(result.merged)
        lines.append("")
        if validation_errors:
            lines.append(f"Validation: {len(validation_errors)} issues")
            for ve in validation_errors:
                lines.append(f"  - {ve}")
        else:
            lines.append("Validation: PASSED (all required fields present)")

        # Operating protocol keys
        op = result.merged.get("operating_protocol", {})
        if op:
            lines.append("")
            lines.append(f"Operating protocol keys ({len(op)}):")
            for key in sorted(op.keys()):
                if key.startswith("_"):
                    continue
                val_preview = str(op[key])[:60]
                lines.append(f"  {key}: {val_preview}...")

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)


# ── CLI Entry Point ────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Parse init prompt MD and produce RAG_MASTER.json"
    )
    parser.add_argument(
        "--spec", required=True,
        help="Path to init prompt Markdown file"
    )
    parser.add_argument(
        "--output", default=None,
        help="Output path for RAG_MASTER.json "
             "(default: ./RAG/RAG_MASTER.json)"
    )
    parser.add_argument(
        "--cold-output", default=None,
        help="Output path for RAG_COLD.json "
             "(default: same dir as --output)"
    )
    parser.add_argument(
        "--root-project", default="",
        help="Set root_project path in the output RAG"
    )
    parser.add_argument(
        "--root-deliverables", default="",
        help="Set root_deliverables path in the output RAG"
    )
    parser.add_argument(
        "--root-rag", default="",
        help="Set root_rag path in the output RAG"
    )
    parser.add_argument(
        "--project-name", default="",
        help="Set project_name in the output RAG"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and validate only, don't write files"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    sp = SpecParser(verbose=args.verbose)

    # Parse
    result = sp.parse_file(args.spec)

    # Apply CLI overrides
    if args.root_project:
        result.merged["meta"]["root_project"] = args.root_project
    if args.root_deliverables:
        result.merged["meta"]["root_deliverables"] = args.root_deliverables
    if args.root_rag:
        result.merged["meta"]["root_rag"] = args.root_rag
    if args.project_name:
        result.merged["meta"]["project_name"] = args.project_name

    # Report
    print(sp.report(result))

    if result.errors:
        print(f"\nWARNING: {len(result.errors)} parse errors occurred.")
        print("The parser skipped malformed blocks and continued.")

    # Validate
    validation_errors = sp.validate_rag(result.merged)
    if validation_errors:
        print(f"\nWARNING: {len(validation_errors)} validation issues.")

    # Write
    if not args.dry_run:
        output = args.output or os.path.join("RAG", "RAG_MASTER.json")
        written = sp.write_rag(result.merged, output)
        print(f"\nRAG_MASTER.json written to: {written}")

        # Write COLD if template found
        if result.cold_template:
            cold_output = args.cold_output or os.path.join(
                os.path.dirname(output), "RAG_COLD.json"
            )
            cold_written = sp.write_cold(result.cold_template, cold_output)
            print(f"RAG_COLD.json written to: {cold_written}")

        print("\nDone. Zero tokens consumed.")
    else:
        print("\n[DRY RUN] No files written.")

    sys.exit(0 if not validation_errors else 1)


if __name__ == "__main__":
    main()
