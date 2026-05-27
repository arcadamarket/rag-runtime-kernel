"""Conflict auto-categorization engine for the RAG Runtime Kernel.

Provides rule-based classification of data conflicts by type, with
suggested resolution paths. Reduces user decision fatigue by
auto-categorizing conflicts on intake and suggesting resolutions
for low-risk categories.

Conflict categories:
- TEMPORAL_DRIFT: same field diverged over time (stale data)
- SOURCE_DISAGREEMENT: two sources give different values for same fact
- DATA_QUALITY: malformed, missing, or invalid data
- SCHEMA_MISMATCH: structural incompatibility between versions
- DUPLICATE_ENTRY: same logical record appears twice
- PRIORITY_CONFLICT: two rules or policies contradict

Design doc reference: v3.2_ARCHITECTURE_DESIGN.md
Spec reference: §11 — CONFLICT LEDGER
Satisfies: ENH-005 (conflict auto-categorization)

@rag-kernel-manifest
{
  "module": "rag_kernel.conflict_engine",
  "capability": "conflict_classification",
  "description": "Rule-based conflict auto-categorization with suggested resolutions",
  "exports": ["ConflictCategory", "ConflictRecord", "classify_conflict", "suggest_resolution", "ConflictEngine"],
  "use_when": "Processing add_conflict proposals or reviewing conflict ledger",
  "never_bypass": false
}
"""

from __future__ import annotations

import enum
import hashlib
import re
import time
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Conflict categories
# ---------------------------------------------------------------------------

class ConflictCategory(enum.Enum):
    """Classification categories for data conflicts.

    Each category maps to a distinct root cause pattern and has
    different suggested resolution strategies.
    """
    TEMPORAL_DRIFT = "temporal_drift"
    SOURCE_DISAGREEMENT = "source_disagreement"
    DATA_QUALITY = "data_quality"
    SCHEMA_MISMATCH = "schema_mismatch"
    DUPLICATE_ENTRY = "duplicate_entry"
    PRIORITY_CONFLICT = "priority_conflict"
    UNCATEGORIZED = "uncategorized"


# ---------------------------------------------------------------------------
# Confidence levels
# ---------------------------------------------------------------------------

VALID_CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})


# ---------------------------------------------------------------------------
# Conflict record
# ---------------------------------------------------------------------------

