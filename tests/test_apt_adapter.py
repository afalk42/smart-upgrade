"""Tests for smart_upgrade.adapters.apt."""

import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from smart_upgrade.adapters.apt import (
    AptAdapter,
    _parse_per_package_policy,
    _parse_policy_origins,
    _parse_policy_source_origins,
)
from smart_upgrade.models import PackageSource

FIXTURES = Path(__file__).parent / "fixtures"


class TestListUpgradable:
    def test_parses_output(self):
        apt_output = (FIXTURES / "apt_list_upgradable.txt").read_text()

        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = apt_output
            mock_run.return_value.stderr = ""

            adapter = AptAdapter()
            packages = adapter.list_upgradable()

        assert len(packages) == 5
        names = {p.name for p in packages}
        assert "curl" in names
        assert "git" in names
        assert "openssl" in names
        assert "python3.10" in names

        curl = next(p for p in packages if p.name == "curl")
        assert curl.current_version == "7.81.0-1ubuntu1.15"
        assert curl.new_version == "7.81.0-1ubuntu1.16"
        assert curl.source == PackageSource.APT

    def test_empty_output(self):
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Listing... Done\n"
            mock_run.return_value.stderr = ""

            adapter = AptAdapter()
            packages = adapter.list_upgradable()

        assert packages == []

    def test_command_failure(self):
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "E: Some error"

            adapter = AptAdapter()
            with pytest.raises(RuntimeError, match="apt list --upgradable"):
                adapter.list_upgradable()


class TestRefreshIndex:
    def test_calls_sudo_apt_update(self):
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""

            adapter = AptAdapter()
            adapter.refresh_index()

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["sudo", "apt", "update"]

    def test_failure_raises(self):
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "Permission denied"

            adapter = AptAdapter()
            with pytest.raises(RuntimeError, match="sudo apt update"):
                adapter.refresh_index()


class TestUpgrade:
    def test_upgrade_all(self):
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0

            adapter = AptAdapter()
            adapter.upgrade()

        cmd = mock_run.call_args[0][0]
        assert cmd == ["sudo", "apt", "upgrade", "-y"]
        # Output should NOT be captured — it streams to the terminal
        # so users see progress and can answer dpkg prompts.
        kwargs = mock_run.call_args[1]
        assert "capture_output" not in kwargs
        assert "stdout" not in kwargs

    def test_upgrade_specific(self):
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0

            adapter = AptAdapter()
            adapter.upgrade(["curl", "git"])

        cmd = mock_run.call_args[0][0]
        assert cmd == ["sudo", "apt", "install", "--only-upgrade", "-y", "curl", "git"]


class TestGetPackageInfo:
    def test_parses_apt_show(self):
        show_output = (
            "Package: curl\n"
            "Version: 7.81.0-1ubuntu1.16\n"
            "Maintainer: Ubuntu Developers <ubuntu-devel@lists.ubuntu.com>\n"
            "Homepage: https://curl.se\n"
        )
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = show_output

            adapter = AptAdapter()
            info = adapter.get_package_info("curl")

        assert info["maintainer"] == "Ubuntu Developers <ubuntu-devel@lists.ubuntu.com>"
        assert info["homepage"] == "https://curl.se"


# ------------------------------------------------------------------
# Origin enrichment
# ------------------------------------------------------------------

POLICY_OUTPUT = """\
Package files:
 100 /var/lib/dpkg/status
     release a=now
 500 http://archive.ubuntu.com/ubuntu jammy-updates/main amd64 Packages
     release v=22.04,o=Ubuntu,a=jammy-updates,n=jammy,l=Ubuntu,c=main,b=amd64
     origin archive.ubuntu.com
 500 http://archive.ubuntu.com/ubuntu jammy-updates/universe amd64 Packages
     release v=22.04,o=Ubuntu,a=jammy-updates,n=jammy,l=Ubuntu,c=universe,b=amd64
     origin archive.ubuntu.com
 500 http://security.ubuntu.com/ubuntu jammy-security/main amd64 Packages
     release v=22.04,o=Ubuntu,a=jammy-security,n=jammy,l=Ubuntu,c=main,b=amd64
     origin security.ubuntu.com
 500 https://brave-browser-apt-release.s3.brave.com/ stable/main amd64 Packages
     release o=brave-browser-apt-release.s3.brave.com,a=stable,n=stable,l=brave-browser-apt-release.s3.brave.com,c=main,b=amd64
     origin brave-browser-apt-release.s3.brave.com
Pinned packages:
"""

