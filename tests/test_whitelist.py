"""Tests for smart_upgrade.whitelist."""

from smart_upgrade.config import WhitelistConfig
from smart_upgrade.models import PackageSource, PendingUpgrade
from smart_upgrade.whitelist import (
    format_whitelist_display,
    is_whitelisted,
    partition_packages,
)


def _pkg(
    name: str,
    source: PackageSource = PackageSource.APT,
    *,
    apt_origin: str | None = None,
) -> PendingUpgrade:
    return PendingUpgrade(
        name=name, current_version="1.0", new_version="2.0",
        source=source, apt_origin=apt_origin,
    )


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


class TestOriginWhitelist:
    def test_trusted_origin_matches(self):
        wl = WhitelistConfig(apt_trusted_origins=["Ubuntu"])
        assert is_whitelisted(_pkg("curl", apt_origin="Ubuntu"), wl) is True

    def test_untrusted_origin_no_match(self):
        wl = WhitelistConfig(apt_trusted_origins=["Ubuntu"])
        pkg = _pkg("brave-browser", apt_origin="brave-browser-apt-release.s3.brave.com")
        assert is_whitelisted(pkg, wl) is False

    def test_no_origin_no_match(self):
        wl = WhitelistConfig(apt_trusted_origins=["Ubuntu"])
        assert is_whitelisted(_pkg("curl"), wl) is False

    def test_empty_trusted_origins(self):
        wl = WhitelistConfig(apt_trusted_origins=[])
        assert is_whitelisted(_pkg("curl", apt_origin="Ubuntu"), wl) is False

    def test_name_match_takes_priority(self):
        """Name-based whitelist works even without origin."""
        wl = WhitelistConfig(apt=["curl"], apt_trusted_origins=[])
        assert is_whitelisted(_pkg("curl"), wl) is True

    def test_origin_and_name_both_work(self):
        """Both matching paths lead to whitelisting."""
        wl = WhitelistConfig(apt=["curl"], apt_trusted_origins=["Ubuntu"])
        assert is_whitelisted(_pkg("curl", apt_origin="Ubuntu"), wl) is True

    def test_multiple_trusted_origins(self):
        wl = WhitelistConfig(apt_trusted_origins=["Ubuntu", "Debian"])
        assert is_whitelisted(_pkg("curl", apt_origin="Ubuntu"), wl) is True
        assert is_whitelisted(_pkg("git", apt_origin="Debian"), wl) is True
        assert is_whitelisted(_pkg("foo", apt_origin="Other"), wl) is False

    def test_brew_unaffected_by_apt_trusted_origins(self):
        wl = WhitelistConfig(apt_trusted_origins=["Ubuntu"])
        pkg = _pkg("curl", PackageSource.BREW_FORMULA)
        assert is_whitelisted(pkg, wl) is False

    def test_partition_with_origins(self):
        wl = WhitelistConfig(apt_trusted_origins=["Ubuntu"])
        packages = [
            _pkg("curl", apt_origin="Ubuntu"),
            _pkg("git", apt_origin="Ubuntu"),
            _pkg("brave-browser", apt_origin="brave-apt.s3.brave.com"),
        ]
        whitelisted, non_whitelisted, names = partition_packages(packages, wl)
        assert len(whitelisted) == 2
        assert len(non_whitelisted) == 1
        assert "curl" in names
        assert "git" in names
        assert "brave-browser" not in names


class TestFormatWhitelistDisplay:
    def test_populated(self):
        wl = WhitelistConfig(apt=["curl", "git"], brew=["node"], brew_cask=[])
        display = format_whitelist_display(wl)
        assert "APT" in display
        assert "Homebrew Formulae" in display
        assert "Homebrew Casks" not in display  # Empty, not shown

    def test_trusted_origins_shown(self):
        wl = WhitelistConfig(apt_trusted_origins=["Ubuntu", "Debian"])
        display = format_whitelist_display(wl)
        assert "APT Trusted Origins" in display
        assert display["APT Trusted Origins"] == ["Debian", "Ubuntu"]  # sorted

    def test_empty(self):
        wl = WhitelistConfig()
        assert format_whitelist_display(wl) == {}
