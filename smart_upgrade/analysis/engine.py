"""Security analysis engine — orchestrates Layers A, B, and C.

The engine coordinates:
- **Layer A**: High-level package list review by Claude.
- **Layer B**: Threat intelligence gathering + Claude synthesis.
- **Layer C**: Changelog / diff review by Claude.

Results from all layers are merged into a single :class:`AnalysisResult`
per package.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from smart_upgrade.analysis.changelog import format_changelog_for_prompt, get_changelog
from smart_upgrade.analysis.claude_invoker import ClaudeInvoker
from smart_upgrade.analysis.threat_intel import gather_threat_intel
from smart_upgrade.config import Config
from smart_upgrade.models import (
    AnalysisResult,
    Finding,
    FindingCategory,
    PackageSource,
    PendingUpgrade,
    Recommendation,
    RiskLevel,
)

logger = logging.getLogger(__name__)

# Directory containing prompt templates (sibling to this file's package).
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_prompt(filename: str) -> str:
    """Read a prompt template from the ``prompts/`` directory."""
    path = _PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8")


def _render(template: str, variables: dict[str, str]) -> str:
    """Replace ``{{key}}`` placeholders in *template*."""
    result = template
    for key, value in variables.items():
        result = result.replace("{{" + key + "}}", value)
    return result


# Severity ordering for comparisons (higher index = more severe).
_RISK_ORDER = {
    RiskLevel.CLEAR: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}

_REC_ORDER = {
    Recommendation.PROCEED: 0,
    Recommendation.REVIEW: 1,
    Recommendation.BLOCK: 2,
}


def _risk_level_from_str(value: str) -> RiskLevel:
    """Convert a string to a RiskLevel, defaulting to CLEAR on bad input."""
    try:
        return RiskLevel(value.lower())
    except ValueError:
        return RiskLevel.CLEAR


def _recommendation_from_str(value: str) -> Recommendation:
    """Convert a string to a Recommendation, defaulting to PROCEED."""
    try:
        return Recommendation(value.lower())
    except ValueError:
        return Recommendation.PROCEED


def _more_severe_risk(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    """Return whichever risk level is more severe."""
    return a if _RISK_ORDER[a] >= _RISK_ORDER[b] else b


def _more_severe_rec(a: Recommendation, b: Recommendation) -> Recommendation:
    """Return whichever recommendation is more severe."""
    return a if _REC_ORDER[a] >= _REC_ORDER[b] else b


def _finding_category_from_str(value: str) -> FindingCategory:
    """Convert a string to a FindingCategory, defaulting to OTHER."""
    try:
        return FindingCategory(value.lower())
    except ValueError:
        return FindingCategory.OTHER


def _ecosystem_for_source(source: PackageSource) -> str:
    """Map a PackageSource to an OSV ecosystem name."""
    if source == PackageSource.APT:
        return "Debian"
    return "Homebrew"


def _parse_findings(raw_findings: list[dict[str, Any]]) -> list[Finding]:
    """Parse a list of finding dicts from Claude's JSON response."""
    findings: list[Finding] = []
    for f in raw_findings:
        findings.append(
            Finding(
                category=_finding_category_from_str(f.get("category", "other")),
                severity=_risk_level_from_str(f.get("severity", "clear")),
                description=f.get("description", ""),
                source=f.get("source", "unknown"),
                reference_url=f.get("reference_url"),
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class AnalysisEngine:
    """Orchestrates the three-layer security analysis.

    Parameters
    ----------
    config:
        The application configuration (model, timeouts, API keys, etc.).
    adapter:
        The active package-manager adapter (for changelog retrieval).
    progress_callback:
        Optional callable invoked with ``(stage: str, detail: str)``
        so the UI can display progress updates.
    """

    def __init__(
        self,
        config: Config,
        adapter: object,
        progress_callback: Any = None,
    ) -> None:
        self.config = config
        self.adapter = adapter
        self._progress = progress_callback or (lambda stage, detail: None)
        self._invoker = ClaudeInvoker(
            model=config.model,
            timeout=config.timeouts.claude_analysis,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        packages: list[PendingUpgrade],
        whitelisted_names: set[str],
    ) -> list[AnalysisResult]:
        """Run the full analysis pipeline and return per-package results.

        Parameters
        ----------
        packages:
            All pending upgrades.
        whitelisted_names:
            Set of package names that skip Layers B and C.
        """
        # Layer A — whole-list review
        self._progress("layer_a", "Reviewing package list with Claude...")
        layer_a = self._run_layer_a(packages)

        # Build initial results from Layer A.
        results: dict[str, AnalysisResult] = {}
        for pkg in packages:
            a_data = layer_a.get(pkg.name, {})
            results[pkg.name] = AnalysisResult(
                package_name=pkg.name,
                risk_level=_risk_level_from_str(a_data.get("risk_level", "clear")),
                findings=[
                    Finding(
                        category=FindingCategory.OTHER,
                        severity=RiskLevel.LOW,
                        description=flag,
                        source="layer_a_review",
                    )
                    for flag in a_data.get("flags", [])
                ],
                recommendation=Recommendation.PROCEED,
                details=a_data.get("notes", ""),
            )

        # Layers B & C — only for non-whitelisted packages.
        non_whitelisted = [p for p in packages if p.name not in whitelisted_names]

        for pkg in non_whitelisted:
            # Layer B — threat intelligence
            self._progress("layer_b", f"Querying threat intel for {pkg.name}...")
            b_result = self._run_layer_b(pkg)
            if b_result:
                r = results[pkg.name]
                r.findings.extend(b_result.get("findings_parsed", []))
                b_risk = _risk_level_from_str(b_result.get("risk_level", "clear"))
                r.risk_level = _more_severe_risk(r.risk_level, b_risk)
                b_rec = _recommendation_from_str(b_result.get("recommendation", "proceed"))
                r.recommendation = _more_severe_rec(r.recommendation, b_rec)
                if b_result.get("explanation"):
                    r.details += f"\n[Threat Intel] {b_result['explanation']}"

            # Layer C — changelog review
            self._progress("layer_c", f"Reviewing changelog for {pkg.name}...")
            c_result = self._run_layer_c(pkg)
            if c_result:
                r = results[pkg.name]
                r.findings.extend(c_result.get("findings_parsed", []))
                c_risk = _risk_level_from_str(c_result.get("risk_level", "clear"))
                r.risk_level = _more_severe_risk(r.risk_level, c_risk)
                c_rec = _recommendation_from_str(c_result.get("recommendation", "proceed"))
                r.recommendation = _more_severe_rec(r.recommendation, c_rec)
                if c_result.get("explanation"):
                    r.details += f"\n[Changelog] {c_result['explanation']}"

        # Clean up details whitespace.
        for r in results.values():
            r.details = r.details.strip()

        return list(results.values())

    # ------------------------------------------------------------------
    # Layer A
    # ------------------------------------------------------------------

    def _run_layer_a(self, packages: list[PendingUpgrade]) -> dict[str, dict]:
        """Run Layer A: send the full package list to Claude for review."""
        template = _load_prompt("layer_a_review.txt")

        upgrades_json = json.dumps(
            [
                {
                    "name": p.name,
                    "current_version": p.current_version,
                    "new_version": p.new_version,
                    "source": str(p.source),
                }
                for p in packages
            ],
            indent=2,
        )

        # Determine platform label.
        sources = {p.source for p in packages}
        if PackageSource.APT in sources:
            plat, pm = "Linux (Debian/Ubuntu)", "APT"
        else:
            plat, pm = "macOS", "Homebrew"

        prompt = _render(template, {
            "platform": plat,
            "package_manager": pm,
            "pending_upgrades_json": upgrades_json,
        })

        try:
            response = self._invoker.analyze(prompt)
        except Exception as exc:
            logger.error("Layer A failed: %s", exc)
            return {}

        # Index the per-package results by name.
        result_map: dict[str, dict] = {}
        for pkg_data in response.get("packages", []):
            name = pkg_data.get("name", "")
            if name:
                result_map[name] = pkg_data

        return result_map

    # ------------------------------------------------------------------
    # Layer B
    # ------------------------------------------------------------------

    def _run_layer_b(self, package: PendingUpgrade) -> dict[str, Any] | None:
        """Run Layer B: gather threat intel and send to Claude for synthesis."""
        ti_config = self.config.threat_intel

        intel_results = gather_threat_intel(
            package_name=package.name,
            ecosystem=_ecosystem_for_source(package.source),
            version=package.new_version,
            brave_api_key=ti_config.brave_search.api_key,
            nvd_api_key=ti_config.nvd.api_key,
            timeout=self.config.timeouts.threat_intel_query,
            enable_brave=ti_config.brave_search.enabled,
            enable_osv=ti_config.osv.enabled,
            enable_nvd=ti_config.nvd.enabled,
        )

        # Format results for the prompt.
        brave_text = "(not queried)"
        osv_text = "(not queried)"
        nvd_text = "(not queried)"

        for r in intel_results:
            formatted = "\n".join(r.findings) if r.findings else "(no findings)"
            if r.source == "brave_search":
                brave_text = formatted
            elif r.source == "osv":
                osv_text = formatted
            elif r.source == "nvd":
                nvd_text = formatted

        template = _load_prompt("layer_b_threat_intel.txt")

        source_label = {
            PackageSource.APT: "APT",
            PackageSource.BREW_FORMULA: "Homebrew",
            PackageSource.BREW_CASK: "Homebrew (cask)",
        }.get(package.source, str(package.source))

        prompt = _render(template, {
            "package_name": package.name,
            "old_version": package.current_version,
            "new_version": package.new_version,
            "package_manager": source_label,
            "brave_results": brave_text,
            "osv_results": osv_text,
            "nvd_results": nvd_text,
        })

        try:
            response = self._invoker.analyze(prompt)
        except Exception as exc:
            logger.error("Layer B failed for %s: %s", package.name, exc)
            return None

        # Parse findings into typed objects.
        response["findings_parsed"] = _parse_findings(response.get("findings", []))
        return response

    # ------------------------------------------------------------------
    # Layer C
    # ------------------------------------------------------------------

    def _run_layer_c(self, package: PendingUpgrade) -> dict[str, Any] | None:
        """Run Layer C: retrieve changelog and send to Claude for review."""
        changelog_text = get_changelog(package, self.adapter)

        if changelog_text.startswith("("):
            # Placeholder / error message — no real changelog to review.
            logger.info("Skipping Layer C for %s: %s", package.name, changelog_text)
            return None

        template = _load_prompt("layer_c_changelog.txt")
        variables = format_changelog_for_prompt(package, changelog_text)
        prompt = _render(template, variables)

        try:
            response = self._invoker.analyze(prompt)
        except Exception as exc:
            logger.error("Layer C failed for %s: %s", package.name, exc)
            return None

        response["findings_parsed"] = _parse_findings(response.get("findings", []))
        return response
