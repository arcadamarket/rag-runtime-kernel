"""RAG Runtime Kernel — OS-level runtime bridge for LLM memory persistence.

Zero external dependencies. Python 3.10+ standard library only.

@rag-kernel-manifest
{
  "package": "rag_kernel",
  "version": "0.4.7",
  "description": "OS-level runtime bridge for LLM memory persistence",
  "spec_version": "3.2.2",
  "python_requires": ">=3.10",
  "dependencies": "stdlib-only",
  "modules": {
    "state_machine": "Deterministic FSM: BOOTING→READY→WORKING→CHECKPOINTING→CLOSING",
    "persistence": "Atomic writes, WAL (append-only JSONL + fsync), SHA-256, backup rotation",
    "schemas": "Pure-data validation for proposals, events, HOT/COLD structures",
    "cold_manager": "Lazy-loading COLD archive with token budgeting and eviction",
    "concurrency": "File-based mutex (.rag_kernel.lock) + split-brain detection",
    "api": "HTTP JSON API server (port 7437)",
    "mcp_transport": "MCP stdio transport for Claude Desktop (JSON-RPC 2.0)",
    "spec_parser": "Deterministic MD→RAG parser (zero tokens, zero LLM); substitutes a single <SPEC_VERSION> self-version token across HOT+COLD and stamps the COLD init_prompt_reference from the spec's own version, fail-loud on any survivor (FIX-2, K4); also substitutes the build-deterministic <ISO> placeholder and strips _-prefixed template keys from operating_protocol so a fresh deploy is born clean (FIX-3, K3+K5)",
    "session_logger": "Structured JSONL session logger — universal observability",
    "conflict_engine": "Rule-based conflict auto-categorization with suggested resolutions",
    "generated_guards": "TLA+-derived transition table + per-action enabling guards (FV-PHASE4 enforced structural source)",
    "guardgen": "Deterministic TLA+ → Python transition-guard generator (build-time, zero-LLM)",
    "context_policy": "Deterministic kernel-enforced context-truncation policy: per-region token accounting, pinned/evictable ordering (HOT never evicted), checkpoint/evict/halt actions (M-009)",
    "graph_orchestrator": "Deterministic DAG core + execution engine: fail-loud build, topological order + deterministic-levels scheduling, guarded node-status lifecycle, propose→validate→commit execution with checkpoint-per-node and opt-in transactional rollback under a single-writer file-mutex (GRAPH-ORCH v4.0)",
    "agent_supervisor": "Observable spawn/monitor/collect layer over pure off-process node work: live per-worker PID + lifecycle state + exit code as an AgentView, owning no authoritative state (GRAPH-ORCH v4.0, increment 7)",
    "drift_control": "Canonical project-state status enum + lifecycle state machine (OPEN→IN_PROGRESS→{RESOLVED|DEFERRED|SUPERSEDED|DISCARDED}, DEFERRED↔OPEN): pure, fail-loud item-lifecycle core (DRIFT-ELIM increment 1)",
    "drift_store": "Deterministic, atomic mutation API over the RAG tracked_items array: guarded transitions, atomic persistence (tmp→verify→.bak→rename), one-time backlog migration — the canonical store every status render projects from (DRIFT-ELIM increment 2)",
    "drift_render": "Deterministic, idempotent renderers projecting the canonical tracked_items array into the legacy open_tasks / deferred_items arrays, the Rule 12 status-report backlog, and the ERROR_LOG backlog summary — makes tracked_items the sole authority, every status mention a derived render (DRIFT-ELIM increment 4)",
    "drift_audit": "Fail-loud session-boundary auditor: asserts the rendered legacy open_tasks/deferred_items match the canonical tracked_items array (E-040 regression), supersede refs resolve, notes don't contradict status (INS-038), no Cowork-memory side stores exist in the project root (Rule 13), current_status version/HEAD match the live authorities (E-043), and the FIX-1 integrity family — WAL monotonicity, RAG↔.bak parity, unsubstituted-placeholder scan, leaked template-key scan, COLD↔HOT spec-version coherence, non-empty written_by_session, session-id coherence (K1+K2)"
  },
  "cli_commands": {
    "init": "python -m rag_kernel init (--spec <path.md> | --allow-void) [--output RAG/] [--dry-run]",
    "health": "python -m rag_kernel health [--path .]",
    "serve": "python -m rag_kernel serve --project <path> [--port 7437]",
    "mcp": "python -m rag_kernel mcp --project <path>",
    "configure": "python -m rag_kernel configure --rag <path> --context <path>",
    "verify": "python -m rag_kernel verify [--rag <path>] [--cold <path>] [--spec <path.md>] — deterministic post-init HOT↔COLD self-version coherence gate (FIX-2)"
  },
  "invocation_rules": {
    "MUST_USE_KERNEL": [
      "State transitions (boot, close, recovery)",
      "Proposal validation and commit",
      "Checkpoint writes (atomic + WAL + backup)",
      "COLD partition load/evict",
      "Split-brain detection",
      "RAG initialization from spec"
    ],
    "DIRECT_IO_OK": [
      "Simple RAG reads (status checks, field lookups)",
      "Error log appends",
      "TODO plan updates",
      "Non-RAG file operations"
    ]
  }
}
"""