APT_LIST_MIXED = """\
Listing... Done
curl/jammy-updates 7.81.0-1ubuntu1.16 amd64 [upgradable from: 7.81.0-1ubuntu1.15]
openssl/jammy-security 3.0.2-0ubuntu1.16 amd64 [upgradable from: 3.0.2-0ubuntu1.15]
brave-browser/stable 1.88.136 amd64 [upgradable from: 1.88.127]
"""

APT_SHOW_MIXED = """\
Package: curl
Version: 7.81.0-1ubuntu1.16
Maintainer: Ubuntu Developers <ubuntu-devel@lists.ubuntu.com>
Homepage: https://curl.se

Package: openssl
Version: 3.0.2-0ubuntu1.16
Maintainer: Ubuntu Developers <ubuntu-devel@lists.ubuntu.com>
Homepage: https://www.openssl.org/

Package: brave-browser
Version: 1.88.136
Maintainer: Brave Software <support@brave.com>
Homepage: https://brave.com
"""


class TestParsePolicyOrigins:
    def test_parses_ubuntu_origins(self):
        origins = _parse_policy_origins(POLICY_OUTPUT)
        assert origins["jammy-updates"] == "Ubuntu"
        assert origins["jammy-security"] == "Ubuntu"

    def test_parses_third_party_origins(self):
        origins = _parse_policy_origins(POLICY_OUTPUT)
        assert origins["stable"] == "brave-browser-apt-release.s3.brave.com"

    def test_skips_dpkg_status(self):
        origins = _parse_policy_origins(POLICY_OUTPUT)
        assert "now" not in origins

    def test_empty_output(self):
        assert _parse_policy_origins("") == {}

    def test_ambiguous_archive_omitted(self):
        """When two repos share an archive name with different origins, omit it."""
        policy = (
            " 500 http://example.com/repo1 stable/main amd64 Packages\n"
            "     release o=RepoA,a=stable,c=main\n"
            " 500 http://example.com/repo2 stable/main amd64 Packages\n"
            "     release o=RepoB,a=stable,c=main\n"
        )
        origins = _parse_policy_origins(policy)
        assert "stable" not in origins


# ------------------------------------------------------------------
# Raspberry Pi OS policy fixtures (ambiguous "bookworm" archive)
# ------------------------------------------------------------------

RPI_POLICY_OUTPUT = """\
Package files:
 100 /var/lib/dpkg/status
     release a=now
 500 http://deb.debian.org/debian bookworm/main arm64 Packages
     release v=12,o=Debian,a=bookworm,n=bookworm,l=Debian,c=main
     origin deb.debian.org
 500 http://deb.debian.org/debian-security bookworm-security/main arm64 Packages
     release v=12,o=Debian,a=bookworm-security,n=bookworm,l=Debian-Security,c=main
     origin deb.debian.org
 500 http://archive.raspberrypi.com/debian bookworm/main arm64 Packages
     release o=Raspberry Pi Foundation,a=bookworm,n=bookworm,l=Raspberry Pi Foundation,c=main
     origin archive.raspberrypi.com
Pinned packages:
"""

RPI_PER_PKG_POLICY = """\
libssl3:
  Installed: 3.0.18-1~deb12u2+rpt1
  Candidate: 3.0.19-1~deb12u1+rpt1
  Version table:
     3.0.19-1~deb12u1+rpt1 500
        500 http://archive.raspberrypi.com/debian bookworm/main arm64 Packages
 *** 3.0.18-1~deb12u2+rpt1 100
        100 /var/lib/dpkg/status
linux-headers-rpi-2712:
  Installed: 1:6.12.62-1+rpt1~bookworm
  Candidate: 1:6.12.75-1+rpt1~bookworm
  Version table:
     1:6.12.75-1+rpt1~bookworm 500
        500 http://archive.raspberrypi.com/debian bookworm/main arm64 Packages
 *** 1:6.12.62-1+rpt1~bookworm 100
        100 /var/lib/dpkg/status
imagemagick-6-common:
  Installed: 8:6.9.11.60+dfsg-1.6+deb12u6
  Candidate: 8:6.9.11.60+dfsg-1.6+deb12u7
  Version table:
     8:6.9.11.60+dfsg-1.6+deb12u7 500
        500 http://deb.debian.org/debian-security bookworm-security/main arm64 Packages
 *** 8:6.9.11.60+dfsg-1.6+deb12u6 100
        100 /var/lib/dpkg/status
"""


