"""Tests for smart_upgrade.analysis.engine — prompt rendering and helpers."""

from smart_upgrade.analysis.engine import (
    _finding_category_from_str,
    _load_prompt,
    _recommendation_from_str,
    _render,
    _risk_level_from_str,
)
from smart_upgrade.models import FindingCategory, Recommendation, RiskLevel


class TestRender:
    def test_simple_substitution(self):
        template = "Hello {{name}}, your version is {{version}}."
        result = _render(template, {"name": "curl", "version": "8.0"})
        assert result == "Hello curl, your version is 8.0."

    def test_no_placeholders(self):
        template = "No placeholders here."
        result = _render(template, {"key": "value"})
        assert result == "No placeholders here."

    def test_multiple_occurrences(self):
        template = "{{pkg}} and {{pkg}} again"
        result = _render(template, {"pkg": "curl"})
        assert result == "curl and curl again"


class TestRiskLevelFromStr:
    def test_valid(self):
        assert _risk_level_from_str("clear") == RiskLevel.CLEAR
        assert _risk_level_from_str("CRITICAL") == RiskLevel.CRITICAL
        assert _risk_level_from_str("Medium") == RiskLevel.MEDIUM

    def test_invalid(self):
        assert _risk_level_from_str("unknown") == RiskLevel.CLEAR
        assert _risk_level_from_str("") == RiskLevel.CLEAR


class TestRecommendationFromStr:
    def test_valid(self):
        assert _recommendation_from_str("proceed") == Recommendation.PROCEED
        assert _recommendation_from_str("Block") == Recommendation.BLOCK

    def test_invalid(self):
        assert _recommendation_from_str("nope") == Recommendation.PROCEED


class TestFindingCategoryFromStr:
    def test_valid(self):
        assert _finding_category_from_str("known_cve") == FindingCategory.KNOWN_CVE
        assert _finding_category_from_str("suspicious_code") == FindingCategory.SUSPICIOUS_CODE

    def test_invalid(self):
        assert _finding_category_from_str("something_else") == FindingCategory.OTHER


class TestLoadPrompt:
    def test_layer_a(self):
        text = _load_prompt("layer_a_review.txt")
        assert "{{pending_upgrades_json}}" in text
        assert "supply-chain" in text.lower()

    def test_layer_b(self):
        text = _load_prompt("layer_b_threat_intel.txt")
        assert "{{package_name}}" in text
        assert "{{brave_results}}" in text

    def test_layer_c(self):
        text = _load_prompt("layer_c_changelog.txt")
        assert "{{changelog_content}}" in text
