"""APT adapter for Debian / Ubuntu systems.

All commands that require root are prefixed with ``sudo`` — the tool itself
is never run as root.  ``apt list --upgradable`` and ``apt show`` work fine
as a regular user.
"""

from __future__ import annotations

import logging
import re
import subprocess

from smart_upgrade.models import PackageSource, PendingUpgrade

logger = logging.getLogger(__name__)

# Example line from `apt list --upgradable`:
#   curl/jammy-updates 7.81.0-1ubuntu1.16 amd64 [upgradable from: 7.81.0-1ubuntu1.15]
_UPGRADABLE_RE = re.compile(
    r"^(?P<name>[^/]+)/\S+\s+(?P<new>\S+)\s+\S+"
    r"\s+\[upgradable from:\s+(?P<old>\S+)\]",
)


class AptAdapter:
    """Package-manager adapter wrapping the ``apt`` CLI."""

    @property
    def name(self) -> str:
        return "APT"

    # ------------------------------------------------------------------
    # Index refresh
    # ------------------------------------------------------------------

    def refresh_index(self) -> None:
        """Run ``sudo apt update`` to refresh the package index."""
        logger.info("Running: sudo apt update")
        result = subprocess.run(
            ["sudo", "apt", "update"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"`sudo apt update` failed (exit {result.returncode}):\n{result.stderr.strip()}"
            )

    # ------------------------------------------------------------------
    # List upgradable packages
    # ------------------------------------------------------------------

    def list_upgradable(self) -> list[PendingUpgrade]:
        """Parse ``apt list --upgradable`` into a list of :class:`PendingUpgrade`."""
        result = subprocess.run(
            ["apt", "list", "--upgradable"],
            capture_output=True,
            text=True,
            check=False,
            env={"LANG": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"`apt list --upgradable` failed (exit {result.returncode}):\n{result.stderr.strip()}"
            )

        upgrades: list[PendingUpgrade] = []
        for line in result.stdout.splitlines():
            m = _UPGRADABLE_RE.match(line)
            if m:
                upgrades.append(
                    PendingUpgrade(
                        name=m.group("name"),
                        current_version=m.group("old"),
                        new_version=m.group("new"),
                        source=PackageSource.APT,
                    )
                )
        return upgrades

    # ------------------------------------------------------------------
    # Upgrade
    # ------------------------------------------------------------------

    def upgrade(self, packages: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        """Run ``sudo apt upgrade`` (or install specific packages).

        Parameters
        ----------
        packages:
            When supplied, only these packages are upgraded via
            ``sudo apt install --only-upgrade``.
        """
        if packages:
            cmd = ["sudo", "apt", "install", "--only-upgrade", "-y", *packages]
        else:
            cmd = ["sudo", "apt", "upgrade", "-y"]

        logger.info("Running: %s", " ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    # ------------------------------------------------------------------
    # Package metadata
    # ------------------------------------------------------------------

    def get_package_info(self, package_name: str) -> dict[str, str]:
        """Return metadata from ``apt show <package>``."""
        result = subprocess.run(
            ["apt", "show", package_name],
            capture_output=True,
            text=True,
            check=False,
        )
        info: dict[str, str] = {}
        if result.returncode != 0:
            return info

        for line in result.stdout.splitlines():
            if line.startswith("Maintainer:"):
                info["maintainer"] = line.split(":", 1)[1].strip()
            elif line.startswith("Homepage:"):
                info["homepage"] = line.split(":", 1)[1].strip()

        return info

    # ------------------------------------------------------------------
    # Changelog retrieval
    # ------------------------------------------------------------------

    def get_changelog(self, package_name: str) -> str:
        """Retrieve the Debian changelog for a package via ``apt changelog``.

        Returns the changelog text, or an empty string on failure.
        """
        result = subprocess.run(
            ["apt", "changelog", package_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.warning("Could not retrieve changelog for %s: %s", package_name, result.stderr.strip())
            return ""
        # Return only the first ~200 lines to keep prompts manageable.
        lines = result.stdout.splitlines()
        return "\n".join(lines[:200])