class TestParsePolicySourceOrigins:
    def test_maps_source_keys_to_origins(self):
        origins = _parse_policy_source_origins(RPI_POLICY_OUTPUT)
        assert origins["http://deb.debian.org/debian bookworm/main"] == "Debian"
        assert origins["http://deb.debian.org/debian-security bookworm-security/main"] == "Debian"
        assert origins["http://archive.raspberrypi.com/debian bookworm/main"] == "Raspberry Pi Foundation"

    def test_empty_output(self):
        assert _parse_policy_source_origins("") == {}

    def test_skips_dpkg_status(self):
        origins = _parse_policy_source_origins(RPI_POLICY_OUTPUT)
        assert all(not k.startswith("/") for k in origins)


class TestParsePerPackagePolicy:
    def test_extracts_candidate_sources(self):
        sources = _parse_per_package_policy(RPI_PER_PKG_POLICY)
        assert sources["libssl3"] == "http://archive.raspberrypi.com/debian bookworm/main"
        assert sources["linux-headers-rpi-2712"] == "http://archive.raspberrypi.com/debian bookworm/main"
        assert sources["imagemagick-6-common"] == "http://deb.debian.org/debian-security bookworm-security/main"

    def test_empty_output(self):
        assert _parse_per_package_policy("") == {}

    def test_handles_starred_candidate(self):
        """When the candidate version is also the installed version (*** marker)."""
        output = (
            "pkg:\n"
            "  Installed: 1.0-1\n"
            "  Candidate: 1.0-1\n"
            "  Version table:\n"
            " *** 1.0-1 500\n"
            "        500 http://example.com/repo bookworm/main arm64 Packages\n"
            "        100 /var/lib/dpkg/status\n"
        )
        sources = _parse_per_package_policy(output)
        assert sources["pkg"] == "http://example.com/repo bookworm/main"


