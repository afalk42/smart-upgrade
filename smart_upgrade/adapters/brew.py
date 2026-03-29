"""Homebrew adapter for macOS (formulae and casks).

Homebrew runs entirely without ``sudo``, so no privilege escalation is needed.
The ``brew outdated --json=v2`` command provides structured JSON output for
both formulae and casks in a single call.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import urllib.error
import urllib.request

from smart_upgrade.models import PackageSource, PendingUpgrade

logger = logging.getLogger(__name__)


class BrewAdapter:
    """Package-manager adapter wrapping the ``brew`` CLI."""

    @property
    def name(self) -> str:
        return "Homebrew"

    # ------------------------------------------------------------------
    # Index refresh
    # ------------------------------------------------------------------

    def refresh_index(self) -> None:
        """Run ``brew update`` to refresh the formulae/cask index."""
        logger.info("Running: brew update")
        result = subprocess.run(
            ["brew", "update"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"`brew update` failed (exit {result.returncode}):\n{result.stderr.strip()}"
            )

    # ------------------------------------------------------------------
    # List upgradable packages
    # ------------------------------------------------------------------

    def list_upgradable(self) -> list[PendingUpgrade]:
        """Parse ``brew outdated --json=v2`` into a list of :class:`PendingUpgrade`.

        The JSON output has two top-level keys: ``formulae`` and ``casks``.
        """
        result = subprocess.run(
            ["brew", "outdated", "--json=v2"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"`brew outdated` failed (exit {result.returncode}):\n{result.stderr.strip()}"
            )

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse `brew outdated` JSON: {exc}") from exc

        upgrades: list[PendingUpgrade] = []

        # --- formulae ---
        for f in data.get("formulae", []):
            name = f.get("name", "")
            current = f.get("installed_versions", ["?"])[0] if f.get("installed_versions") else "?"
            new_version = f.get("current_version", "?")
            upgrades.append(
                PendingUpgrade(
                    name=name,
                    current_version=current,
                    new_version=new_version,
                    source=PackageSource.BREW_FORMULA,
                    homepage=f.get("homepage"),
                )
            )

        # --- casks ---
        for c in data.get("casks", []):
            name = c.get("name", "")
            current = c.get("installed_versions", "?")
            # installed_versions for casks is a string, not a list
            if isinstance(current, list):
                current = current[0] if current else "?"
            new_version = c.get("current_version", "?")
            upgrades.append(
                PendingUpgrade(
                    name=name,
                    current_version=current,
                    new_version=new_version,
                    source=PackageSource.BREW_CASK,
                )
            )

        # Enrich with metadata from `brew info` (homepage, source repo, tap).
        self._enrich_metadata(upgrades)

        return upgrades

    def _enrich_metadata(self, packages: list[PendingUpgrade]) -> None:
        """Fill in homepage, source_repo, and maintainer via a single ``brew info`` call."""
        if not packages:
            return

        names = [p.name for p in packages]
        result = subprocess.run(
            ["brew", "info", "--json=v2", *names],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.warning("brew info failed; package metadata will be incomplete")
            return

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return

        # Build lookup dicts from the info response.
        formula_info: dict[str, dict] = {}
        for f in data.get("formulae", []):
            formula_info[f.get("name", "")] = f

        cask_info: dict[str, dict] = {}
        for c in data.get("casks", []):
            # Cask "token" is the identifier (e.g. "firefox").
            cask_info[c.get("token", c.get("name", ""))] = c

        for pkg in packages:
            if pkg.source == PackageSource.BREW_FORMULA:
                info = formula_info.get(pkg.name, {})
                pkg.homepage = info.get("homepage")
                pkg.source_repo = (
                    info.get("urls", {}).get("stable", {}).get("url")
                )
                pkg.maintainer = info.get("tap")
            elif pkg.source == PackageSource.BREW_CASK:
                info = cask_info.get(pkg.name, {})
                pkg.homepage = info.get("homepage")
                pkg.maintainer = info.get("tap")

    # ------------------------------------------------------------------
    # Upgrade
    # ------------------------------------------------------------------

    def upgrade(self, packages: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        """Run ``brew upgrade`` for all or specific packages.

        When *packages* is provided, each package is upgraded individually
        to handle mixed formulae / cask lists correctly.
        """
        if packages:
            cmd = ["brew", "upgrade", *packages]
        else:
            cmd = ["brew", "upgrade"]

        logger.info("Running: %s", " ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    # ------------------------------------------------------------------
    # Package metadata
    # ------------------------------------------------------------------

    def get_package_info(self, package_name: str) -> dict[str, str]:
        """Return metadata from ``brew info --json=v2 <package>``."""
        result = subprocess.run(
            ["brew", "info", "--json=v2", package_name],
            capture_output=True,
            text=True,
            check=False,
        )
        info: dict[str, str] = {}
        if result.returncode != 0:
            return info

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return info

        # Try formulae first, then casks.
        formulae = data.get("formulae", [])
        if formulae:
            f = formulae[0]
            info["homepage"] = f.get("homepage", "")
            info["source_repo"] = f.get("urls", {}).get("stable", {}).get("url", "")
            # Maintainer info is not directly available in brew JSON,
            # but we can note the tap.
            info["maintainer"] = f.get("tap", "")
            return info

        casks = data.get("casks", [])
        if casks:
            c = casks[0]
            info["homepage"] = c.get("homepage", "")
            info["maintainer"] = c.get("tap", "")
            return info

        return info

    # ------------------------------------------------------------------
    # Changelog / release notes retrieval
    # ------------------------------------------------------------------

    # Matches "https://github.com/<owner>/<repo>/..." URLs.
    _GITHUB_RE = re.compile(r"https://github\.com/([^/]+)/([^/]+)")

    def get_changelog(self, package_name: str) -> str:
        """Retrieve release notes for a Homebrew package.

        Strategy:
        1. Run ``brew info --json=v2`` to get the upstream source URL.
        2. If the source is on GitHub, fetch the release notes for the
           new version tag via the GitHub API.
        3. Fall back to an empty string if the source is not on GitHub
           or if the API call fails.

        This is more reliable than ``brew log`` which depends on the
        local Homebrew git history and often returns nothing on shallow
        clones.
        """
        # Step 1: get the source URL from brew info.
        info = self.get_package_info(package_name)
        source_url = info.get("source_repo", "") or info.get("homepage", "")

        m = self._GITHUB_RE.match(source_url)
        if not m:
            # Try the homepage as a fallback.
            homepage = info.get("homepage", "")
            m = self._GITHUB_RE.match(homepage)
        if not m:
            logger.info("No GitHub URL found for %s; cannot fetch release notes", package_name)
            return ""

        owner, repo = m.group(1), m.group(2)
        return self._fetch_github_release_notes(owner, repo, package_name)

    def _fetch_github_release_notes(self, owner: str, repo: str, package_name: str) -> str:
        """Fetch the latest release notes from GitHub for *owner/repo*.

        Tries the ``/releases/latest`` endpoint first. Returns the
        release body (Markdown) or an empty string on failure.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "smart-upgrade",
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
            logger.warning("GitHub release notes fetch failed for %s: %s", package_name, exc)
            return ""

        tag = data.get("tag_name", "")
        name = data.get("name", "")
        body = data.get("body", "")

        if not body:
            logger.info("GitHub release %s for %s has no body text", tag, package_name)
            return ""

        # Prefix with the release tag/name for context, then truncate.
        header = f"GitHub Release: {name or tag}\n\n"
        # Limit to ~4000 chars to keep Claude prompts manageable.
        max_len = 4000
        if len(body) > max_len:
            body = body[:max_len] + "\n\n... (truncated)"

        return header + body