class ConflictRecord:
    """A structured conflict record compatible with §11 schema.

    Extends the base §11 fields (source_a, source_b, difference,
    resolution, confidence, resolver, timestamp_utc) with:
    - category: auto-assigned ConflictCategory
    - suggested_resolution: text suggestion from classifier
    - auto_resolved: whether the engine resolved it automatically
    - conflict_id: unique identifier for tracking
    """

    def __init__(
        self,
        source_a: str,
        source_b: str,
        difference: str,
        *,
        source_a_tier: Optional[str] = None,
        source_b_tier: Optional[str] = None,
        source_a_value: Any = None,
        source_b_value: Any = None,
        field_name: Optional[str] = None,
        resolution: Optional[str] = None,
        confidence: str = "low",
        resolver: Optional[str] = None,
        timestamp_utc: Optional[str] = None,
        conflict_id: Optional[str] = None,
        category: Optional[ConflictCategory] = None,
        suggested_resolution: Optional[str] = None,
        auto_resolved: bool = False,
    ) -> None:
        self.conflict_id = conflict_id or self._generate_id(source_a, source_b, difference)
        self.source_a = source_a
        self.source_b = source_b
        self.source_a_tier = source_a_tier
        self.source_b_tier = source_b_tier
        self.source_a_value = source_a_value
        self.source_b_value = source_b_value
        self.field_name = field_name
        self.difference = difference
        self.resolution = resolution
        self.confidence = confidence if confidence in VALID_CONFIDENCE_LEVELS else "low"
        self.resolver = resolver
        self.timestamp_utc = timestamp_utc or time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )
        self.category = category or ConflictCategory.UNCATEGORIZED
        self.suggested_resolution = suggested_resolution
        self.auto_resolved = auto_resolved

    @staticmethod
    def _generate_id(source_a: str, source_b: str, difference: str) -> str:
        """Generate a deterministic conflict ID from content."""
        content = f"{source_a}|{source_b}|{difference}"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        return f"C-{digest}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to §11-compatible dict with ENH-005 extensions."""
        d: dict[str, Any] = {
            "conflict_id": self.conflict_id,
            "source_a": self.source_a,
            "source_b": self.source_b,
            "difference": self.difference,
            "resolution": self.resolution,
            "confidence": self.confidence,
            "resolver": self.resolver,
            "timestamp_utc": self.timestamp_utc,
            # ENH-005 extensions
            "category": self.category.value,
            "suggested_resolution": self.suggested_resolution,
            "auto_resolved": self.auto_resolved,
        }
        # Optional fields — include only when set
        if self.source_a_tier is not None:
            d["source_a_tier"] = self.source_a_tier
        if self.source_b_tier is not None:
            d["source_b_tier"] = self.source_b_tier
        if self.source_a_value is not None:
            d["source_a_value"] = self.source_a_value
        if self.source_b_value is not None:
            d["source_b_value"] = self.source_b_value
        if self.field_name is not None:
            d["field_name"] = self.field_name
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConflictRecord":
        """Deserialize from dict."""
        cat_str = data.get("category", "uncategorized")
        try:
            category = ConflictCategory(cat_str)
        except ValueError:
            category = ConflictCategory.UNCATEGORIZED

        return cls(
            source_a=data.get("source_a", ""),
            source_b=data.get("source_b", ""),
            difference=data.get("difference", ""),
            source_a_tier=data.get("source_a_tier"),
            source_b_tier=data.get("source_b_tier"),
            source_a_value=data.get("source_a_value"),
            source_b_value=data.get("source_b_value"),
            field_name=data.get("field_name"),
            resolution=data.get("resolution"),
            confidence=data.get("confidence", "low"),
            resolver=data.get("resolver"),
            timestamp_utc=data.get("timestamp_utc"),
            conflict_id=data.get("conflict_id"),
            category=category,
            suggested_resolution=data.get("suggested_resolution"),
            auto_resolved=data.get("auto_resolved", False),
        )

    def __repr__(self) -> str:
        return (
            f"ConflictRecord(id={self.conflict_id!r}, "
            f"category={self.category.value!r}, "
            f"resolved={self.auto_resolved})"
        )


# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------

# Patterns that suggest temporal drift
_TEMPORAL_KEYWORDS = re.compile(
    r"(stale|outdated|old|newer|updated|changed since|version.*differ|"
    r"timestamp.*mismatch|date.*differ|was.*now|previously|superseded)",
    re.IGNORECASE,
)

# Patterns that suggest data quality issues
_QUALITY_KEYWORDS = re.compile(
    r"(missing|null|empty|invalid|malformed|corrupt|truncat|"
    r"encoding|parse error|not found|incomplete|broken|NaN|undefined)",
    re.IGNORECASE,
)

# Patterns that suggest schema mismatch
_SCHEMA_KEYWORDS = re.compile(
    r"(schema|structure|format.*differ|field.*missing|key.*absent|"
    r"type.*mismatch|incompatible|version.*schema|migration|"
    r"expected.*got|wrong type)",
    re.IGNORECASE,
)

# Patterns that suggest duplicate entries
_DUPLICATE_KEYWORDS = re.compile(
    r"(duplicate|same.*entry|identical|already exists|"
    r"copy|redundant|repeated|double)",
    re.IGNORECASE,
)

# Patterns that suggest priority/policy conflicts
_PRIORITY_KEYWORDS = re.compile(
    r"(priority|policy|rule.*conflict|contradict|override|"
    r"precedence|which.*takes|mutually exclusive|incompatible.*rule)",
    re.IGNORECASE,
)

# ISO timestamp pattern for temporal analysis
_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
)