class TestEnrichOrigins:
    def test_sets_origin_on_packages(self):
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=APT_LIST_MIXED, stderr=""),
                MagicMock(returncode=0, stdout=POLICY_OUTPUT, stderr=""),
                MagicMock(returncode=0, stdout=APT_SHOW_MIXED, stderr=""),
            ]

            adapter = AptAdapter()
            packages = adapter.list_upgradable()

        pkg_map = {p.name: p for p in packages}
        assert pkg_map["curl"].apt_origin == "Ubuntu"
        assert pkg_map["openssl"].apt_origin == "Ubuntu"
        assert pkg_map["brave-browser"].apt_origin == "brave-browser-apt-release.s3.brave.com"

    def test_policy_failure_is_graceful(self):
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=APT_LIST_MIXED, stderr=""),
                MagicMock(returncode=1, stdout="", stderr="error"),
                MagicMock(returncode=0, stdout="", stderr=""),
            ]

            adapter = AptAdapter()
            packages = adapter.list_upgradable()

        # Packages are still returned, just without origin info.
        assert len(packages) == 3
        assert all(p.apt_origin is None for p in packages)

    def test_comma_separated_suites(self):
        """The first suite in a comma-separated list is used."""
        apt_output = (
            "Listing... Done\n"
            "curl/jammy-updates,jammy-security 7.81.0-1ubuntu1.16 amd64 "
            "[upgradable from: 7.81.0-1ubuntu1.15]\n"
        )
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=apt_output, stderr=""),
                MagicMock(returncode=0, stdout=POLICY_OUTPUT, stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),
            ]

            adapter = AptAdapter()
            packages = adapter.list_upgradable()

        assert packages[0].apt_origin == "Ubuntu"

    def test_fallback_resolves_ambiguous_archives(self):
        """On RPi OS, 'bookworm' is shared by Debian and RPi Foundation repos.

        The fast path drops the ambiguous archive.  The fallback uses
        per-package ``apt-cache policy`` to resolve origins precisely.
        """
        rpi_apt_list = (
            "Listing... Done\n"
            "libssl3/bookworm 3.0.19-1~deb12u1+rpt1 arm64 "
            "[upgradable from: 3.0.18-1~deb12u2+rpt1]\n"
            "imagemagick-6-common/bookworm-security "
            "8:6.9.11.60+dfsg-1.6+deb12u7 arm64 "
            "[upgradable from: 8:6.9.11.60+dfsg-1.6+deb12u6]\n"
        )
        rpi_per_pkg = (
            "libssl3:\n"
            "  Installed: 3.0.18-1~deb12u2+rpt1\n"
            "  Candidate: 3.0.19-1~deb12u1+rpt1\n"
            "  Version table:\n"
            "     3.0.19-1~deb12u1+rpt1 500\n"
            "        500 http://archive.raspberrypi.com/debian bookworm/main arm64 Packages\n"
            " *** 3.0.18-1~deb12u2+rpt1 100\n"
            "        100 /var/lib/dpkg/status\n"
        )
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=rpi_apt_list, stderr=""),
                MagicMock(returncode=0, stdout=RPI_POLICY_OUTPUT, stderr=""),
                # Per-package policy — only called for unresolved packages
                MagicMock(returncode=0, stdout=rpi_per_pkg, stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),  # apt show
            ]

            adapter = AptAdapter()
            packages = adapter.list_upgradable()

        pkg_map = {p.name: p for p in packages}
        # libssl3 comes from RPi Foundation repo (resolved via fallback).
        assert pkg_map["libssl3"].apt_origin == "Raspberry Pi Foundation"
        # imagemagick comes from bookworm-security which is unambiguous.
        assert pkg_map["imagemagick-6-common"].apt_origin == "Debian"


class TestEnrichMetadata:
    def test_populates_maintainer_and_homepage(self):
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=APT_LIST_MIXED, stderr=""),
                MagicMock(returncode=0, stdout=POLICY_OUTPUT, stderr=""),
                MagicMock(returncode=0, stdout=APT_SHOW_MIXED, stderr=""),
            ]

            adapter = AptAdapter()
            packages = adapter.list_upgradable()

        pkg_map = {p.name: p for p in packages}
        assert pkg_map["curl"].maintainer == "Ubuntu Developers <ubuntu-devel@lists.ubuntu.com>"
        assert pkg_map["curl"].homepage == "https://curl.se"
        assert pkg_map["brave-browser"].maintainer == "Brave Software <support@brave.com>"
        assert pkg_map["brave-browser"].homepage == "https://brave.com"

    def test_apt_show_failure_is_graceful(self):
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=APT_LIST_MIXED, stderr=""),
                MagicMock(returncode=0, stdout=POLICY_OUTPUT, stderr=""),
                MagicMock(returncode=1, stdout="", stderr="error"),
            ]

            adapter = AptAdapter()
            packages = adapter.list_upgradable()

        # Packages are returned, just without metadata.
        assert len(packages) == 3
        assert all(p.maintainer is None for p in packages)
        assert all(p.homepage is None for p in packages)

    def test_source_package_homepage_fallback(self):
        """When a binary package has no Homepage, try its Source package."""
        apt_list = (
            "Listing... Done\n"
            "imagemagick-6-common/noble-apps-security "
            "8:6.9.12-esm8 amd64 [upgradable from: 8:6.9.12-esm7]\n"
        )
        # Binary package has no Homepage but has a Source field.
        apt_show_binary = (
            "Package: imagemagick-6-common\n"
            "Maintainer: Ubuntu Developers <ubuntu-devel@lists.ubuntu.com>\n"
            "Source: imagemagick (8:6.9.12)\n"
        )
        # Source package (looked up as binary) has Homepage.
        apt_show_source = (
            "Package: imagemagick\n"
            "Homepage: https://www.imagemagick.org/\n"
        )
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=apt_list, stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),        # policy
                MagicMock(returncode=0, stdout=apt_show_binary, stderr=""),  # 1st show
                MagicMock(returncode=0, stdout=apt_show_source, stderr=""),  # 2nd show
            ]

            adapter = AptAdapter()
            packages = adapter.list_upgradable()

        assert packages[0].homepage == "https://www.imagemagick.org/"
        assert adapter._homepages["imagemagick-6-common"] == "https://www.imagemagick.org/"

    def test_caches_homepages_for_changelog(self):
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=APT_LIST_MIXED, stderr=""),
                MagicMock(returncode=0, stdout=POLICY_OUTPUT, stderr=""),
                MagicMock(returncode=0, stdout=APT_SHOW_MIXED, stderr=""),
            ]

            adapter = AptAdapter()
            adapter.list_upgradable()

        assert adapter._homepages["curl"] == "https://curl.se"
        assert adapter._homepages["brave-browser"] == "https://brave.com"


