"""APT adapter for Debian / Ubuntu systems.

All commands that require root are prefixed with ``sudo`` — the tool itself
is never run as root.  ``apt list --upgradable`` and ``apt show`` work fine
as a regular user.
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

    # Matches "https://github.com/<owner>/<repo>/..." URLs.
    _GITHUB_RE = re.compile(r"https://github\.com/([^/]+)/([^/]+)")

    def __init__(self) -> None:
        # Cache of package name → homepage URL, populated by _enrich_metadata()
        # and used by get_changelog() for the GitHub release notes fallback.
        self._homepages: dict[str, str] = {}

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
        self._enrich_metadata(upgrades)
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
    # Metadata enrichment
    # ------------------------------------------------------------------

    def _enrich_metadata(self, packages: list[PendingUpgrade]) -> None:
        """Fill in maintainer and homepage via a batched ``apt show`` call.

        Similar to the Homebrew adapter's ``_enrich_metadata()`` which uses
        ``brew info --json=v2``.  Homepages are also cached for use by
        :meth:`get_changelog`'s GitHub release notes fallback.
        """
        if not packages:
            return
        try:
            names = [p.name for p in packages]
            result = subprocess.run(
                ["apt", "show", *names],
                capture_output=True,
                text=True,
                check=False,
                env={"LANG": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            )

            # Parse whatever output we got — apt show may return non-zero
            # when some packages are unknown but still prints data for others.
            pkg_info: dict[str, dict[str, str]] = {}
            current_name: str | None = None

            for line in result.stdout.splitlines():
                if line.startswith("Package:"):
                    current_name = line.split(":", 1)[1].strip()
                    pkg_info.setdefault(current_name, {})
                elif current_name:
                    if line.startswith("Maintainer:"):
                        pkg_info[current_name].setdefault(
                            "maintainer", line.split(":", 1)[1].strip(),
                        )
                    elif line.startswith("Homepage:"):
                        pkg_info[current_name].setdefault(
                            "homepage", line.split(":", 1)[1].strip(),
                        )

            for pkg in packages:
                info = pkg_info.get(pkg.name, {})
                if info.get("maintainer") and pkg.maintainer is None:
                    pkg.maintainer = info["maintainer"]
                if info.get("homepage") and pkg.homepage is None:
                    pkg.homepage = info["homepage"]
                # Cache homepage for changelog fallback.
                if info.get("homepage"):
                    self._homepages[pkg.name] = info["homepage"]
        except Exception:
            logger.debug("Failed to enrich APT metadata", exc_info=True)

    # ------------------------------------------------------------------
    # Upgrade
    # ------------------------------------------------------------------

    def upgrade(self, packages: list[str] | None = None) -> subprocess.CompletedProcess[bytes]:
        """Run ``sudo apt upgrade`` (or install specific packages).

        Output is streamed live to the terminal so the user can see
        download/install progress and respond to interactive ``dpkg``
        prompts (e.g. config-file conflict questions).

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
        return subprocess.run(cmd, check=False)

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
        """Retrieve changelog text for a package.

        Strategy:

        1. Try ``apt changelog`` — works for official Debian/Ubuntu packages.
        2. If that fails (typical for third-party packages), check whether
           the package's homepage is on GitHub and fetch release notes via
           the GitHub API (same approach as the Homebrew adapter).
        3. Return an empty string if neither source is available.
        """
        result = subprocess.run(
            ["apt", "changelog", package_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Return only the first ~200 lines to keep prompts manageable.
            lines = result.stdout.splitlines()
            return "\n".join(lines[:200])

        # apt changelog failed — try GitHub release notes as fallback.
        logger.debug(
            "apt changelog unavailable for %s, trying GitHub fallback",
            package_name,
        )
        homepage = self._homepages.get(package_name, "")
        if not homepage:
            info = self.get_package_info(package_name)
            homepage = info.get("homepage", "")

        m = self._GITHUB_RE.match(homepage)
        if m:
            owner, repo = m.group(1), m.group(2)
            return self._fetch_github_release_notes(owner, repo, package_name)

        logger.info(
            "No changelog available for %s (not in APT repos and no GitHub homepage)",
            package_name,
        )
        return ""

    def _fetch_github_release_notes(
        self, owner: str, repo: str, package_name: str,
    ) -> str:
        """Fetch the latest release notes from GitHub for *owner/repo*.

        Tries the ``/releases/latest`` endpoint.  Returns the release body
        (Markdown) or an empty string on failure.
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
            logger.warning(
                "GitHub release notes fetch failed for %s: %s", package_name, exc,
            )
            return ""

        tag = data.get("tag_name", "")
        name = data.get("name", "")
        body = data.get("body", "")

        if not body:
            logger.info(
                "GitHub release %s for %s has no body text", tag, package_name,
            )
            return ""

        header = f"GitHub Release: {name or tag}\n\n"
        max_len = 4000
        if len(body) > max_len:
            body = body[:max_len] + "\n\n... (truncated)"

        return header + body
