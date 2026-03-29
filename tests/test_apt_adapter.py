"""Tests for smart_upgrade.adapters.apt."""

from pathlib import Path
from unittest.mock import patch

import pytest

from smart_upgrade.adapters.apt import AptAdapter
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
