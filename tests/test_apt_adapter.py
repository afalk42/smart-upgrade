"""Tests for smart_upgrade.adapters.apt."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from smart_upgrade.adapters.apt import AptAdapter, _parse_policy_origins
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


class TestEnrichOrigins:
    def test_sets_origin_on_packages(self):
        with patch("smart_upgrade.adapters.apt.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=APT_LIST_MIXED, stderr=""),
                MagicMock(returncode=0, stdout=POLICY_OUTPUT, stderr=""),
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
            ]

            adapter = AptAdapter()
            packages = adapter.list_upgradable()

        assert packages[0].apt_origin == "Ubuntu"
