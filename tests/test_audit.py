"""Tests for smart_upgrade.audit."""

import os
import stat

import yaml

from smart_upgrade.audit import build_audit_entry, write_audit_log
from smart_upgrade.models import (
    AnalysisResult,
    PackageSource,
    PendingUpgrade,
    Recommendation,
    RiskLevel,
    UpgradeDecision,
)


class TestBuildAuditEntry:
    def test_creates_entry(self):
        pkg = PendingUpgrade("curl", "1.0", "2.0", PackageSource.APT)
        result = AnalysisResult(package_name="curl")
        decision = UpgradeDecision(package=pkg, analysis=result, approved=True)

        entry = build_audit_entry(
            platform="linux-apt",
            package_manager="APT",
            pending=[pkg],
            results=[result],
            decisions=[decision],
            upgraded=["curl"],
            skipped=[],
        )

        assert entry.platform == "linux-apt"
        assert len(entry.pending_upgrades) == 1
        assert entry.upgraded == ["curl"]
        assert entry.timestamp  # Non-empty


class TestWriteAuditLog:
    def test_writes_yaml_file(self, tmp_path):
        pkg = PendingUpgrade("curl", "1.0", "2.0", PackageSource.APT)
        result = AnalysisResult(package_name="curl", risk_level=RiskLevel.CLEAR)
        decision = UpgradeDecision(package=pkg, analysis=result, approved=True)

        entry = build_audit_entry(
            platform="linux-apt",
            package_manager="APT",
            pending=[pkg],
            results=[result],
            decisions=[decision],
            upgraded=["curl"],
            skipped=[],
        )

        path = write_audit_log(entry, tmp_path)

        assert path.exists()
        assert path.suffix == ".yaml"

        # Verify it's valid YAML.
        with open(path) as fh:
            data = yaml.safe_load(fh)
        assert data["platform"] == "linux-apt"
        assert data["upgraded"] == ["curl"]

        # Verify decisions are compact (no duplicated analysis/package data).
        decisions = data["decisions"]
        assert len(decisions) == 1
        d = decisions[0]
        assert d["package"] == "curl"
        assert d["approved"] is True
        assert d["risk_level"] == "clear"
        # Should NOT contain nested package or analysis objects.
        assert "current_version" not in d
        assert "findings" not in d

    def test_restrictive_permissions(self, tmp_path):
        pkg = PendingUpgrade("curl", "1.0", "2.0", PackageSource.APT)
        entry = build_audit_entry("linux-apt", "APT", [pkg], [], [], [], [])

        path = write_audit_log(entry, tmp_path)

        mode = os.stat(path).st_mode
        assert mode & stat.S_IRUSR  # Owner can read
        assert mode & stat.S_IWUSR  # Owner can write
        assert not (mode & stat.S_IRGRP)  # Group cannot read
        assert not (mode & stat.S_IROTH)  # Others cannot read

    def test_creates_directory(self, tmp_path):
        log_dir = tmp_path / "nested" / "logs"
        pkg = PendingUpgrade("curl", "1.0", "2.0", PackageSource.APT)
        entry = build_audit_entry("linux-apt", "APT", [pkg], [], [], [], [])

        path = write_audit_log(entry, log_dir)

        assert log_dir.exists()
        assert path.exists()
