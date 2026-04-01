"""Changelog / diff retrieval for pending upgrades.

This module calls the appropriate adapter method to retrieve changelog data
and formats it for Claude analysis.
"""

from __future__ import annotations

import logging

from smart_upgrade.models import PackageSource, PendingUpgrade

logger = logging.getLogger(__name__)


def get_changelog(package: PendingUpgrade, adapter: object) -> str:
    """Retrieve changelog text for *package* using the given *adapter*.

    Parameters
    ----------
    package:
        The pending upgrade to look up.
    adapter:
        A package-manager adapter instance (AptAdapter or BrewAdapter) that
        has a ``get_changelog(package_name)`` method.

    Returns
    -------
    str
        The changelog text, or a placeholder message if retrieval fails.
    """
    if not hasattr(adapter, "get_changelog"):
        return "(changelog retrieval not supported by this adapter)"

    try:
        text = adapter.get_changelog(package.name)
    except Exception as exc:
        logger.warning("Changelog retrieval failed for %s: %s", package.name, exc)
        return f"(changelog retrieval failed: {exc})"

    if not text:
        return "(no changelog available)"

    return text


def format_changelog_for_prompt(
    package: PendingUpgrade,
    changelog_text: str,
) -> dict[str, str]:
    """Build the template variables for the Layer C prompt.

    Returns a dict with keys matching the ``{{...}}`` placeholders in
    ``prompts/layer_c_changelog.txt``.
    """
    source_label = {
        PackageSource.APT: "APT (Debian/Ubuntu)",
        PackageSource.BREW_FORMULA: "Homebrew formula",
        PackageSource.BREW_CASK: "Homebrew cask",
        PackageSource.NPM: "npm",
    }.get(package.source, str(package.source))

    return {
        "package_name": package.name,
        "old_version": package.current_version,
        "new_version": package.new_version,
        "package_manager": source_label,
        "changelog_content": changelog_text,
    }
