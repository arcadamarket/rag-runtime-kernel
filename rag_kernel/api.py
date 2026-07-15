"""HTTP API router for the RAG Runtime Kernel.

Stdlib-only HTTP server exposing the kernel's functionality via JSON API.
All endpoints accept/return JSON. Default port: 7437 ("R-G-K" on phone keypad).

Integrates:
- StateMachine: session state transitions
- Persistence: atomic writes, WAL, hash verification
- ColdManager: lazy COLD partition access
- ProjectLock: concurrency guard

Design doc reference: v3.2_ARCHITECTURE_DESIGN.md §5
Satisfies: M-023 (core HTTP API)

@rag-kernel-manifest
{
  "module": "rag_kernel.api",
  "capability": "http_api",
  "description": "HTTP JSON API server — primary interface for GPT Web and direct access",
  "exports": ["KernelApp", "create_server", "DEFAULT_PORT"],
  "endpoints": [
    "POST /boot", "GET /status", "GET /hot", "GET /cold/:partition",
    "POST /propose", "POST /commit", "POST /reject",
    "POST /checkpoint", "GET /wal", "POST /recover", "POST /close",
    "GET /config/pov_mode", "PATCH /config/pov_mode", "POST /config/pov_mode/check",
    "POST /conflicts/add", "POST /conflicts/resolve", "GET /conflicts/summary"
  ],
  "use_when": "Running kernel as a persistent HTTP service",
  "never_bypass": false
}
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from rag_kernel.cold_manager import (
    ColdManager,
    ColdFileError,
    PartitionNotFoundError,
    estimate_tokens,
)
from rag_kernel.context_policy import (
    MemoryRegion,
    PolicyAction,
    RegionAccount,
    TokenLedger,
    TruncationPolicy,
    evaluate as evaluate_context_policy,
    default_policy as default_context_policy,
)
from rag_kernel.concurrency import (
    ProjectLock,
    LockConflictError,
    detect_split_brain,
)
from rag_kernel.conflict_engine import (
    ConflictEngine,
    validate_conflict_payload,
)
from rag_kernel.persistence import (
    WAL,
    atomic_write_json,
    compute_hash,
    verify_hashes,
    DeltaCheckpointManager,
)
from rag_kernel.schemas import VALID_POV_MODES, validate_pov_mode, should_auto_escalate
from rag_kernel.session_logger import SessionLogger, EventCategory
from rag_kernel.state_machine import State, StateMachine


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PORT = 7437
HOT_FILENAME = "RAG_MASTER.json"
COLD_FILENAME = "RAG_COLD.json"
WAL_FILENAME = "WAL.jsonl"


def generate_session_id() -> str:
    """Canonical auto session id for an unattended boot (FIX-3, K7).

    Shape: ``S`` + a non-negative integer (millisecond epoch). This satisfies the
    drift_audit session-id coherence invariant (``^S<non-negative-int>``) —
    unlike the old ``S-{pid}-{epoch}`` form, whose ``S-`` prefix the auditor
    flags as a malformed/negative id (the eBay ``S-12488-...`` defect). A
    human-assigned ``SNN`` passed explicitly always takes precedence.
    """
    return f"S{int(time.time() * 1000)}"


# ---------------------------------------------------------------------------
# Kernel Application (ties all components together)
# ---------------------------------------------------------------------------

class KernelApp:
    """Central application object. Owns all kernel subsystems.

    This is the single integration point. The HTTP handler and
    MCP transport both delegate to KernelApp methods.
    """

    def __init__(
        self,
        project_dir: Path,
        session_id: Optional[str] = None,
    ) -> None:
        self.project_dir = project_dir
        self.session_id = session_id or generate_session_id()

        # Paths
        self.hot_path = project_dir / HOT_FILENAME
        self.cold_path = project_dir / COLD_FILENAME
        self.wal_path = project_dir / WAL_FILENAME

        # Subsystems (initialized but not booted)
        self.state_machine = StateMachine()
        self.lock = ProjectLock(project_dir)
        self.wal = WAL(self.wal_path)
        self.cold = ColdManager(self.cold_path)

        # HOT data (loaded on boot)
        self._hot: dict = {}

        # Proposal staging area
        self._proposals: dict[str, dict] = {}
        self._proposal_seq = 0

        # Delta checkpoint manager (ENH-006)
        self._delta_mgr = DeltaCheckpointManager(max_deltas=10)

        # Session logger (ENH-007) — automatic observability
        self.logger = SessionLogger(
            session_id=self.session_id,
            log_dir=project_dir,
        )

        # Conflict engine (ENH-005) — auto-categorization
        self.conflicts = ConflictEngine()

    # -- Boot ---------------------------------------------------------------

    def boot(self) -> dict:
        """Initialize the kernel session.

        Sequence:
        1. Acquire project lock
        2. Load HOT (RAG_MASTER.json)
        3. Verify hashes
        4. Open WAL
        5. Check for split-brain
        6. Transition to READY (or RECOVERY)

        Returns status dict.
        """
        # 0. Open session logger
        self.logger.open()
        self.logger.info(f"Booting kernel session {self.session_id}", category=EventCategory.LIFECYCLE)

        # 1. Lock
        if not self.lock.acquire(self.session_id):
            info = self.lock.read_lock()
            self.state_machine.transition(State.RECOVERY)
            self.logger.error(
                "Boot failed: project locked by another session",
                category=EventCategory.LIFECYCLE,
            )
            return {
                "status": "LOCKED",
                "state": self.state_machine.current.value,
                "locked_by": info.to_dict() if info else None,
            }

        # 2. Open WAL early so it's available in all code paths
        self.wal.open()

        # 3. Load HOT
        if self.hot_path.exists():
            try:
                raw = self.hot_path.read_text(encoding="utf-8")
                self._hot = json.loads(raw)
            except (json.JSONDecodeError, OSError) as e:
                self.state_machine.transition(State.RECOVERY)
                self.logger.error(
                    "HOT load failed",
                    category=EventCategory.IO,
                    exc=e,
                )
                return {
                    "status": "HOT_LOAD_FAILED",
                    "state": State.RECOVERY.value,
                    "error": str(e),
                }
        else:
            self._hot = {"meta": {"session_id": self.session_id}}

        # 4. Verify hashes
        hash_errors = verify_hashes(self._hot)

        # 5. Split-brain check
        wal_entries = [e.to_dict() for e in self.wal.replay()]
        split = detect_split_brain(self._hot, wal_entries)

        if split or hash_errors:
            self.state_machine.transition(State.RECOVERY)
            self.wal.append(
                "BOOT_RECOVERY",
                session_id=self.session_id,
                hash_errors=hash_errors,
                split_brain=str(split) if split else None,
            )
            self.logger.error(
                "Boot entered RECOVERY: integrity issues detected",
                category=EventCategory.RECOVERY,
            )
            return {
                "status": "RECOVERY",
                "state": State.RECOVERY.value,
                "hash_errors": hash_errors,
                "split_brain": str(split) if split else None,
            }

        # 6. Transition to READY (no errors)
        self.state_machine.transition(State.READY)
        self.wal.append(
            "BOOT_COMPLETE",
            session_id=self.session_id,
        )

        # Update HOT session info
        if "meta" not in self._hot:
            self._hot["meta"] = {}
        self._hot["meta"]["session_id"] = self.session_id

        # Set delta checkpoint base snapshot
        self._delta_mgr.set_base(self._hot, self.wal.seq)

        self.logger.state_transition("BOOTING", "READY", trigger="boot")

        return {
            "status": "OK",
            "state": State.READY.value,
            "session_id": self.session_id,
        }

    # -- Status -------------------------------------------------------------

    def status(self) -> dict:
        """Return current kernel status."""
        return {
            "state": self.state_machine.current.value,
            "session_id": self.session_id,
            "seq": self.state_machine.seq,
            "wal_seq": self.wal.seq,
            "is_terminal": self.state_machine.is_terminal,
            "available_transitions": [
                s.value for s in self.state_machine.available_transitions
            ],
            "cold_summary": self.cold.summary(),
            "lock_held": self.lock.is_locked,
            "pov_mode": self.get_pov_mode(),
            "delta_checkpoint": {
                "deltas_since_full": self._delta_mgr.delta_count,
                "max_deltas": self._delta_mgr.max_deltas,
                "needs_full": self._delta_mgr.needs_full,
            },
        }

    # -- POV mode -----------------------------------------------------------

    def get_pov_mode(self) -> str:
        """Return current POV mode from HOT."""
        mandate = self._hot.get("pov_mandate", {})
        return mandate.get("mode", "strict")

    def set_pov_mode(self, mode: str) -> dict:
        """Set POV mode. Validates against VALID_POV_MODES.

        Returns status dict with success/error.
        """
        valid, errors = validate_pov_mode(mode)
        if not valid:
            return {"updated": False, "errors": errors}

        # Ensure pov_mandate exists
        if "pov_mandate" not in self._hot:
            self._hot["pov_mandate"] = {"count": 0, "mode": "strict"}

        old_mode = self._hot["pov_mandate"].get("mode", "strict")
        self._hot["pov_mandate"]["mode"] = mode

        # Atomic write
        atomic_write_json(self.hot_path, self._hot)

        # WAL
        self.wal.append(
            "PROPOSAL_COMMITTED",
            action="update_pov_mode",
            session_id=self.session_id,
            old_mode=old_mode,
            new_mode=mode,
        )

        return {
            "updated": True,
            "old_mode": old_mode,
            "new_mode": mode,
        }

    def check_auto_escalate(self, operation_type: str) -> dict:
        """Check if an operation requires auto-escalation to strict POV mode.

        Returns dict with escalated flag and the effective mode.
        """
        current_mode = self.get_pov_mode()
        escalated = should_auto_escalate(operation_type)

        return {
            "configured_mode": current_mode,
            "effective_mode": "strict" if escalated else current_mode,
            "escalated": escalated,
            "operation_type": operation_type,
        }

    # -- Conflict engine (ENH-005) -----------------------------------------

    def add_conflict(self, conflict_data: dict) -> dict:
        """Add a conflict via the engine. Auto-classifies and suggests resolution.

        Required payload fields: source_a, source_b, difference.
        Optional: source_a_tier, source_b_tier, source_a_value,
                  source_b_value, field_name.

        Returns the classified conflict record as a dict.
        """
        valid, errors = validate_conflict_payload(conflict_data)
        if not valid:
            return {"added": False, "errors": errors}

        record = self.conflicts.add_conflict(
            source_a=conflict_data["source_a"],
            source_b=conflict_data["source_b"],
            difference=conflict_data["difference"],
            source_a_tier=conflict_data.get("source_a_tier"),
            source_b_tier=conflict_data.get("source_b_tier"),
            source_a_value=conflict_data.get("source_a_value"),
            source_b_value=conflict_data.get("source_b_value"),
            field_name=conflict_data.get("field_name"),
        )

        # Update HOT active_conflicts_count
        self._hot["active_conflicts_count"] = self.conflicts.active_count

        # Atomic write to persist count
        atomic_write_json(self.hot_path, self._hot)

        # WAL
        self.wal.append(
            "PROPOSAL_COMMITTED",
            action="add_conflict",
            session_id=self.session_id,
            conflict_id=record.conflict_id,
            category=record.category.value,
            auto_resolved=record.auto_resolved,
        )

        self.logger.info(
            f"Conflict {record.conflict_id} added: "
            f"category={record.category.value}, "
            f"auto_resolved={record.auto_resolved}",
            category=EventCategory.LIFECYCLE,
        )

        return {
            "added": True,
            "conflict": record.to_dict(),
            "active_count": self.conflicts.active_count,
        }

    def resolve_conflict(self, conflict_id: str, resolution: str, resolver: str = "user") -> dict:
        """Manually resolve an active conflict.

        Returns the resolved record or error.
        """
        record = self.conflicts.resolve_conflict(conflict_id, resolution, resolver)
        if record is None:
            return {
                "resolved": False,
                "error": f"Conflict '{conflict_id}' not found in active conflicts.",
            }

        # Update HOT
        self._hot["active_conflicts_count"] = self.conflicts.active_count
        atomic_write_json(self.hot_path, self._hot)

        # WAL
        self.wal.append(
            "PROPOSAL_COMMITTED",
            action="resolve_conflict",
            session_id=self.session_id,
            conflict_id=record.conflict_id,
            resolver=resolver,
        )

        self.logger.info(
            f"Conflict {record.conflict_id} resolved by {resolver}",
            category=EventCategory.LIFECYCLE,
        )

        return {
            "resolved": True,
            "conflict": record.to_dict(),
            "active_count": self.conflicts.active_count,
        }

    def get_conflict_summary(self) -> dict:
        """Return a summary of all conflicts by category and status."""
        return self.conflicts.summary()

    # -- HOT ----------------------------------------------------------------

    def get_hot(self) -> dict:
        """Return current HOT data."""
        return dict(self._hot)

    # -- COLD ---------------------------------------------------------------

    def get_cold(self, partition: Optional[str] = None) -> Any:
        """Return COLD data (full or specific partition)."""
        if partition:
            return self.cold.get(partition)
        return self.cold.get_all()

    # -- Propose / Commit / Reject ------------------------------------------

    # -- Tier gate validation ------------------------------------------------

    # Valid web access tiers and their numeric rank (lower = preferred)
    _WEB_TIERS = {"script": 1, "fetch": 2, "search": 3}

    def validate_web_tier(self, proposal: dict) -> list[str]:
        """Validate web access tier compliance (INS-012, kernel-enforced).

        If the proposal declares a ``web_tier`` field, verify:
        1. It is a recognized tier name.
        2. The ``tier_justification`` field explains why a higher tier is used.
        3. Using Tier 3 (search) when a Tier 1 script exists is a violation.

        Returns a list of error strings (empty = valid).
        """
        errors: list[str] = []
        tier = proposal.get("web_tier")
        if tier is None:
            return errors  # Not a web-access proposal — skip

        tier_lower = str(tier).lower()
        if tier_lower not in self._WEB_TIERS:
            errors.append(
                f"Unknown web_tier '{tier}'. "
                f"Valid tiers: {', '.join(self._WEB_TIERS)}."
            )
            return errors

        rank = self._WEB_TIERS[tier_lower]

        # Tier > 1 requires justification
        if rank > 1 and not proposal.get("tier_justification"):
            errors.append(
                f"web_tier '{tier_lower}' (rank {rank}) requires a "
                f"'tier_justification' field explaining why Tier 1 (script) "
                f"is not applicable."
            )

        # Check for existing scraper scripts in the project
        if rank >= 3:
            scripts_dir = self.project_dir.parent  # project root (RAG is subdir)
            has_scripts = any(
                f.endswith(".py")
                for f in os.listdir(scripts_dir)
                if "scrape" in f.lower() or "fetch" in f.lower()
            ) if scripts_dir.exists() else False

            if has_scripts:
                errors.append(
                    f"Tier violation: web_tier 'search' used but scraper/fetcher "
                    f"scripts exist in project. Use Tier 1 (script) instead."
                )

        return errors

    # -- Echo-back validation -----------------------------------------------

    @staticmethod
    def validate_echo_back(proposal: dict) -> list[str]:
        """Validate echo-back compliance for user-input proposals (INS-015).

        If the proposal declares ``user_input_consumed: true``, it MUST also
        include ``echo_value`` with the value that was echoed back to the user
        for confirmation.

        Returns a list of error strings (empty = valid).
        """
        errors: list[str] = []
        if proposal.get("user_input_consumed") and not proposal.get("echo_value"):
            errors.append(
                "Echo-back violation: proposal consumes user input "
                "(user_input_consumed=true) but does not include 'echo_value'. "
                "The received value must be echoed to the user before acting on it."
            )
        return errors

    # -- Secrets ingest guard (SECRETS-INGEST-GUARD, P1/G2) ------------------

    def _secret_ingest_candidates(self) -> dict[str, tuple[str, str]]:
        """Declared-secret VALUES for the ingest guard, memoized per session.

        Reuses ``drift_audit.collect_declared_secret_values`` — the SAME source of
        truth the audit-time ``check_secrets_boundary`` uses — so the proactive
        (ingest) and reactive (audit) boundaries can never disagree on what a secret
        is. The project root is ``project_dir.parent`` (RAG is a subdir), matching the
        ``root`` the close-time audit passes. Scanned once per KernelApp: declared-
        secret files are effectively static within a session, and the bounded scan
        should not run on every proposal. Failures degrade OPEN-safe to ``{}`` — the
        audit-time guard remains the fail-loud backstop.
        """
        cache = getattr(self, "_secret_candidates_cache", None)
        if cache is None:
            try:
                from rag_kernel import drift_audit  # lazy: avoid import cycle
                cache = drift_audit.collect_declared_secret_values(
                    self.get_hot(), self.project_dir.parent
                )
            except Exception:
                cache = {}
            self._secret_candidates_cache = cache
        return cache

    def validate_secrets_ingest(self, proposal: dict) -> list[str]:
        """Refuse a proposal whose payload carries a declared-secret VALUE (P1/G2).

        The proactive half of KA-SECRETS-BOUNDARY: intercepts the INGESTING path
        (``propose``) so a live credential can NEVER be committed into HOT / loaded
        into context — the audit-time ``check_secrets_boundary`` only catches a leak
        after the fact. Serializes the proposal payload and tests each declared-secret
        value for verbatim membership. Redaction-safe by construction: an error names
        only the source location + a ``sha256:<12>`` fingerprint, never the secret, so
        the guard cannot become the exfiltration path it defends against.

        Returns a list of error strings (empty = clean).
        """
        errors: list[str] = []
        payload = proposal.get("payload")
        if payload is None:
            return errors
        candidates = self._secret_ingest_candidates()
        if not candidates:
            return errors
        try:
            hay = json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            hay = repr(payload)
        for val, (rel_posix, key_label) in candidates.items():
            if val and val in hay:
                fp = hashlib.sha256(val.encode("utf-8")).hexdigest()[:12]
                errors.append(
                    f"Secrets-ingest violation: proposal payload carries a declared-secret "
                    f"value from {rel_posix} (key {key_label!r}, sha256:{fp}). Secret values "
                    f"must never be ingested into the RAG (KA-SECRETS-BOUNDARY); reference the "
                    f"secret by path, not by value."
                )
        return errors

    def propose(self, proposal: dict) -> dict:
        """Submit a mutation proposal for validation.

        The proposal must include:
        - action: str (e.g., "update_status", "add_session")
        - payload: dict (the data to write)

        Optional enforcement fields:
        - web_tier: str — declares which web access tier is being used
        - tier_justification: str — why a higher tier is necessary
        - user_input_consumed: bool — whether this proposal uses user input
        - echo_value: str — the value echoed back to the user

        Returns proposal_id and validation result.
        """
        self._proposal_seq += 1
        proposal_id = f"{self.session_id}-P{self._proposal_seq}"

        # Validate structure
        errors = []
        if "action" not in proposal:
            errors.append("Missing 'action' field")
        if "payload" not in proposal:
            errors.append("Missing 'payload' field")

        # Validate state allows mutation
        current = self.state_machine.current
        if current not in (State.READY, State.WORKING, State.INGESTING):
            errors.append(
                f"Cannot propose in state {current.value}. "
                f"Must be READY, WORKING, or INGESTING."
            )

        # Tier gate enforcement (INS-012)
        errors.extend(self.validate_web_tier(proposal))

        # Echo-back enforcement (INS-015)
        errors.extend(self.validate_echo_back(proposal))

        # Secrets ingest guard (SECRETS-INGEST-GUARD, P1/G2) — refuse a payload
        # carrying a declared-secret value before it can reach HOT/context.
        errors.extend(self.validate_secrets_ingest(proposal))

        # Conflict payload validation (ENH-005)
        if proposal.get("action") == "add_conflict" and "payload" in proposal:
            valid, conflict_errors = validate_conflict_payload(proposal["payload"])
            if not valid:
                errors.extend(conflict_errors)

        result = {
            "proposal_id": proposal_id,
            "valid": len(errors) == 0,
            "errors": errors,
        }

        if not errors:
            self._proposals[proposal_id] = {
                "proposal_id": proposal_id,
                "action": proposal["action"],
                "payload": proposal["payload"],
                "state_before": current.value,
                "created_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
            }
            self.wal.append(
                "PROPOSAL_CREATED",
                proposal_id=proposal_id,
                action=proposal["action"],
                session_id=self.session_id,
            )

        return result

    def commit(self, proposal_id: str) -> dict:
        """Commit a validated proposal.

        Applies the payload to HOT, writes atomically, updates WAL.
        """
        if proposal_id not in self._proposals:
            return {
                "committed": False,
                "error": f"Proposal '{proposal_id}' not found or already committed.",
            }

        proposal = self._proposals.pop(proposal_id)
        payload = proposal["payload"]

        # M-009: truncate_context is a context operation, not a HOT mutation.
        # Route it through the kernel-enforced policy rather than merging the
        # payload into HOT. This is the proposal -> validate -> commit path
        # for context truncation (LLM proposes, system decides).
        if proposal["action"] == "truncate_context":
            self.wal.append(
                "PROPOSAL_COMMITTED",
                proposal_id=proposal_id,
                action="truncate_context",
                session_id=self.session_id,
            )
            pol = None
            if isinstance(payload.get("max_budget"), int):
                pol = default_context_policy(payload["max_budget"])
            enforcement = self.enforce_context_policy(
                conversation_tokens=payload.get("conversation_tokens", 0),
                policy=pol,
                candidate_scores=payload.get("candidate_scores"),
            )
            return {
                "committed": True,
                "proposal_id": proposal_id,
                "truncation": enforcement,
            }

        # Transition to WORKING if not already
        if self.state_machine.current == State.READY:
            self.state_machine.transition(State.WORKING)

        # Apply payload to HOT (shallow merge at top level)
        for key, value in payload.items():
            self._hot[key] = value

        # Recompute hash
        state_hash = compute_hash(self._hot)
        if "meta" not in self._hot:
            self._hot["meta"] = {}
        self._hot["meta"]["state_hash"] = state_hash

        # Atomic write
        atomic_write_json(self.hot_path, self._hot)

        # WAL
        self.wal.append(
            "PROPOSAL_COMMITTED",
            proposal_id=proposal_id,
            action=proposal["action"],
            session_id=self.session_id,
        )

        self.logger.rag_mutation(
            target="HOT",
            mutation_type="proposal_commit",
            proposal_id=proposal_id,
            action=proposal["action"],
        )

        return {
            "committed": True,
            "proposal_id": proposal_id,
            "state_hash": state_hash,
        }

    def reject(self, proposal_id: str) -> dict:
        """Reject a proposal. Removes it from staging."""
        if proposal_id not in self._proposals:
            return {
                "rejected": False,
                "error": f"Proposal '{proposal_id}' not found.",
            }

        self._proposals.pop(proposal_id)
        self.wal.append(
            "PROPOSAL_REJECTED",
            proposal_id=proposal_id,
            session_id=self.session_id,
        )
        return {"rejected": True, "proposal_id": proposal_id}

    # -- Checkpoint ---------------------------------------------------------

    def checkpoint(self, force_full: bool = False, is_closing: bool = False) -> dict:
        """Save current state. Supports full and delta checkpoints (ENH-006).

        Full checkpoint: writes entire RAG_MASTER.json atomically.
        Delta checkpoint: writes only changed fields to WAL.

        Full is triggered when:
        - force_full=True
        - is_closing=True (session close always gets full)
        - No base snapshot exists (first checkpoint)
        - Delta count >= max_deltas threshold

        Otherwise a delta checkpoint is written.
        """
        if not self.state_machine.transition(State.CHECKPOINTING):
            return {
                "checkpointed": False,
                "error": f"Cannot checkpoint from {self.state_machine.current.value}",
            }

        # Recompute hash
        state_hash = compute_hash(self._hot)
        if "meta" not in self._hot:
            self._hot["meta"] = {}
        self._hot["meta"]["state_hash"] = state_hash
        self._hot["meta"]["last_checkpoint_seq"] = self.wal.seq
        self._hot["meta"]["session_id"] = self.session_id
        # FIX-3 (K7): stamp the canonical session id as written_by_session on
        # every checkpoint (covers close, which routes through here). The eBay
        # deploy left written_by_session empty because the runtime persisted
        # only meta.session_id — breaking the RAG's session lineage.
        self._hot["meta"]["written_by_session"] = self.session_id

        do_full = (
            force_full
            or self._delta_mgr.should_full_checkpoint(is_closing=is_closing)
        )

        if do_full:
            # Full checkpoint: atomic write. FIX-4 (K6): a full checkpoint — which
            # the session close always routes through — refreshes .bak to a
            # byte-identical parity-mirror of the just-committed HOT, so the backup
            # restores the exact known-good close state (operator-settled
            # parity-mirror, not rollback-prev). This is the enforce half of the
            # K6 contract whose audit half is drift_audit.check_bak_parity.
            # FIX-7 (T1): live pre-write side-store guard — session-close checkpoint
            # refuses to commit while a parallel rule/state store is live (Rule 13).
            atomic_write_json(
                self.hot_path, self._hot, mirror_bak=True, guard_side_stores=True
            )

            self.wal.append(
                "CHECKPOINT",
                checkpoint_type="full",
                session_id=self.session_id,
                seq=self.wal.seq,
            )

            # Reset delta manager with new base and mark full done
            self._delta_mgr.set_base(self._hot, self.wal.seq)
            self._delta_mgr._first_full_done = True

            # Back to READY
            self.state_machine.transition(State.READY)

            self.logger.checkpoint("full", seq=self.wal.seq)

            return {
                "checkpointed": True,
                "checkpoint_type": "full",
                "state_hash": state_hash,
                "wal_seq": self.wal.seq,
            }
        else:
            # Delta checkpoint: compute diff and write to WAL
            delta = self._delta_mgr.compute_delta(self._hot)

            if delta is None:
                # No changes — skip checkpoint, back to READY
                self.state_machine.transition(State.READY)
                return {
                    "checkpointed": False,
                    "checkpoint_type": "delta",
                    "reason": "no_changes",
                    "wal_seq": self.wal.seq,
                }

            self.wal.append(
                "CHECKPOINT",
                checkpoint_type="delta",
                session_id=self.session_id,
                seq=self.wal.seq,
                delta=delta.to_dict(),
            )

            # Back to READY
            self.state_machine.transition(State.READY)

            self.logger.checkpoint(
                "delta", seq=self.wal.seq,
                delta_count=delta.delta_count,
                deltas_since_full=self._delta_mgr.delta_count,
            )

            return {
                "checkpointed": True,
                "checkpoint_type": "delta",
                "delta_count": delta.delta_count,
                "deltas_since_full": self._delta_mgr.delta_count,
                "state_hash": state_hash,
                "wal_seq": self.wal.seq,
            }

    # -- Context truncation policy (M-009) ----------------------------------

    def build_token_ledger(self, conversation_tokens: int = 0) -> TokenLedger:
        """Snapshot per-region token consumption into a TokenLedger.

        HOT is pinned (source of truth). COLD is subdivided by loaded
        partition so the eviction plan can name exactly what to free. WAL
        tokens are estimated from on-disk size with the same 4-chars/token
        heuristic used elsewhere in the kernel.
        """
        hot_tokens = estimate_tokens(self._hot)

        cold_items = tuple(
            (name, self.cold.token_estimate(name))
            for name in self.cold.loaded_partitions
        )
        cold_tokens = sum(t for _, t in cold_items)

        try:
            wal_bytes = self.wal_path.stat().st_size if self.wal_path.exists() else 0
        except OSError:
            wal_bytes = 0
        wal_tokens = wal_bytes // 4

        return TokenLedger(accounts=(
            RegionAccount(MemoryRegion.HOT, hot_tokens, pinned=True),
            RegionAccount(MemoryRegion.COLD, cold_tokens, items=cold_items),
            RegionAccount(MemoryRegion.WAL, wal_tokens),
            RegionAccount(
                MemoryRegion.CONVERSATION, max(0, int(conversation_tokens))
            ),
        ))

    def enforce_context_policy(
        self,
        conversation_tokens: int = 0,
        policy: Optional[TruncationPolicy] = None,
        candidate_scores: Optional[dict] = None,
    ) -> dict:
        """Evaluate and ENFORCE the context-truncation policy (M-009).

        The decision is deterministic (``context_policy.evaluate``).
        Enforcement is kernel-owned, never LLM discretion:

        - CHECKPOINT / EVICT / HALT all first persist a full safe point
          through the guarded CHECKPOINTING transition (``self.checkpoint``),
          so HOT is durable before anything is freed.
        - EVICT then frees evictable regions in the policy's deterministic
          order: COLD partitions via ``cold.evict``; WAL via ``truncate``
          (safe only after the checkpoint). HOT is never touched.
        - CONVERSATION cannot be evicted by the kernel (it lives in the LLM
          context), so the kernel returns an explicit drop directive instead.
        - HALT sets ``transfer_required`` — the session must hand off.

        Every enforcement appends a WAL ``CONTEXT_TRUNCATION`` event.
        """
        policy = policy or default_context_policy()
        ledger = self.build_token_ledger(conversation_tokens)
        decision = evaluate_context_policy(ledger, policy, candidate_scores)

        result: dict = {
            "action": decision.action.value,
            "reason": decision.reason,
            "ledger": ledger.to_dict(),
            "decision": decision.to_dict(),
            "executed": {
                "checkpoint": None,
                "evicted": [],
                "wal_truncated": False,
                "conversation_directives": [],
            },
        }

        if decision.action == PolicyAction.NONE:
            return result

        # Enforcement requires a mutating state (so we can checkpoint).
        if self.state_machine.current not in (
            State.READY, State.WORKING, State.INGESTING
        ):
            result["error"] = (
                f"cannot enforce truncation from state "
                f"{self.state_machine.current.value}"
            )
            return result

        # 1. Persist a full safe point through the guarded transition.
        ckpt = self.checkpoint(force_full=True)
        result["executed"]["checkpoint"] = ckpt
        if not ckpt.get("checkpointed"):
            # No safe point established — do NOT evict; surface the failure.
            result["error"] = "checkpoint failed; eviction aborted"
            return result

        # 2. Execute the deterministic eviction plan (EVICT / HALT only).
        if decision.action in (PolicyAction.EVICT, PolicyAction.HALT):
            for step in decision.plan:
                if step.region == MemoryRegion.COLD and step.target:
                    if self.cold.evict(step.target):
                        result["executed"]["evicted"].append(step.target)
                elif step.region == MemoryRegion.WAL:
                    self.wal.truncate()
                    result["executed"]["wal_truncated"] = True
                elif step.region == MemoryRegion.CONVERSATION:
                    result["executed"]["conversation_directives"].append(
                        {"drop_tokens": step.tokens_freed}
                    )

        # 3. WAL-log the enforcement as an auditable event.
        self.wal.append(
            "CONTEXT_TRUNCATION",
            session_id=self.session_id,
            decision=decision.action.value,
            total_before=decision.total_before,
            projected_after=decision.projected_after,
            evicted=result["executed"]["evicted"],
            wal_truncated=result["executed"]["wal_truncated"],
        )
        self.logger.info(
            f"Context policy enforced: {decision.action.value} "
            f"({decision.total_before} -> {decision.projected_after} tokens)",
            category=EventCategory.LIFECYCLE,
        )

        if decision.action == PolicyAction.HALT:
            result["transfer_required"] = True

        return result

    # -- Graph orchestration (v4.0 runtime-wiring) --------------------------

    def run_graph(
        self,
        nodes: list,
        *,
        schedule: str = "sequential",
        stop_on_failure: bool = False,
        rollback_on_failure: bool = False,
        force_full_checkpoint: bool = False,
    ) -> dict:
        """Build and execute a Graph Orchestrator DAG through THIS kernel.

        This is the runtime entry point that makes the Graph Orchestrator
        invokable through the kernel (CLI / MCP / direct API), not merely
        importable. ``nodes`` is a JSON-serializable spec — each item is a
        mapping ``{"id": str, "deps"?: [str], "action": str, "payload"?: {}}``
        (an already-built ``OrchestratorNode`` is also accepted). The DAG is
        built fail-loud, then every node is driven through this kernel's single
        serialized propose -> validate -> commit -> per-node-checkpoint pipeline
        by ``GraphExecutor``. The kernel remains the SOLE writer; this method
        owns control-flow wiring only and adds no new state mutation, WAL event
        type, or schema (the executor's existing GRAPH_NODE_EXECUTED events are
        the audit trail).

        Only the ``sequential`` and ``levels`` schedules are reachable from a
        serialized spec; ``process_levels`` needs picklable ``work`` callables
        that cannot cross the JSON/CLI/MCP boundary, so construct a
        ``GraphExecutor`` in-process for that. Returns the executor report dict
        (JSON-serializable), or ``{"error": ...}`` on a build/spec/state error
        without mutating HOT.
        """
        # Lazy import: graph_orchestrator duck-types KernelApp (no import of
        # api.py at its module scope), so importing it here avoids a cycle.
        from rag_kernel.graph_orchestrator import (
            DAGBuildError,
            ExecutionDAG,
            GraphExecutionError,
            GraphExecutor,
            OrchestratorNode,
            Schedule,
        )

        # Resolve the schedule from the serialized name. PROCESS_LEVELS is
        # deliberately unreachable from a spec (no picklable work callable).
        schedule_map = {
            "sequential": Schedule.SEQUENTIAL,
            "levels": Schedule.LEVELS,
        }
        sched = schedule_map.get(str(schedule).lower())
        if sched is None:
            return {
                "error": (
                    f"unsupported schedule {schedule!r}; "
                    f"use one of {sorted(schedule_map)} "
                    f"(process_levels is in-process only)"
                )
            }

        # Enforcement requires a state where proposals/checkpoints are legal.
        if self.state_machine.current not in (
            State.READY, State.WORKING, State.INGESTING
        ):
            return {
                "error": (
                    f"cannot run graph from state "
                    f"{self.state_machine.current.value}"
                )
            }

        # Build the node objects from the spec (fail-loud).
        try:
            built: list = []
            for spec in nodes:
                if isinstance(spec, OrchestratorNode):
                    built.append(spec)
                    continue
                built.append(OrchestratorNode(
                    id=spec["id"],
                    deps=frozenset(spec.get("deps", ())),
                    action=spec.get("action"),
                    payload=spec.get("payload", {}) or {},
                ))
            dag = ExecutionDAG(built)
            executor = GraphExecutor(
                dag,
                self,
                schedule=sched,
                stop_on_failure=stop_on_failure,
                rollback_on_failure=rollback_on_failure,
                force_full_checkpoint=force_full_checkpoint,
            )
            report = executor.run()
        except (DAGBuildError, GraphExecutionError, KeyError, TypeError) as e:
            return {"error": f"{type(e).__name__}: {e}"}

        self.logger.info(
            f"Graph executed via runtime: schedule={sched.value}, "
            f"nodes={len(dag.node_ids)}, "
            f"done={len(report.get('done', []))}, "
            f"failed={len(report.get('failed', []))}",
            category=EventCategory.LIFECYCLE,
        )
        return report

    # -- WAL ----------------------------------------------------------------

    def get_wal(self, since: int = 0) -> list[dict]:
        """Return WAL entries, optionally filtered by since=seq."""
        return [e.to_dict() for e in self.wal.replay(since=since)]

    # -- Recovery -----------------------------------------------------------

    def recover(self) -> dict:
        """Attempt recovery: try .bak, then report state."""
        bak_path = self.hot_path.with_suffix(
            self.hot_path.suffix + ".bak"
        )

        if bak_path.exists():
            try:
                raw = bak_path.read_text(encoding="utf-8")
                self._hot = json.loads(raw)
                hash_errors = verify_hashes(self._hot)
                if not hash_errors:
                    atomic_write_json(self.hot_path, self._hot)
                    self.wal.append(
                        "RECOVERY_BAK_RESTORED",
                        session_id=self.session_id,
                    )
                    if self.state_machine.current == State.RECOVERY:
                        self.state_machine.transition(State.READY)
                    self.logger.info(
                        "Recovery succeeded from .bak file",
                        category=EventCategory.RECOVERY,
                    )
                    return {
                        "recovered": True,
                        "method": "bak",
                        "state": self.state_machine.current.value,
                    }
                else:
                    return {
                        "recovered": False,
                        "method": "bak",
                        "hash_errors": hash_errors,
                    }
            except (json.JSONDecodeError, OSError) as e:
                return {
                    "recovered": False,
                    "method": "bak",
                    "error": str(e),
                }

        return {
            "recovered": False,
            "error": "No .bak file found. Manual intervention required.",
        }

    def rollback_to_snapshot(self, snapshot: dict, reason: str = "") -> dict:
        """Roll HOT back to a prior in-memory snapshot via a real RECOVERY path.

        Used by the graph orchestrator's transactional (rollback-on-failure)
        mode: when a node fails after earlier nodes in the same run already
        committed, the whole run is undone by restoring the pre-run baseline so
        the DAG is all-or-nothing.

        The kernel — never the executor — owns this state mutation. Mirrors
        recover(): enter RECOVERY (force_state is the only sanctioned escape,
        since READY->RECOVERY is deliberately NOT a normal transition), replace
        HOT atomically (which refreshes .bak), log a GRAPH_ROLLBACK WAL event,
        reset the delta base to the restored state, then return RECOVERY->READY.
        Single-writer is unchanged: this runs in the same session under the same
        project lock.
        """
        import copy

        self.state_machine.force_state(
            State.RECOVERY, reason=reason or "graph rollback"
        )

        self._hot = copy.deepcopy(snapshot)
        state_hash = compute_hash(self._hot)
        if "meta" not in self._hot:
            self._hot["meta"] = {}
        self._hot["meta"]["state_hash"] = state_hash
        self._hot["meta"]["last_checkpoint_seq"] = self.wal.seq
        self._hot["meta"]["session_id"] = self.session_id
        # FIX-3 (K7): a rollback persists HOT too — stamp lineage here as well.
        self._hot["meta"]["written_by_session"] = self.session_id

        # Atomic restore (creates/refreshes .bak), then audit the rollback.
        atomic_write_json(self.hot_path, self._hot)
        self.wal.append(
            "GRAPH_ROLLBACK",
            session_id=self.session_id,
            reason=reason,
            restored_seq=self.wal.seq,
        )

        # The restored state is the new delta-checkpoint base.
        self._delta_mgr.set_base(self._hot, self.wal.seq)
        self._delta_mgr._first_full_done = True

        # RECOVERY -> READY is a legal transition.
        self.state_machine.transition(State.READY)

        self.logger.info(
            f"Graph rollback restored baseline ({reason})",
            category=EventCategory.RECOVERY,
        )

        return {
            "rolled_back": True,
            "state": self.state_machine.current.value,
            "state_hash": state_hash,
            "restored_seq": self.wal.seq,
        }

    # -- Close --------------------------------------------------------------

    def close(self) -> dict:
        """Close the session: checkpoint, flush WAL, release lock."""
        # Full checkpoint on close (ENH-006: always full on session close)
        current = self.state_machine.current
        if current in (State.READY, State.WORKING, State.INGESTING):
            self.checkpoint(is_closing=True)

        # Transition to CLOSING
        if self.state_machine.current != State.CLOSING:
            if not self.state_machine.transition(State.CLOSING):
                self.state_machine.force_state(
                    State.CLOSING, reason="session close"
                )

        # WAL
        self.wal.append(
            "SESSION_CLOSED",
            session_id=self.session_id,
        )
        self.wal.close()

        # Release lock
        self.lock.release()

        # Close session logger (after all other operations)
        self.logger.info(f"Session {self.session_id} closing", category=EventCategory.LIFECYCLE)
        self.logger.close()

        return {
            "closed": True,
            "state": State.CLOSING.value,
            "session_id": self.session_id,
        }


# ---------------------------------------------------------------------------
# HTTP Request Handler
# ---------------------------------------------------------------------------

class KernelHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the RAG Kernel API.

    Routes requests to the KernelApp instance stored on the server.
    All responses are JSON.
    """

    # Suppress default stderr logging
    def log_message(self, format: str, *args: Any) -> None:
        pass  # Override to suppress default logging

    def do_GET(self) -> None:
        """Handle GET requests."""
        path = self.path.split("?")[0]  # strip query string

        routes: dict[str, Callable] = {
            "/status": self._handle_status,
            "/hot": self._handle_hot,
            "/cold": self._handle_cold,
            "/wal": self._handle_wal,
            "/config/pov_mode": self._handle_get_pov_mode,
            "/conflicts/summary": self._handle_conflict_summary,
        }

        # Check for parameterized routes
        cold_match = re.match(r"^/cold/(.+)$", path)
        if cold_match:
            self._handle_cold_partition(cold_match.group(1))
            return

        handler = routes.get(path)
        if handler:
            handler()
        else:
            self._send_json({"error": f"Not found: {path}"}, 404)

    def do_PATCH(self) -> None:
        """Handle PATCH requests."""
        path = self.path.split("?")[0]

        if path == "/config/pov_mode":
            self._handle_set_pov_mode()
        else:
            self._send_json({"error": f"Not found: {path}"}, 404)

    def do_POST(self) -> None:
        """Handle POST requests."""
        path = self.path.split("?")[0]

        routes: dict[str, Callable] = {
            "/boot": self._handle_boot,
            "/propose": self._handle_propose,
            "/checkpoint": self._handle_checkpoint,
            "/recover": self._handle_recover,
            "/close": self._handle_close,
            "/config/pov_mode/check": self._handle_check_auto_escalate,
            "/conflicts/add": self._handle_add_conflict,
            "/conflicts/resolve": self._handle_resolve_conflict,
        }

        # Parameterized routes
        commit_match = re.match(r"^/commit/(.+)$", path)
        if commit_match:
            self._handle_commit(commit_match.group(1))
            return

        reject_match = re.match(r"^/reject/(.+)$", path)
        if reject_match:
            self._handle_reject(reject_match.group(1))
            return

        handler = routes.get(path)
        if handler:
            handler()
        else:
            self._send_json({"error": f"Not found: {path}"}, 404)

    # -- Route handlers -----------------------------------------------------

    def _handle_status(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        self._send_json(app.status())

    def _handle_hot(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        self._send_json(app.get_hot())

    def _handle_cold(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        try:
            self._send_json(app.get_cold())
        except ColdFileError as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_cold_partition(self, partition: str) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        try:
            data = app.get_cold(partition)
            self._send_json(data)
        except PartitionNotFoundError as e:
            self._send_json({"error": str(e)}, 404)
        except ColdFileError as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_wal(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        # Parse ?since=N from query string
        since = 0
        if "?" in self.path:
            query = self.path.split("?")[1]
            for param in query.split("&"):
                if param.startswith("since="):
                    try:
                        since = int(param.split("=")[1])
                    except ValueError:
                        pass
        self._send_json(app.get_wal(since=since))

    def _handle_boot(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        result = app.boot()
        code = 200 if result.get("status") == "OK" else 409
        self._send_json(result, code)

    def _handle_propose(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        body = self._read_body()
        if body is None:
            return
        result = app.propose(body)
        code = 200 if result["valid"] else 400
        self._send_json(result, code)

    def _handle_commit(self, proposal_id: str) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        result = app.commit(proposal_id)
        code = 200 if result["committed"] else 404
        self._send_json(result, code)

    def _handle_reject(self, proposal_id: str) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        result = app.reject(proposal_id)
        code = 200 if result["rejected"] else 404
        self._send_json(result, code)

    def _handle_checkpoint(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        result = app.checkpoint()
        code = 200 if result["checkpointed"] else 409
        self._send_json(result, code)

    def _handle_recover(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        result = app.recover()
        code = 200 if result.get("recovered") else 500
        self._send_json(result, code)

    def _handle_close(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        result = app.close()
        self._send_json(result)

    def _handle_get_pov_mode(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        self._send_json({
            "pov_mode": app.get_pov_mode(),
        })

    def _handle_set_pov_mode(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        body = self._read_body()
        if body is None:
            return
        mode = body.get("mode")
        if mode is None:
            self._send_json({"error": "Missing 'mode' field"}, 400)
            return
        result = app.set_pov_mode(mode)
        code = 200 if result.get("updated") else 400
        self._send_json(result, code)

    def _handle_check_auto_escalate(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        body = self._read_body()
        if body is None:
            return
        op_type = body.get("operation_type")
        if op_type is None:
            self._send_json({"error": "Missing 'operation_type' field"}, 400)
            return
        self._send_json(app.check_auto_escalate(op_type))

    def _handle_conflict_summary(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        self._send_json(app.get_conflict_summary())

    def _handle_add_conflict(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        body = self._read_body()
        if body is None:
            return
        result = app.add_conflict(body)
        code = 200 if result.get("added") else 400
        self._send_json(result, code)

    def _handle_resolve_conflict(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        body = self._read_body()
        if body is None:
            return
        conflict_id = body.get("conflict_id")
        resolution = body.get("resolution")
        if not conflict_id or not resolution:
            self._send_json(
                {"error": "Missing 'conflict_id' and/or 'resolution' fields"},
                400,
            )
            return
        resolver = body.get("resolver", "user")
        result = app.resolve_conflict(conflict_id, resolution, resolver)
        code = 200 if result.get("resolved") else 404
        self._send_json(result, code)

    # -- Helpers ------------------------------------------------------------

    def _read_body(self) -> Optional[dict]:
        """Read and parse JSON request body."""
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._send_json({"error": "Empty request body"}, 400)
            return None
        try:
            raw = self.rfile.read(length)
            return json.loads(raw)
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON"}, 400)
            return None

    def _send_json(self, data: Any, code: int = 200) -> None:
        """Send a JSON response."""
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

class KernelHTTPServer(HTTPServer):
    """HTTPServer subclass that carries a KernelApp reference."""

    def __init__(
        self,
        app: KernelApp,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
    ) -> None:
        self.app = app
        super().__init__((host, port), KernelHTTPHandler)


def create_server(
    project_dir: Path,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    session_id: Optional[str] = None,
) -> KernelHTTPServer:
    """Create a configured HTTP server ready to serve.

    Usage:
        server = create_server(Path("RAG"))
        server.app.boot()
        server.serve_forever()
    """
    app = KernelApp(project_dir, session_id=session_id)
    return KernelHTTPServer(app, host, port)