def classify_conflict(
    source_a: str,
    source_b: str,
    difference: str,
    *,
    source_a_value: Any = None,
    source_b_value: Any = None,
    field_name: Optional[str] = None,
) -> tuple[ConflictCategory, str]:
    """Classify a conflict by analyzing its description and values.

    Uses rule-based pattern matching on the difference text, field names,
    and value types. No ML dependencies.

    Returns (category, confidence) where confidence is high/medium/low.
    """
    # Score each category. Highest score wins.
    scores: dict[ConflictCategory, float] = {cat: 0.0 for cat in ConflictCategory}

    # --- Text-based classification on difference field ---
    if _TEMPORAL_KEYWORDS.search(difference):
        scores[ConflictCategory.TEMPORAL_DRIFT] += 3.0
    if _QUALITY_KEYWORDS.search(difference):
        scores[ConflictCategory.DATA_QUALITY] += 3.0
    if _SCHEMA_KEYWORDS.search(difference):
        scores[ConflictCategory.SCHEMA_MISMATCH] += 3.0
    if _DUPLICATE_KEYWORDS.search(difference):
        scores[ConflictCategory.DUPLICATE_ENTRY] += 3.0
    if _PRIORITY_KEYWORDS.search(difference):
        scores[ConflictCategory.PRIORITY_CONFLICT] += 3.0

    # --- Structural analysis ---

    # If both values are provided and identical → duplicate
    if source_a_value is not None and source_b_value is not None:
        if source_a_value == source_b_value:
            scores[ConflictCategory.DUPLICATE_ENTRY] += 4.0
        elif type(source_a_value) != type(source_b_value):
            # Different types → schema mismatch
            scores[ConflictCategory.SCHEMA_MISMATCH] += 2.0

    # If values contain timestamps, likely temporal drift
    for val in (source_a_value, source_b_value):
        if isinstance(val, str) and _ISO_TIMESTAMP.search(val):
            scores[ConflictCategory.TEMPORAL_DRIFT] += 1.5

    # If either value is None/empty → data quality
    if source_a_value is None or source_b_value is None:
        if source_a_value is not None or source_b_value is not None:
            # One has data, the other doesn't
            scores[ConflictCategory.DATA_QUALITY] += 2.5

    for val in (source_a_value, source_b_value):
        if val == "" or val == [] or val == {}:
            scores[ConflictCategory.DATA_QUALITY] += 1.5

    # --- Source analysis ---
    # Same source path → temporal drift (same file changed over time)
    if source_a == source_b:
        scores[ConflictCategory.TEMPORAL_DRIFT] += 2.0

    # Different sources → source disagreement baseline
    if source_a != source_b:
        scores[ConflictCategory.SOURCE_DISAGREEMENT] += 1.0

    # --- Field name analysis ---
    if field_name:
        fn_lower = field_name.lower()
        if any(kw in fn_lower for kw in ("date", "time", "version", "updated", "created")):
            scores[ConflictCategory.TEMPORAL_DRIFT] += 2.0
        if any(kw in fn_lower for kw in ("schema", "type", "format", "structure")):
            scores[ConflictCategory.SCHEMA_MISMATCH] += 2.0
        if any(kw in fn_lower for kw in ("priority", "rule", "policy", "weight")):
            scores[ConflictCategory.PRIORITY_CONFLICT] += 2.0

    # --- Find winner ---
    # Remove UNCATEGORIZED from scoring (it's the fallback)
    scores.pop(ConflictCategory.UNCATEGORIZED, None)

    if not scores:
        return ConflictCategory.UNCATEGORIZED, "low"

    best_cat = max(scores, key=lambda k: scores[k])
    best_score = scores[best_cat]

    # If no category scored at all, it's uncategorized
    if best_score <= 0:
        return ConflictCategory.UNCATEGORIZED, "low"

    # Confidence based on score magnitude and separation
    sorted_scores = sorted(scores.values(), reverse=True)
    separation = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else sorted_scores[0]

    if best_score >= 5.0 and separation >= 2.0:
        confidence = "high"
    elif best_score >= 3.0:
        confidence = "medium"
    else:
        confidence = "low"

    return best_cat, confidence


# ---------------------------------------------------------------------------
# Resolution suggestions
# ---------------------------------------------------------------------------

