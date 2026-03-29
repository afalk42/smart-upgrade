"""Tests for smart_upgrade.models."""

from smart_upgrade.models import (
    AnalysisResult,
    Finding,
    FindingCategory,
    PackageSource,
    PendingUpgrade,
    Recommendation,
    RiskLevel,
    ThreatIntelResult,
    UpgradeDecision,
)


class TestEnums:
    def test_risk_level_values(self):
        # Verify all expected members exist.
        assert set(RiskLevel) == {
            RiskLevel.CLEAR, RiskLevel.LOW, RiskLevel.MEDIUM,
            RiskLevel.HIGH, RiskLevel.CRITICAL,
        }

    def test_risk_level_str(self):
        assert str(RiskLevel.CLEAR) == "clear"
        assert str(RiskLevel.CRITICAL) == "critical"

    def test_recommendation_str(self):
        assert str(Recommendation.PROCEED) == "proceed"
        assert str(Recommendation.BLOCK) == "block"

    def test_package_source_str(self):
        assert str(PackageSource.APT) == "apt"
        assert str(PackageSource.BREW_FORMULA) == "brew-formula"
        assert str(PackageSource.BREW_CASK) == "brew-cask"


class TestPendingUpgrade:
    def test_basic_creation(self):
        pkg = PendingUpgrade(
            name="curl",
            current_version="7.81.0",
            new_version="7.82.0",
            source=PackageSource.APT,
        )
        assert pkg.name == "curl"
        assert pkg.maintainer is None
        assert pkg.homepage is None

    def test_full_creation(self):
        pkg = PendingUpgrade(
            name="git",
            current_version="2.44.0",
            new_version="2.45.0",
            source=PackageSource.BREW_FORMULA,
            maintainer="homebrew",
            homepage="https://git-scm.com",
            source_repo="https://github.com/git/git",
        )
        assert pkg.source == PackageSource.BREW_FORMULA
        assert pkg.source_repo == "https://github.com/git/git"


class TestAnalysisResult:
    def test_defaults(self):
        result = AnalysisResult(package_name="test")
        assert result.risk_level == RiskLevel.CLEAR
        assert result.recommendation == Recommendation.PROCEED
        assert result.findings == []
        assert result.details == ""


class TestUpgradeDecision:
    def test_approved(self):
        pkg = PendingUpgrade("curl", "1.0", "2.0", PackageSource.APT)
        decision = UpgradeDecision(package=pkg, analysis=None, approved=True)
        assert decision.approved is True
        assert decision.skipped_reason is None

    def test_rejected(self):
        pkg = PendingUpgrade("curl", "1.0", "2.0", PackageSource.APT)
        decision = UpgradeDecision(package=pkg, analysis=None, approved=False, skipped_reason="user declined")
        assert decision.approved is False
        assert decision.skipped_reason == "user declined"
