"""npm adapter for auditing global npm package upgrades.

Supports two modes:

* **Targeted mode** (``smart-upgrade --npm openclaw@latest``): resolves the
  full dependency tree via ``npm install --dry-run --json -g`` and diffs
  against the currently installed tree to flag new, changed, or removed
  transitive dependencies.
* **Global mode** (``smart-upgrade --npm``): uses ``npm outdated -g --json``
  to discover all outdated global packages.

npm requires no ``sudo``.  All read operations (``npm outdated``,
``npm view``, ``npm ls``) and ``npm install -g`` are run as the regular user.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import urllib.error
import urllib.request

from smart_upgrade.models import PackageSource, PendingUpgrade

logger = logging.getLogger(__name__)

# Matches "https://github.com/<owner>/<repo>" in repository URLs,
# including git+https:// and .git suffixes.
_GITHUB_RE = re.compile(r"(?:git\+)?https://github\.com/([^/]+)/([^/.]+)")

# Text-line patterns emitted by `npm install --dry-run`.
# npm prints these BEFORE the final JSON summary.
#   add <name> <version>
#   change <name> <old_version> => <new_version>
#   remove <name> <version>
_ADD_RE = re.compile(r"^add\s+(\S+)\s+(\S+)$")
_CHANGE_RE = re.compile(r"^change\s+(\S+)\s+(\S+)\s+=>\s+(\S+)$")
_REMOVE_RE = re.compile(r"^remove\s+(\S+)\s+(\S+)$")


class NpmAdapter:
    """Package-manager adapter wrapping the ``npm`` CLI for global packages."""

    def __init__(self, target_package: str | None = None) -> None:
        """
        Parameters
        ----------
        target_package:
            A package spec like ``"openclaw@latest"`` for targeted mode.
            When *None*, the adapter audits all outdated global packages.
        """
        self._target = target_package
        self._npm = self._find_npm()

    @property
    def name(self) -> str:
        return "npm"

    # ------------------------------------------------------------------
    # Locate npm
    # ------------------------------------------------------------------

    @staticmethod
    def _find_npm() -> str:
        """Return the path to the ``npm`` executable or raise."""
        path = shutil.which("npm")
        if path is None:
            raise RuntimeError(
                "npm is not installed or not on $PATH.  "
                "Install Node.js / npm and try again."
            )
        return path

    # ------------------------------------------------------------------
    # Index refresh
    # ------------------------------------------------------------------

    def refresh_index(self) -> None:
        """No-op — the npm registry is always live."""
        logger.info("npm registry is live; no index refresh needed")

    # ------------------------------------------------------------------
    # List upgradable packages
    # ------------------------------------------------------------------

    def list_upgradable(self) -> list[PendingUpgrade]:
        """Discover packages that would change on upgrade.

        In targeted mode, runs ``npm install -g <target> --dry-run --json``
        and diffs the result.  In global mode, runs ``npm outdated -g --json``.
        """
        if self._target:
            return self._list_targeted()
        return self._list_outdated()

    # --- targeted mode ------------------------------------------------

    def _list_targeted(self) -> list[PendingUpgrade]:
        """Dry-run install for a specific package and diff the tree.

        ``npm install -g <pkg> --dry-run`` emits text progress lines to
        stdout with the actual package changes, followed by a JSON summary
        that only contains counts (``added``, ``changed``, ``removed``).
        We parse the **text lines** — not the JSON — to extract details.

        Line formats::

            add <name> <version>
            change <name> <old_version> => <new_version>
            remove <name> <version>

        Lines where ``old_version == new_version`` are reinstalls (not
        real changes) and are filtered out.
        """
        assert self._target is not None

        result = subprocess.run(
            [self._npm, "install", "-g", self._target, "--dry-run"],
            capture_output=True,
            text=True,
            check=False,
        )

        upgrades = self._parse_dryrun_lines(result.stdout)

        if not upgrades and result.returncode != 0:
            stderr = result.stderr.strip() if result.stderr else ""
            raise RuntimeError(
                f"`npm install --dry-run` failed (exit {result.returncode})"
                + (f": {stderr}" if stderr else "")
            )

        # Enrich with metadata from the registry.
        self._enrich_metadata(upgrades)

        return upgrades

    # --- global mode --------------------------------------------------

    def _list_outdated(self) -> list[PendingUpgrade]:
        """List all outdated global packages via ``npm outdated``."""
        result = subprocess.run(
            [self._npm, "outdated", "-g", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )

        # npm outdated exits 1 when packages are outdated — that's OK.
        if not result.stdout.strip():
            return []

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Failed to parse `npm outdated` JSON: {exc}"
            ) from exc

        upgrades: list[PendingUpgrade] = []
        for name, info in data.items():
            current = info.get("current", "?")
            latest = info.get("latest", info.get("wanted", "?"))
            if current == latest:
                continue
            upgrades.append(PendingUpgrade(
                name=name,
                current_version=current,
                new_version=latest,
                source=PackageSource.NPM,
            ))

        self._enrich_metadata(upgrades)
        return upgrades

    # ------------------------------------------------------------------
    # Metadata enrichment
    # ------------------------------------------------------------------

    def _enrich_metadata(self, packages: list[PendingUpgrade]) -> None:
        """Fill in homepage, source_repo, and maintainer via ``npm view``."""
        for pkg in packages:
            if pkg.new_version in ("(new)", "(removed)"):
                version_spec = pkg.name
            else:
                version_spec = f"{pkg.name}@{pkg.new_version}"

            result = subprocess.run(
                [self._npm, "view", version_spec, "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                continue

            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                continue

            pkg.homepage = data.get("homepage")
            repo = data.get("repository")
            if isinstance(repo, dict):
                pkg.source_repo = repo.get("url", "")
            elif isinstance(repo, str):
                pkg.source_repo = repo

            # Use the _npmUser (publisher) as maintainer.
            npm_user = data.get("_npmUser")
            if isinstance(npm_user, dict):
                pkg.maintainer = npm_user.get("name")
            elif isinstance(npm_user, str):
                pkg.maintainer = npm_user

    # ------------------------------------------------------------------
    # Upgrade
    # ------------------------------------------------------------------

    def upgrade(self, packages: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        """Run ``npm install -g`` for the target or specific packages.

        In targeted mode the original target spec is used.  In global
        mode, each package is upgraded to its latest version.
        """
        if self._target and not packages:
            cmd = [self._npm, "install", "-g", self._target]
        elif packages:
            specs = [f"{p}@latest" for p in packages]
            cmd = [self._npm, "install", "-g", *specs]
        else:
            cmd = [self._npm, "update", "-g"]

        logger.info("Running: %s", " ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    # ------------------------------------------------------------------
    # Package metadata
    # ------------------------------------------------------------------

    def get_package_info(self, package_name: str) -> dict[str, str]:
        """Return metadata from ``npm view <package> --json``."""
        result = subprocess.run(
            [self._npm, "view", package_name, "--json"],
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

        info["homepage"] = data.get("homepage", "")
        repo = data.get("repository")
        if isinstance(repo, dict):
            info["source_repo"] = repo.get("url", "")
        elif isinstance(repo, str):
            info["source_repo"] = repo
        else:
            info["source_repo"] = ""

        npm_user = data.get("_npmUser")
        if isinstance(npm_user, dict):
            info["maintainer"] = npm_user.get("name", "")
        elif isinstance(npm_user, str):
            info["maintainer"] = npm_user
        else:
            info["maintainer"] = ""

        return info

    # ------------------------------------------------------------------
    # Changelog / release notes retrieval
    # ------------------------------------------------------------------

    def get_changelog(self, package_name: str) -> str:
        """Retrieve release notes for an npm package via the GitHub API.

        Strategy:
        1. Run ``npm view <package> --json`` to get the repository URL.
        2. If the repository is on GitHub, fetch the latest release notes
           via the GitHub Releases API.
        3. Fall back to an empty string if the repository is not on GitHub
           or the API call fails.
        """
        info = self.get_package_info(package_name)
        source_url = info.get("source_repo", "") or info.get("homepage", "")

        m = _GITHUB_RE.search(source_url)
        if not m:
            homepage = info.get("homepage", "")
            m = _GITHUB_RE.search(homepage)
        if not m:
            logger.info(
                "No GitHub URL found for npm package %s; cannot fetch release notes",
                package_name,
            )
            return ""

        owner, repo = m.group(1), m.group(2)
        return self._fetch_github_release_notes(owner, repo, package_name)

    def _fetch_github_release_notes(
        self, owner: str, repo: str, package_name: str
    ) -> str:
        """Fetch the latest release notes from GitHub for *owner/repo*."""
        url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "smart-upgrade",
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                logger.info(
                    "No GitHub releases found for %s (%s/%s)",
                    package_name, owner, repo,
                )
            else:
                logger.warning(
                    "GitHub release notes fetch failed for %s: %s",
                    package_name, exc,
                )
            return ""
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
            logger.warning(
                "GitHub release notes fetch failed for %s: %s",
                package_name, exc,
            )
            return ""

        tag = data.get("tag_name", "")
        name = data.get("name", "")
        body = data.get("body", "")

        if not body:
            logger.info(
                "GitHub release %s for %s has no body text", tag, package_name
            )
            return ""

        header = f"GitHub Release: {name or tag}\n\n"
        max_len = 4000
        if len(body) > max_len:
            body = body[:max_len] + "\n\n... (truncated)"

        return header + body

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_dryrun_lines(stdout: str) -> list[PendingUpgrade]:
        """Parse the text progress lines from ``npm install --dry-run``.

        Each line is one of::

            add <name> <version>
            change <name> <old_version> => <new_version>
            remove <name> <version>

        Lines where ``old_version == new_version`` (reinstalls with no
        actual change) are filtered out.  Other lines (warnings, the
        trailing JSON summary) are ignored.
        """
        upgrades: list[PendingUpgrade] = []

        for line in stdout.splitlines():
            line = line.strip()

            m = _ADD_RE.match(line)
            if m:
                upgrades.append(PendingUpgrade(
                    name=m.group(1),
                    current_version="(new)",
                    new_version=m.group(2),
                    source=PackageSource.NPM,
                ))
                continue

            m = _CHANGE_RE.match(line)
            if m:
                old_ver, new_ver = m.group(2), m.group(3)
                if old_ver != new_ver:
                    upgrades.append(PendingUpgrade(
                        name=m.group(1),
                        current_version=old_ver,
                        new_version=new_ver,
                        source=PackageSource.NPM,
                    ))
                continue

            m = _REMOVE_RE.match(line)
            if m:
                upgrades.append(PendingUpgrade(
                    name=m.group(1),
                    current_version=m.group(2),
                    new_version="(removed)",
                    source=PackageSource.NPM,
                ))

        return upgrades
