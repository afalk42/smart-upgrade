"""Tests for smart_upgrade.analysis.threat_intel."""

import json
from unittest.mock import patch, MagicMock

import pytest

from smart_upgrade.analysis.threat_intel import (
    gather_threat_intel,
    query_brave_search,
    query_nvd,
    query_osv,
)
from smart_upgrade.models import RiskLevel


class TestQueryOSV:
    @patch("smart_upgrade.analysis.threat_intel._http_post_json")
    def test_no_vulns(self, mock_post):
        mock_post.return_value = {"vulns": []}
        result = query_osv("curl", ecosystem="Debian")
        assert result.source == "osv"
        assert result.severity == RiskLevel.CLEAR
        assert result.findings == []

    @patch("smart_upgrade.analysis.threat_intel._http_post_json")
    def test_with_vulns(self, mock_post):
        mock_post.return_value = {
            "vulns": [
                {
                    "id": "DSA-5587-1",
                    "summary": "Security update for curl",
                    "severity": [{"score": "7.5"}],
                }
            ]
        }
        result = query_osv("curl", ecosystem="Debian")
        assert result.severity == RiskLevel.MEDIUM
        assert len(result.findings) == 1
        assert "DSA-5587-1" in result.findings[0]

    @patch("smart_upgrade.analysis.threat_intel._http_post_json")
    def test_api_failure(self, mock_post):
        mock_post.return_value = {}
        result = query_osv("curl", ecosystem="Debian")
        assert result.severity == RiskLevel.CLEAR


class TestQueryNVD:
    @patch("smart_upgrade.analysis.threat_intel._http_get")
    def test_no_vulns(self, mock_get):
        mock_get.return_value = {"vulnerabilities": []}
        result = query_nvd("curl")
        assert result.source == "nvd"
        assert result.severity == RiskLevel.CLEAR

    @patch("smart_upgrade.analysis.threat_intel._http_get")
    def test_with_vulns(self, mock_get):
        mock_get.return_value = {
            "vulnerabilities": [
                {
                    "cve": {
                        "id": "CVE-2024-7264",
                        "descriptions": [
                            {"lang": "en", "value": "Denial of service in curl"}
                        ],
                    }
                }
            ]
        }
        result = query_nvd("curl")
        assert result.severity == RiskLevel.LOW
        assert "CVE-2024-7264" in result.findings[0]


class TestQueryBraveSearch:
    @patch("smart_upgrade.analysis.threat_intel._http_get")
    def test_no_results(self, mock_get):
        mock_get.return_value = {"web": {"results": []}}
        result = query_brave_search("curl", api_key="test-key")
        assert result.source == "brave_search"
        assert result.severity == RiskLevel.CLEAR

    @patch("smart_upgrade.analysis.threat_intel._http_get")
    def test_with_results(self, mock_get):
        mock_get.return_value = {
            "web": {
                "results": [
                    {
                        "title": "curl maintainer account compromised",
                        "description": "Supply chain attack detected",
                        "url": "https://example.com/article",
                    }
                ]
            }
        }
        result = query_brave_search("curl", api_key="test-key")
        assert result.severity == RiskLevel.LOW
        assert len(result.findings) == 1


class TestGatherThreatIntel:
    @patch("smart_upgrade.analysis.threat_intel.query_nvd")
    @patch("smart_upgrade.analysis.threat_intel.query_osv")
    @patch("smart_upgrade.analysis.threat_intel.query_brave_search")
    def test_all_enabled(self, mock_brave, mock_osv, mock_nvd):
        mock_brave.return_value = MagicMock(source="brave_search")
        mock_osv.return_value = MagicMock(source="osv")
        mock_nvd.return_value = MagicMock(source="nvd")

        results = gather_threat_intel(
            "curl", ecosystem="Debian",
            brave_api_key="key", nvd_api_key="key",
        )
        assert len(results) == 3

    @patch("smart_upgrade.analysis.threat_intel.query_nvd")
    @patch("smart_upgrade.analysis.threat_intel.query_osv")
    def test_brave_disabled(self, mock_osv, mock_nvd):
        mock_osv.return_value = MagicMock(source="osv")
        mock_nvd.return_value = MagicMock(source="nvd")

        results = gather_threat_intel(
            "curl", ecosystem="Debian",
            enable_brave=False,
        )
        assert len(results) == 2

    def test_brave_skipped_without_key(self):
        with patch("smart_upgrade.analysis.threat_intel.query_osv") as mock_osv, \
             patch("smart_upgrade.analysis.threat_intel.query_nvd") as mock_nvd:
            mock_osv.return_value = MagicMock(source="osv")
            mock_nvd.return_value = MagicMock(source="nvd")

            results = gather_threat_intel(
                "curl", ecosystem="Debian",
                brave_api_key=None,
                enable_brave=True,
            )
            # Brave enabled but no key => skipped
            assert len(results) == 2
