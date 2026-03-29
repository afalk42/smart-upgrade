"""Homebrew adapter for macOS (formulae and casks).

Homebrew runs entirely without ``sudo``, so no privilege escalation is needed.
The ``brew outdated --json=v2`` command provides structured JSON output for
both formulae and casks in a single call.
"""

from __future__ import annotations

import json
import logging
import subprocess

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

        return upgrades

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
    # Changelog / formula diff retrieval
    # ------------------------------------------------------------------

    def get_changelog(self, package_name: str) -> str:
        """Retrieve recent git log for a Homebrew formula/cask.

        Uses ``brew log --oneline -20`` to get the last 20 formula commits.
        Returns the log text, or an empty string on failure.
        """
        result = subprocess.run(
            ["brew", "log", "--oneline", "-20", package_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.warning("Could not retrieve changelog for %s: %s", package_name, result.stderr.strip())
            return ""
        return result.stdout.strip()
