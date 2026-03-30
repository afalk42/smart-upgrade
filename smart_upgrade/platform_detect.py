"""Platform detection — identifies the current OS and selects the right adapter.

Supported platforms:
- **macOS**: Uses Homebrew (formulae + casks).
- **Linux (Debian/Ubuntu/Raspberry Pi OS)**: Uses APT.

Raspberry Pi OS (formerly Raspbian) is Debian-based and uses APT.  It is
detected via ``ID_LIKE=debian`` in ``/etc/os-release``, the same path as
any other Debian derivative.

On unsupported platforms the module raises ``UnsupportedPlatformError`` with a
clear message listing what *is* supported.
"""

from __future__ import annotations

import platform
from pathlib import Path


class UnsupportedPlatformError(RuntimeError):
    """Raised when smart-upgrade is run on a platform it does not support."""


def _read_os_release() -> dict[str, str]:
    """Parse ``/etc/os-release`` into a dict (Linux only)."""
    path = Path("/etc/os-release")
    if not path.exists():
        return {}
    pairs: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        pairs[key.strip()] = value.strip().strip('"')
    return pairs


def detect_platform() -> str:
    """Return ``"macos"`` or ``"linux-apt"`` depending on the host.

    Raises
    ------
    UnsupportedPlatformError
        If the current OS is not macOS or a Debian-based Linux
        (Debian, Ubuntu, Raspberry Pi OS, and other derivatives).
    """
    system = platform.system()

    if system == "Darwin":
        return "macos"

    if system == "Linux":
        os_release = _read_os_release()
        distro_id = os_release.get("ID", "").lower()
        id_like = os_release.get("ID_LIKE", "").lower()

        if distro_id in ("debian", "ubuntu") or "debian" in id_like:
            return "linux-apt"

        raise UnsupportedPlatformError(
            f"Unsupported Linux distribution: {os_release.get('PRETTY_NAME', distro_id or 'unknown')}.\n"
            "smart-upgrade currently supports Debian, Ubuntu, Raspberry Pi OS,\n"
            "and other Debian-based distributions.\n"
            "Future versions may add Fedora/RHEL (dnf) support."
        )

    raise UnsupportedPlatformError(
        f"Unsupported operating system: {system}.\n"
        "smart-upgrade supports macOS (Homebrew) and Debian-based Linux (APT),\n"
        "including Debian, Ubuntu, and Raspberry Pi OS."
    )


def is_running_as_root() -> bool:
    """Return True if the current process is running as root (UID 0)."""
    import os

    return os.geteuid() == 0