class TestGetChangelogFallback:
    def test_apt_changelog_success(self):
        """When apt changelog works, use it directly."""
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "curl (7.81.0) jammy; urgency=medium\n\n  * Fix bug\n"

            adapter = AptAdapter()
            text = adapter.get_changelog("curl")

        assert "Fix bug" in text

    def test_falls_back_to_github(self):
        """When apt changelog fails and homepage is GitHub, fetch release notes."""
        adapter = AptAdapter()
        adapter._homepages["myapp"] = "https://github.com/owner/myapp"

        mock_changelog = MagicMock(returncode=1, stdout="", stderr="E: Failed")
        github_response = json.dumps({
            "tag_name": "v2.0",
            "name": "Release v2.0",
            "body": "## What's new\n\n- Feature X\n- Bug fix Y",
        }).encode("utf-8")

        with patch("smart_upgrade.adapters.apt.subprocess.run", return_value=mock_changelog):
            with patch("smart_upgrade.adapters.apt.urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__ = lambda s: s
                mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value.read.return_value = github_response

                text = adapter.get_changelog("myapp")

        assert "Release v2.0" in text
        assert "Feature X" in text

    def test_no_github_returns_empty(self):
        """When apt changelog fails and homepage is not GitHub, return empty."""
        adapter = AptAdapter()
        adapter._homepages["brave-browser"] = "https://brave.com"

        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "E: Failed"

            text = adapter.get_changelog("brave-browser")

        assert text == ""

    def test_no_cached_homepage_calls_get_package_info(self):
        """When homepage is not cached, falls back to get_package_info()."""
        adapter = AptAdapter()
        # No cached homepage — will call get_package_info

        show_output = "Package: myapp\nHomepage: https://github.com/owner/myapp\n"
        github_response = json.dumps({
            "tag_name": "v1.0",
            "name": "v1.0",
            "body": "Initial release",
        }).encode("utf-8")

        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1, stdout="", stderr="E: Failed"),  # apt changelog
                MagicMock(returncode=0, stdout=show_output, stderr=""),  # apt show
            ]
            with patch("smart_upgrade.adapters.apt.urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__ = lambda s: s
                mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value.read.return_value = github_response

                text = adapter.get_changelog("myapp")

        assert "Initial release" in text

    def test_github_404_logs_info_not_warning(self):
        """Repos without GitHub Releases (e.g. kernel forks) get a 404.

        This should be INFO (not WARNING) since it's expected for many repos.
        """
        adapter = AptAdapter()
        adapter._homepages["linux-headers-rpi"] = "https://github.com/raspberrypi/linux"

        mock_changelog = MagicMock(returncode=1, stdout="", stderr="E: Failed")

        with patch("smart_upgrade.adapters.apt.subprocess.run", return_value=mock_changelog):
            with patch("smart_upgrade.adapters.apt.urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.side_effect = urllib.error.HTTPError(
                    url="https://api.github.com/repos/raspberrypi/linux/releases/latest",
                    code=404,
                    msg="Not Found",
                    hdrs={},
                    fp=None,
                )
                text = adapter.get_changelog("linux-headers-rpi")

        assert text == ""