_RESOLUTION_MAP: dict[ConflictCategory, str] = {
    ConflictCategory.TEMPORAL_DRIFT: (
        "Accept the newer value. If timestamps are available, use the most "
        "recent. If unclear, keep both and flag for user review."
    ),
    ConflictCategory.SOURCE_DISAGREEMENT: (
        "Compare source tiers — higher-tier source takes precedence. "
        "If same tier, preserve both records and escalate to user."
    ),
    ConflictCategory.DATA_QUALITY: (
        "Prefer the non-empty/valid value. If both are problematic, "
        "flag for manual data correction."
    ),
    ConflictCategory.SCHEMA_MISMATCH: (
        "Identify which schema version is current. Migrate the older "
        "record to the current schema. If ambiguous, escalate to user."
    ),
    ConflictCategory.DUPLICATE_ENTRY: (
        "Keep the first occurrence (lowest conflict_id or earliest timestamp). "
        "Archive the duplicate. Verify no unique data would be lost."
    ),
    ConflictCategory.PRIORITY_CONFLICT: (
        "Cannot auto-resolve policy conflicts. Escalate to user with "
        "both rules clearly stated. User must decide precedence."
    ),
    ConflictCategory.UNCATEGORIZED: (
        "Unable to auto-categorize. Manual review required. "
        "Provide both sources to user for decision."
    ),
}

# Categories safe for auto-resolution (low risk)
_AUTO_RESOLVABLE = frozenset({
    ConflictCategory.TEMPORAL_DRIFT,
    ConflictCategory.DUPLICATE_ENTRY,
    ConflictCategory.DATA_QUALITY,
})


def suggest_resolution(category: ConflictCategory) -> str:
    """Return a suggested resolution path for the given category."""
    return _RESOLUTION_MAP.get(category, _RESOLUTION_MAP[ConflictCategory.UNCATEGORIZED])


def is_auto_resolvable(category: ConflictCategory, confidence: str) -> bool:
    """Check if a conflict category can be auto-resolved.

    Auto-resolution requires:
    1. Category is in the safe auto-resolve set
    2. Classification confidence is high

    PRIORITY_CONFLICT and SOURCE_DISAGREEMENT always require user input.
    """
    return category in _AUTO_RESOLVABLE and confidence == "high"


# ---------------------------------------------------------------------------
# Conflict Engine (stateful manager)
# ---------------------------------------------------------------------------

