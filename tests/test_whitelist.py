"""Tests for smart_upgrade.whitelist."""

from smart_upgrade.config import WhitelistConfig
from smart_upgrade.models import PackageSource, PendingUpgrade
from smart_upgrade.whitelist import (
    format_whitelist_display,
    is_whitelisted,
    partition_packages,
)


def _pkg(name: str, source: PackageSource = PackageSource.APT) -> PendingUpgrade:
    return PendingUpgrade(name=name, current_version="1.0", new_version="2.0", source=source)


class TestIsWhitelisted:
    def test_exact_match_apt(self):
        wl = WhitelistConfig(apt=["curl", "git"])
        assert is_whitelisted(_pkg("curl"), wl) is True
        assert is_whitelisted(_pkg("wget"), wl) is False

    def test_glob_match_apt(self):
        wl = WhitelistConfig(apt=["linux-image-*"])
        assert is_whitelisted(_pkg("linux-image-6.1.0-generic"), wl) is True
        assert is_whitelisted(_pkg("linux-headers-6.1.0"), wl) is False

    def test_brew_formula(self):
        wl = WhitelistConfig(brew=["python@3.*"])
        pkg = _pkg("python@3.12", PackageSource.BREW_FORMULA)
        assert is_whitelisted(pkg, wl) is True

    def test_brew_cask(self):
        wl = WhitelistConfig(brew_cask=["firefox"])
        pkg = _pkg("firefox", PackageSource.BREW_CASK)
        assert is_whitelisted(pkg, wl) is True

    def test_empty_whitelist(self):
        wl = WhitelistConfig()
        assert is_whitelisted(_pkg("curl"), wl) is False


class TestPartitionPackages:
    def test_partition(self):
        wl = WhitelistConfig(apt=["curl", "git"])
        packages = [_pkg("curl"), _pkg("git"), _pkg("wget"), _pkg("openssl")]
        whitelisted, non_whitelisted, names = partition_packages(packages, wl)
        assert len(whitelisted) == 2
        assert len(non_whitelisted) == 2
        assert "curl" in names
        assert "git" in names
        assert "wget" not in names

    def test_all_whitelisted(self):
        wl = WhitelistConfig(apt=["*"])
        packages = [_pkg("curl"), _pkg("git")]
        whitelisted, non_whitelisted, names = partition_packages(packages, wl)
        assert len(whitelisted) == 2
        assert len(non_whitelisted) == 0

    def test_none_whitelisted(self):
        wl = WhitelistConfig()
        packages = [_pkg("curl")]
        whitelisted, non_whitelisted, names = partition_packages(packages, wl)
        assert len(whitelisted) == 0
        assert len(non_whitelisted) == 1


class TestFormatWhitelistDisplay:
    def test_populated(self):
        wl = WhitelistConfig(apt=["curl", "git"], brew=["node"], brew_cask=[])
        display = format_whitelist_display(wl)
        assert "APT" in display
        assert "Homebrew Formulae" in display
        assert "Homebrew Casks" not in display  # Empty, not shown

    def test_empty(self):
        wl = WhitelistConfig()
        assert format_whitelist_display(wl) == {}
