"""Threat intelligence clients for Brave Search, OSV.dev, and NVD.

All HTTP requests use :mod:`urllib.request` from the standard library to
avoid an external dependency on ``requests``.  Each client returns a
:class:`~smart_upgrade.models.ThreatIntelResult`.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from smart_upgrade.models import RiskLevel, ThreatIntelResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Generic HTTP helper
# ---------------------------------------------------------------------------

def _http_get(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    """Perform an HTTP GET and return the parsed JSON response."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Try to extract a structured error message from the response body.
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = body.get("error", {}).get("detail", "") or body.get("message", "")
        except Exception:
            pass
        if detail:
            logger.warning("HTTP GET %s failed (HTTP %d): %s", url, exc.code, detail)
        else:
            logger.warning("HTTP GET %s failed: HTTP %d %s", url, exc.code, exc.reason)
        return {}
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        logger.warning("HTTP GET %s failed: %s", url, exc)
        return {}


def _http_post_json(url: str, body: dict, headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    """Perform an HTTP POST with a JSON body and return the parsed response."""
    data = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body_resp = json.loads(exc.read().decode("utf-8"))
            detail = body_resp.get("error", {}).get("detail", "") or body_resp.get("message", "")
        except Exception:
            pass
        if detail:
            logger.warning("HTTP POST %s failed (HTTP %d): %s", url, exc.code, detail)
        else:
            logger.warning("HTTP POST %s failed: HTTP %d %s", url, exc.code, exc.reason)
        return {}
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        logger.warning("HTTP POST %s failed: %s", url, exc)
        return {}


# ---------------------------------------------------------------------------
# Brave Search
# ---------------------------------------------------------------------------

def query_brave_search(
    package_name: str,
    api_key: str,
    timeout: int = 30,
) -> ThreatIntelResult:
    """Search Brave for recent security news about *package_name*.

    The search query is crafted to surface supply-chain attack reports,
    compromised maintainers, and malicious package advisories.
    """
    query = f'"{package_name}" supply chain attack OR compromised OR malware OR vulnerability OR CVE'
    url = (
        f"https://api.search.brave.com/res/v1/web/search"
        f"?q={urllib.request.quote(query)}"
        f"&count=5"
    )

    data = _http_get(url, headers={"X-Subscription-Token": api_key, "Accept": "application/json"}, timeout=timeout)

    if not data:
        logger.warning(
            "Brave Search returned no data for %s. "
            "Check that BRAVE_SEARCH_API_KEY is set to a valid key.",
            package_name,
        )

    findings: list[str] = []
    results = data.get("web", {}).get("results", [])
    for r in results:
        title = r.get("title", "")
        description = r.get("description", "")
        result_url = r.get("url", "")
        findings.append(f"{title}: {description} ({result_url})")

    severity = RiskLevel.CLEAR
    if findings:
        severity = RiskLevel.LOW  # Presence of search results warrants review

    return ThreatIntelResult(
        source="brave_search",
        query=query,
        findings=findings,
        raw_data=data,
        severity=severity,
    )


# ---------------------------------------------------------------------------
# OSV.dev
# ---------------------------------------------------------------------------

# OSV ecosystem names that are known to work with the OSV.dev API.
# Homebrew is NOT a valid OSV ecosystem — brew packages should be skipped.
_VALID_OSV_ECOSYSTEMS = {"Debian", "Alpine", "PyPI", "npm", "crates.io", "Go", "Maven", "NuGet", "Packagist", "RubyGems"}


def query_osv(
    package_name: str,
    ecosystem: str = "Debian",
    version: str | None = None,
    timeout: int = 30,
) -> ThreatIntelResult:
    """Query OSV.dev for known vulnerabilities affecting *package_name*.

    Parameters
    ----------
    ecosystem:
        OSV ecosystem name.  ``"Debian"`` for apt packages.  Homebrew
        packages are not tracked by OSV — the caller should pass a
        valid ecosystem or this function will return an empty result.
    version:
        The new version being upgraded to.  If supplied, only vulns
        affecting that version are returned.
    """
    if ecosystem not in _VALID_OSV_ECOSYSTEMS:
        logger.info("OSV skipped for %s: ecosystem %r is not supported by OSV.dev", package_name, ecosystem)
        return ThreatIntelResult(
            source="osv",
            query=f"{package_name} ({ecosystem})",
            findings=[],
            raw_data={},
            severity=RiskLevel.CLEAR,
        )

    url = "https://api.osv.dev/v1/query"
    body: dict[str, Any] = {"package": {"name": package_name, "ecosystem": ecosystem}}
    if version:
        body["version"] = version

    data = _http_post_json(url, body, timeout=timeout)

    findings: list[str] = []
    vulns = data.get("vulns", [])
    for v in vulns:
        vuln_id = v.get("id", "unknown")
        summary = v.get("summary", "No summary available")
        severity_list = v.get("severity", [])
        sev_str = severity_list[0].get("score", "?") if severity_list else "?"
        findings.append(f"{vuln_id}: {summary} (CVSS: {sev_str})")

    severity = RiskLevel.CLEAR
    if vulns:
        severity = RiskLevel.MEDIUM  # Known vulnerabilities are at least medium

    return ThreatIntelResult(
        source="osv",
        query=f"{package_name} ({ecosystem})",
        findings=findings,
        raw_data=data,
        severity=severity,
    )


# ---------------------------------------------------------------------------
# NVD / CVE
# ---------------------------------------------------------------------------

def query_nvd(
    package_name: str,
    api_key: str | None = None,
    timeout: int = 30,
) -> ThreatIntelResult:
    """Query NIST NVD for CVEs matching *package_name*.

    Parameters
    ----------
    api_key:
        Optional NVD API key.  Without one, requests are rate-limited
        to ~5 per 30 seconds.
    """
    url = (
        f"https://services.nvd.nist.gov/rest/json/cves/2.0"
        f"?keywordSearch={urllib.request.quote(package_name)}"
        f"&resultsPerPage=5"
    )
    headers: dict[str, str] = {}
    if api_key:
        headers["apiKey"] = api_key

    data = _http_get(url, headers=headers, timeout=timeout)

    findings: list[str] = []
    vulns = data.get("vulnerabilities", [])
    for v in vulns:
        cve = v.get("cve", {})
        cve_id = cve.get("id", "unknown")
        descriptions = cve.get("descriptions", [])
        desc = next((d["value"] for d in descriptions if d.get("lang") == "en"), "No description")
        # Truncate long descriptions
        if len(desc) > 200:
            desc = desc[:200] + "..."
        findings.append(f"{cve_id}: {desc}")

    severity = RiskLevel.CLEAR
    if vulns:
        severity = RiskLevel.LOW

    return ThreatIntelResult(
        source="nvd",
        query=package_name,
        findings=findings,
        raw_data=data,
        severity=severity,
    )


# ---------------------------------------------------------------------------
# Convenience: query all enabled sources
# ---------------------------------------------------------------------------

def gather_threat_intel(
    package_name: str,
    ecosystem: str,
    version: str | None = None,
    brave_api_key: str | None = None,
    nvd_api_key: str | None = None,
    timeout: int = 30,
    enable_brave: bool = True,
    enable_osv: bool = True,
    enable_nvd: bool = True,
) -> list[ThreatIntelResult]:
    """Run all enabled threat-intelligence queries for a single package.

    Returns a list of :class:`ThreatIntelResult` — one per source that was
    queried.  Sources that are disabled or that fail gracefully are omitted.
    """
    results: list[ThreatIntelResult] = []

    if enable_brave and brave_api_key:
        results.append(query_brave_search(package_name, brave_api_key, timeout=timeout))
    elif enable_brave and not brave_api_key:
        logger.info("Brave Search skipped for %s: no API key configured", package_name)

    if enable_osv:
        results.append(query_osv(package_name, ecosystem=ecosystem, version=version, timeout=timeout))

    if enable_nvd:
        results.append(query_nvd(package_name, api_key=nvd_api_key, timeout=timeout))

    return results
