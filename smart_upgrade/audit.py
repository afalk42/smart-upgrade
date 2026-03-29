"""Audit trail writer — records every run for post-hoc review.

Each invocation of ``smart-upgrade`` produces a YAML file in the configured
log directory (default: ``~/.local/share/smart-upgrade/logs/``).  The file
captures the full list of pending upgrades, analysis results, user decisions,
and final outcome.
"""

from __future__ import annotations

import logging
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from smart_upgrade.models import (
    AnalysisResult,
    AuditEntry,
    PendingUpgrade,
    UpgradeDecision,
)

logger = logging.getLogger(__name__)


def _to_serializable(obj: Any) -> Any:
    """Recursively convert dataclasses and enums to plain dicts/strings."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_serializable(v) for k, v in obj.__dict__.items()}
    if isinstance(obj, list):
        return [_to_serializable(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "value"):  # Enum
        return obj.value
    return obj


def _serialize_decisions(decisions: list[UpgradeDecision]) -> list[dict[str, Any]]:
    """Serialize decisions without duplicating package/analysis data.

    The ``pending_upgrades`` and ``analysis_results`` sections of the
    audit log already contain the full details.  The decisions section
    only needs to record what the user decided for each package.
    """
    result: list[dict[str, Any]] = []
    for d in decisions:
        entry: dict[str, Any] = {
            "package": d.package.name,
            "approved": d.approved,
        }
        if d.skipped_reason:
            entry["skipped_reason"] = d.skipped_reason
        if d.analysis:
            entry["risk_level"] = d.analysis.risk_level.value
            entry["recommendation"] = d.analysis.recommendation.value
        result.append(entry)
    return result


def build_audit_entry(
    platform: str,
    package_manager: str,
    pending: list[PendingUpgrade],
    results: list[AnalysisResult],
    decisions: list[UpgradeDecision],
    upgraded: list[str],
    skipped: list[str],
    errors: list[str] | None = None,
) -> AuditEntry:
    """Construct an :class:`AuditEntry` for the current run."""
    return AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        platform=platform,
        package_manager=package_manager,
        pending_upgrades=pending,
        analysis_results=results,
        decisions=decisions,
        upgraded=upgraded,
        skipped=skipped,
        errors=errors or [],
    )


def write_audit_log(entry: AuditEntry, log_dir: Path) -> Path:
    """Write *entry* as a YAML file and return the path.

    The file is written with ``0o600`` permissions (owner read/write only)
    to protect potentially sensitive analysis data.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{timestamp}.yaml"
    path = log_dir / filename

    data = _to_serializable(entry)

    # Replace the fully-expanded decisions with the compact version
    # that only records per-package decision data (package name,
    # approved/skipped, risk level) — without duplicating the full
    # PendingUpgrade and AnalysisResult already present above.
    data["decisions"] = _serialize_decisions(entry.decisions)

    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Set restrictive permissions (owner read/write only).
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    logger.info("Audit log written: %s", path)
    return path
