"""Whitelist matching — determines which packages skip deep analysis.

Whitelisted packages still go through Layer A (the high-level list review)
but skip Layer B (threat intelligence) and Layer C (changelog review).
Patterns support shell-style globs via :mod:`fnmatch`.
"""

from __future__ import annotations

from fnmatch import fnmatch

from smart_upgrade.config import WhitelistConfig
from smart_upgrade.models import PackageSource, PendingUpgrade


def _matches_any(name: str, patterns: list[str]) -> bool:
    """Return True if *name* matches at least one glob pattern."""
    return any(fnmatch(name, pattern) for pattern in patterns)


def is_whitelisted(package: PendingUpgrade, whitelist: WhitelistConfig) -> bool:
    """Check whether *package* appears on the whitelist.

    Matching uses :func:`fnmatch.fnmatch` for name patterns and
    origin-based matching for APT packages whose repository origin
    (e.g. ``"Ubuntu"``, ``"Debian"``) is in ``apt_trusted_origins``.
    """
    if package.source == PackageSource.APT:
        if _matches_any(package.name, whitelist.apt):
            return True
        if (
            whitelist.apt_trusted_origins
            and package.apt_origin
            and package.apt_origin in whitelist.apt_trusted_origins
        ):
            return True
        return False
    elif package.source == PackageSource.BREW_FORMULA:
        return _matches_any(package.name, whitelist.brew)
    elif package.source == PackageSource.BREW_CASK:
        return _matches_any(package.name, whitelist.brew_cask)
    return False


def partition_packages(
    packages: list[PendingUpgrade],
    whitelist: WhitelistConfig,
) -> tuple[list[PendingUpgrade], list[PendingUpgrade], set[str]]:
    """Split packages into whitelisted and non-whitelisted groups.

    Returns
    -------
    tuple
        ``(whitelisted, non_whitelisted, whitelisted_names)``
    """
    whitelisted: list[PendingUpgrade] = []
    non_whitelisted: list[PendingUpgrade] = []
    whitelisted_names: set[str] = set()

    for pkg in packages:
        if is_whitelisted(pkg, whitelist):
            whitelisted.append(pkg)
            whitelisted_names.add(pkg.name)
        else:
            non_whitelisted.append(pkg)

    return whitelisted, non_whitelisted, whitelisted_names


def format_whitelist_display(whitelist: WhitelistConfig) -> dict[str, list[str]]:
    """Return the whitelist in a display-friendly format."""
    result: dict[str, list[str]] = {}
    if whitelist.apt:
        result["APT"] = sorted(whitelist.apt)
    if whitelist.apt_trusted_origins:
        result["APT Trusted Origins"] = sorted(whitelist.apt_trusted_origins)
    if whitelist.brew:
        result["Homebrew Formulae"] = sorted(whitelist.brew)
    if whitelist.brew_cask:
        result["Homebrew Casks"] = sorted(whitelist.brew_cask)
    return result
