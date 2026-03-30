"""Data models used throughout smart-upgrade.

All core data structures are defined here as dataclasses so that every module
shares a single, well-documented set of types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class RiskLevel(str, Enum):
    """Severity / risk classification returned by the analysis engine."""

    CLEAR = "clear"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    def __str__(self) -> str:
        return self.value


class Recommendation(str, Enum):
    """Action recommendation attached to an analysis result."""

    PROCEED = "proceed"
    REVIEW = "review"
    BLOCK = "block"

    def __str__(self) -> str:
        return self.value


class PackageSource(str, Enum):
    """Which package manager / channel the package comes from."""

    APT = "apt"
    BREW_FORMULA = "brew-formula"
    BREW_CASK = "brew-cask"

    def __str__(self) -> str:
        return self.value


class FindingCategory(str, Enum):
    """Broad classification for a security finding."""

    MAINTAINER_CHANGE = "maintainer_change"
    SUSPICIOUS_CODE = "suspicious_code"
    KNOWN_CVE = "known_cve"
    SUPPLY_CHAIN_NEWS = "supply_chain_news"
    VERSION_ANOMALY = "version_anomaly"
    DEPENDENCY_CHANGE = "dependency_change"
    OBFUSCATED_PAYLOAD = "obfuscated_payload"
    CREDENTIAL_HARVESTING = "credential_harvesting"
    NETWORK_CALL = "network_call"
    OTHER = "other"

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PendingUpgrade:
    """A single package that has an upgrade available."""

    name: str
    current_version: str
    new_version: str
    source: PackageSource
    maintainer: str | None = None
    homepage: str | None = None
    source_repo: str | None = None
    apt_origin: str | None = None


@dataclass
class Finding:
    """A single security observation from any analysis layer."""

    category: FindingCategory
    severity: RiskLevel
    description: str
    source: str
    reference_url: str | None = None


@dataclass
class ThreatIntelResult:
    """Results from a single threat-intelligence source for one package."""

    source: str          # "brave_search", "osv", "nvd"
    query: str           # What was searched
    findings: list[str] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)
    severity: RiskLevel = RiskLevel.CLEAR


@dataclass
class AnalysisResult:
    """Aggregated analysis for a single package across all layers."""

    package_name: str
    risk_level: RiskLevel = RiskLevel.CLEAR
    findings: list[Finding] = field(default_factory=list)
    recommendation: Recommendation = Recommendation.PROCEED
    details: str = ""


@dataclass
class UpgradeDecision:
    """The user's decision for a single package after reviewing the analysis."""

    package: PendingUpgrade
    analysis: AnalysisResult | None
    approved: bool = False
    skipped_reason: str | None = None


@dataclass
class AuditEntry:
    """A complete audit record for one run of smart-upgrade."""

    timestamp: str
    platform: str
    package_manager: str
    pending_upgrades: list[PendingUpgrade] = field(default_factory=list)
    analysis_results: list[AnalysisResult] = field(default_factory=list)
    decisions: list[UpgradeDecision] = field(default_factory=list)
    upgraded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
