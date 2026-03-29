"""Base protocol that every package-manager adapter must implement.

Using :class:`typing.Protocol` so that adapters are duck-typed — no
inheritance required, just match the method signatures.
"""

from __future__ import annotations

import subprocess
from typing import Protocol

from smart_upgrade.models import PendingUpgrade


class PackageManagerAdapter(Protocol):
    """Interface contract for a package-manager adapter."""

    @property
    def name(self) -> str:
        """Human-readable name shown in the UI (e.g. ``"Homebrew"``)."""
        ...

    def refresh_index(self) -> None:
        """Update the local package index.

        For APT this runs ``sudo apt update``; for Brew, ``brew update``.
        """
        ...

    def list_upgradable(self) -> list[PendingUpgrade]:
        """Return every package that has a newer version available."""
        ...

    def upgrade(self, packages: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        """Perform the actual upgrade.

        Parameters
        ----------
        packages:
            When provided, only upgrade these specific packages.
            When *None*, upgrade everything.
        """
        ...

    def get_package_info(self, package_name: str) -> dict[str, str]:
        """Return metadata about a package.

        The returned dict should include keys like ``maintainer``,
        ``homepage``, and ``source_repo`` where available.
        """
        ...