class ConflictEngine:
    """Manages the conflict lifecycle: intake, classification, resolution.

    Integrates with HOT (active_conflicts_count) and COLD (conflict_ledger).
    Thread-safe for single-session use (kernel is single-session by design).
    """

    def __init__(self) -> None:
        self._active: dict[str, ConflictRecord] = {}
        self._resolved: list[ConflictRecord] = []

    @property
    def active_count(self) -> int:
        """Number of unresolved conflicts."""
        return len(self._active)

    @property
    def resolved_count(self) -> int:
        """Number of resolved conflicts this session."""
        return len(self._resolved)

    def load_from_ledger(self, ledger: list[dict[str, Any]]) -> int:
        """Load existing conflicts from COLD conflict_ledger.

        Returns count of loaded active (unresolved) conflicts.
        """
        loaded = 0
        for entry in ledger:
            record = ConflictRecord.from_dict(entry)
            if record.resolution is None:
                self._active[record.conflict_id] = record
                loaded += 1
            else:
                self._resolved.append(record)
        return loaded

    def add_conflict(
        self,
        source_a: str,
        source_b: str,
        difference: str,
        *,
        source_a_tier: Optional[str] = None,
        source_b_tier: Optional[str] = None,
        source_a_value: Any = None,
        source_b_value: Any = None,
        field_name: Optional[str] = None,
    ) -> ConflictRecord:
        """Add a new conflict. Auto-classifies and suggests resolution.

        If the category is auto-resolvable with high confidence,
        the conflict is auto-resolved immediately.

        Returns the (possibly auto-resolved) ConflictRecord.
        """
        # Classify
        category, confidence = classify_conflict(
            source_a, source_b, difference,
            source_a_value=source_a_value,
            source_b_value=source_b_value,
            field_name=field_name,
        )

        # Build record
        record = ConflictRecord(
            source_a=source_a,
            source_b=source_b,
            difference=difference,
            source_a_tier=source_a_tier,
            source_b_tier=source_b_tier,
            source_a_value=source_a_value,
            source_b_value=source_b_value,
            field_name=field_name,
            category=category,
            confidence=confidence,
            suggested_resolution=suggest_resolution(category),
        )

        # Check auto-resolution
        if is_auto_resolvable(category, confidence):
            record.auto_resolved = True
            record.resolution = record.suggested_resolution
            record.resolver = "engine"
            self._resolved.append(record)
        else:
            self._active[record.conflict_id] = record

        return record

    def resolve_conflict(
        self,
        conflict_id: str,
        resolution: str,
        resolver: str = "user",
    ) -> Optional[ConflictRecord]:
        """Manually resolve an active conflict.

        Returns the resolved record, or None if conflict_id not found.
        """
        record = self._active.pop(conflict_id, None)
        if record is None:
            return None

        record.resolution = resolution
        record.resolver = resolver
        self._resolved.append(record)
        return record

    def get_conflict(self, conflict_id: str) -> Optional[ConflictRecord]:
        """Look up a conflict by ID (active or resolved)."""
        if conflict_id in self._active:
            return self._active[conflict_id]
        for r in self._resolved:
            if r.conflict_id == conflict_id:
                return r
        return None

    def get_active(self) -> list[ConflictRecord]:
        """Return all active (unresolved) conflicts."""
        return list(self._active.values())

    def get_resolved(self) -> list[ConflictRecord]:
        """Return all resolved conflicts."""
        return list(self._resolved)

    def summary(self) -> dict[str, Any]:
        """Return a summary of conflict state by category.

        Suitable for inclusion in HOT status or boot warnings.
        """
        by_category: dict[str, int] = {}
        for record in self._active.values():
            cat = record.category.value
            by_category[cat] = by_category.get(cat, 0) + 1

        auto_resolved_count = sum(
            1 for r in self._resolved if r.auto_resolved
        )

        return {
            "active_count": self.active_count,
            "resolved_count": self.resolved_count,
            "auto_resolved_count": auto_resolved_count,
            "user_resolved_count": self.resolved_count - auto_resolved_count,
            "active_by_category": by_category,
        }

    def export_ledger(self) -> list[dict[str, Any]]:
        """Export all conflicts (active + resolved) as §11-compatible dicts.

        Suitable for writing to COLD conflict_ledger.
        """
        ledger: list[dict[str, Any]] = []
        for record in self._active.values():
            ledger.append(record.to_dict())
        for record in self._resolved:
            ledger.append(record.to_dict())
        return ledger


# ---------------------------------------------------------------------------
# Validation helpers (for schema integration)
# ---------------------------------------------------------------------------

VALID_CONFLICT_CATEGORIES = frozenset(c.value for c in ConflictCategory)

REQUIRED_CONFLICT_FIELDS = frozenset({
    "source_a",
    "source_b",
    "difference",
})


def validate_conflict_payload(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate an add_conflict proposal payload.

    Required: source_a, source_b, difference (all strings).
    Optional: source_a_tier, source_b_tier, source_a_value, source_b_value,
              field_name, category (if pre-set).

    Returns (valid, errors).
    """
    errors: list[str] = []

    if not isinstance(payload, dict):
        return False, [f"Payload must be a dict, got {type(payload).__name__}"]

    for field in REQUIRED_CONFLICT_FIELDS:
        val = payload.get(field)
        if val is None:
            errors.append(f"Missing required field: '{field}'")
        elif not isinstance(val, str):
            errors.append(f"'{field}' must be a string, got {type(val).__name__}")
        elif not val.strip():
            errors.append(f"'{field}' must not be empty")

    # Optional category validation
    cat = payload.get("category")
    if cat is not None and cat not in VALID_CONFLICT_CATEGORIES:
        errors.append(
            f"Invalid category '{cat}'. "
            f"Valid: {sorted(VALID_CONFLICT_CATEGORIES)}"
        )

    return len(errors) == 0, errors
