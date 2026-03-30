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
    r"^(?P<name>[^/]+)/(?P<suites>\S+)\s+(?P<new>\S+)\s+\S+"
    r"\s+\[upgradable from:\s+(?P<old>\S+)\]",
)

# Matches the "release" line in `apt-cache policy` output, e.g.:
#   release v=22.04,o=Ubuntu,a=jammy-updates,n=jammy,l=Ubuntu,c=main,b=amd64
_RELEASE_LINE_RE = re.compile(r"^\s+release\s+(.+)$")


def _parse_policy_origins(policy_output: str) -> dict[str, str]:
    """Parse ``apt-cache policy`` output to map archive names to origin labels.

    Returns a dict mapping archive name (e.g. ``"jammy-updates"``) to origin
    label (e.g. ``"Ubuntu"``).  Only unambiguous mappings are returned — if
    two repositories share the same archive name but have different origins,
    that archive is omitted.
    """
    archive_origins: dict[str, set[str]] = {}

    for line in policy_output.splitlines():
        m = _RELEASE_LINE_RE.match(line)
        if not m:
            continue

        release_info = m.group(1)
        archive: str | None = None
        origin: str | None = None

        for field in release_info.split(","):
            key, _, value = field.strip().partition("=")
            if key == "o" and value:
                origin = value
            elif key == "a" and value:
                archive = value

        if archive and origin and archive != "now":
            archive_origins.setdefault(archive, set()).add(origin)

    # Only return unambiguous mappings (one origin per archive).
    return {
        archive: next(iter(origins))
        for archive, origins in archive_origins.items()
        if len(origins) == 1
    }


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
        suites: dict[str, str] = {}
        for line in result.stdout.splitlines():
            m = _UPGRADABLE_RE.match(line)
            if m:
                name = m.group("name")
                upgrades.append(
                    PendingUpgrade(
                        name=name,
                        current_version=m.group("old"),
                        new_version=m.group("new"),
                        source=PackageSource.APT,
                    )
                )
                # Take the first suite (primary source) from comma-separated list.
                suites[name] = m.group("suites").split(",")[0]

        self._enrich_origins(upgrades, suites)
        return upgrades

    # ------------------------------------------------------------------
    # Origin enrichment
    # ------------------------------------------------------------------

    def _enrich_origins(
        self,
        packages: list[PendingUpgrade],
        suites: dict[str, str],
    ) -> None:
        """Set :attr:`apt_origin` on each package by resolving suite → origin.

        Runs ``apt-cache policy`` once to build a mapping from APT archive
        names to repository origin labels (e.g. ``"Ubuntu"``, ``"Debian"``).
        """
        try:
            result = subprocess.run(
                ["apt-cache", "policy"],
                capture_output=True,
                text=True,
                check=False,
                env={"LANG": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            )
            if result.returncode != 0:
                logger.debug(
                    "apt-cache policy failed (exit %d), skipping origin enrichment",
                    result.returncode,
                )
                return

            archive_origins = _parse_policy_origins(result.stdout)

            for pkg in packages:
                suite = suites.get(pkg.name)
                if suite and suite in archive_origins:
                    pkg.apt_origin = archive_origins[suite]
        except Exception:
            logger.debug("Failed to enrich APT origins", exc_info=True)

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