__version__ = "0.4.8"


# ── Capability Discovery ──────────────────────────────────────

import importlib
import json
from typing import Any

# Module-count convention (closes INS-003 / INS-019):
#   * "19 capability modules" == the manifest `modules` dict above — the
#     functional units, excluding the __init__ package marker and the
#     __main__ CLI entry point. (M-009 added context_policy as the 13th;
#     GRAPH-ORCH increment 5 registered graph_orchestrator as the 14th;
#     GRAPH-ORCH increment 7 registered agent_supervisor as the 15th;
#     DRIFT-ELIM increment 3 registered drift_control as the 16th and
#     drift_store as the 17th; DRIFT-ELIM increment 4 registered
#     drift_render as the 18th; DRIFT-ELIM increment 5 registered
#     drift_audit as the 19th.)
#   * _KERNEL_MODULES below additionally includes __main__ as a final import
#     target so discover()/cmd_health verify the CLI imports cleanly too
#     (20 import targets == 19 capability modules + __main__).
#   The __init__ package marker is never counted (it IS the package).
_KERNEL_MODULES = [
    "rag_kernel.state_machine",
    "rag_kernel.persistence",
    "rag_kernel.schemas",
    "rag_kernel.cold_manager",
    "rag_kernel.concurrency",
    "rag_kernel.api",
    "rag_kernel.mcp_transport",
    "rag_kernel.spec_parser",
    "rag_kernel.session_logger",
    "rag_kernel.conflict_engine",
    "rag_kernel.generated_guards",
    "rag_kernel.guardgen",
    "rag_kernel.context_policy",
    "rag_kernel.graph_orchestrator",
    "rag_kernel.agent_supervisor",
    "rag_kernel.drift_control",
    "rag_kernel.drift_store",
    "rag_kernel.drift_render",
    "rag_kernel.drift_audit",
    "rag_kernel.__main__",
]


def _extract_manifest(module_doc: str) -> dict[str, Any] | None:
    """Extract @rag-kernel-manifest JSON from a module docstring.

    Uses brace-counting to handle nested JSON objects correctly.
    """
    if not module_doc:
        return None
    marker = "@rag-kernel-manifest"
    idx = module_doc.find(marker)
    if idx == -1:
        return None

    # Find the first '{' after the marker
    brace_start = module_doc.find("{", idx + len(marker))
    if brace_start == -1:
        return None

    # Count braces to find matching close
    depth = 0
    for i in range(brace_start, len(module_doc)):
        if module_doc[i] == "{":
            depth += 1
        elif module_doc[i] == "}":
            depth -= 1
            if depth == 0:
                raw = module_doc[brace_start:i + 1]
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return None
    return None


def discover() -> dict[str, Any]:
    """
    Discover all rag_kernel capabilities by scanning module manifests.

    Returns a registry dict with:
    - package: package-level manifest (from __init__.py)
    - modules: dict of module_name -> manifest
    - capabilities: flat list of capability strings
    - critical: list of modules where never_bypass=true
    - cli_commands: available CLI commands

    Usage at session boot:
        import rag_kernel
        registry = rag_kernel.discover()
        print(json.dumps(registry, indent=2))
    """
    # Package-level manifest
    pkg_manifest = _extract_manifest(__doc__) or {}

    modules: dict[str, dict] = {}
    capabilities: list[str] = []
    critical: list[str] = []

    for mod_name in _KERNEL_MODULES:
        try:
            mod = importlib.import_module(mod_name)
            manifest = _extract_manifest(mod.__doc__)
            if manifest:
                short_name = mod_name.split(".")[-1]
                modules[short_name] = manifest
                if "capability" in manifest:
                    capabilities.append(manifest["capability"])
                if manifest.get("never_bypass"):
                    critical.append(short_name)
        except ImportError:
            continue

    return {
        "package": pkg_manifest,
        "modules": modules,
        "capabilities": capabilities,
        "critical_modules": critical,
        "cli_commands": pkg_manifest.get("cli_commands", {}),
        "invocation_rules": pkg_manifest.get("invocation_rules", {}),
    }
