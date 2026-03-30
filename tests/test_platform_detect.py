"""Tests for smart_upgrade.platform_detect."""

from unittest.mock import patch

import pytest

from smart_upgrade.platform_detect import (
    UnsupportedPlatformError,
    detect_platform,
    is_running_as_root,
)


class TestDetectPlatform:
    @patch("smart_upgrade.platform_detect.platform.system", return_value="Darwin")
    def test_macos(self, _mock_system):
        assert detect_platform() == "macos"

    @patch("smart_upgrade.platform_detect.platform.system", return_value="Linux")
    @patch(
        "smart_upgrade.platform_detect._read_os_release",
        return_value={"ID": "ubuntu", "ID_LIKE": "debian"},
    )
    def test_ubuntu(self, _mock_release, _mock_system):
        assert detect_platform() == "linux-apt"

    @patch("smart_upgrade.platform_detect.platform.system", return_value="Linux")
    @patch(
        "smart_upgrade.platform_detect._read_os_release",
        return_value={"ID": "debian"},
    )
    def test_debian(self, _mock_release, _mock_system):
        assert detect_platform() == "linux-apt"

    @patch("smart_upgrade.platform_detect.platform.system", return_value="Linux")
    @patch(
        "smart_upgrade.platform_detect._read_os_release",
        return_value={"ID": "linuxmint", "ID_LIKE": "ubuntu debian"},
    )
    def test_debian_derivative(self, _mock_release, _mock_system):
        assert detect_platform() == "linux-apt"

    @patch("smart_upgrade.platform_detect.platform.system", return_value="Linux")
    @patch(
        "smart_upgrade.platform_detect._read_os_release",
        return_value={
            "ID": "raspbian",
            "ID_LIKE": "debian",
            "PRETTY_NAME": "Raspbian GNU/Linux 11 (bullseye)",
        },
    )
    def test_raspberry_pi_os_old(self, _mock_release, _mock_system):
        """Older Raspberry Pi OS (Bullseye and earlier) uses ID=raspbian."""
        assert detect_platform() == "linux-apt"

    @patch("smart_upgrade.platform_detect.platform.system", return_value="Linux")
    @patch(
        "smart_upgrade.platform_detect._read_os_release",
        return_value={
            "ID": "debian",
            "PRETTY_NAME": "Debian GNU/Linux 12 (bookworm)",
            "VERSION_CODENAME": "bookworm",
        },
    )
    def test_raspberry_pi_os_new(self, _mock_release, _mock_system):
        """Newer Raspberry Pi OS (Bookworm+) uses ID=debian directly."""
        assert detect_platform() == "linux-apt"

    @patch("smart_upgrade.platform_detect.platform.system", return_value="Linux")
    @patch(
        "smart_upgrade.platform_detect._read_os_release",
        return_value={"ID": "fedora", "PRETTY_NAME": "Fedora 40"},
    )
    def test_unsupported_linux(self, _mock_release, _mock_system):
        with pytest.raises(UnsupportedPlatformError, match="Fedora 40"):
            detect_platform()

    @patch("smart_upgrade.platform_detect.platform.system", return_value="Windows")
    def test_unsupported_os(self, _mock_system):
        with pytest.raises(UnsupportedPlatformError, match="Windows"):
            detect_platform()


class TestIsRunningAsRoot:
    @patch("os.geteuid", return_value=0)
    def test_root(self, _mock_euid):
        assert is_running_as_root() is True

    @patch("os.geteuid", return_value=1000)
    def test_normal_user(self, _mock_euid):
        assert is_running_as_root() is False
