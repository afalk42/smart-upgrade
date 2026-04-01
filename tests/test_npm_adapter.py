"""Tests for smart_upgrade.adapters.npm."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from smart_upgrade.adapters.npm import NpmAdapter, _is_foreign_platform
from smart_upgrade.models import PackageSource

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_run(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Create a mock CompletedProcess."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def _npm_view_json(
    name: str = "openclaw",
    homepage: str = "https://github.com/example/openclaw",
    repo_url: str = "git+https://github.com/example/openclaw.git",
    maintainer: str = "alice",
) -> str:
    """Return a minimal ``npm view --json`` response."""
    return json.dumps({
        "name": name,
        "homepage": homepage,
        "repository": {"type": "git", "url": repo_url},
        "_npmUser": {"name": maintainer},
    })


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestInit:
    def test_raises_when_npm_not_found(self):
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="npm is not installed"):
                NpmAdapter()

    def test_finds_npm(self):
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter()
        assert adapter._npm == "/usr/local/bin/npm"

    def test_name(self):
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter()
        assert adapter.name == "npm"


# ---------------------------------------------------------------------------
# refresh_index (no-op)
# ---------------------------------------------------------------------------

class TestRefreshIndex:
    def test_does_not_raise(self):
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter()
        adapter.refresh_index()  # Should not raise


# ---------------------------------------------------------------------------
# list_upgradable — global mode
# ---------------------------------------------------------------------------

class TestListOutdated:
    def test_parses_outdated_json(self):
        outdated_output = (FIXTURES / "npm_outdated.json").read_text()

        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter()

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            # npm outdated exits 1 when packages are outdated.
            mock_run.return_value = _mock_run(
                returncode=1, stdout=outdated_output,
            )
            packages = adapter.list_upgradable()

        assert len(packages) == 2
        assert all(p.source == PackageSource.NPM for p in packages)

        openclaw = next(p for p in packages if p.name == "openclaw")
        assert openclaw.current_version == "1.2.0"
        assert openclaw.new_version == "1.3.0"

        ts = next(p for p in packages if p.name == "typescript")
        assert ts.current_version == "5.3.0"
        assert ts.new_version == "5.4.0"

    def test_empty_output(self):
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter()

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=0, stdout="")
            packages = adapter.list_upgradable()

        assert packages == []

    def test_invalid_json(self):
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter()

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=1, stdout="not json")
            with pytest.raises(RuntimeError, match="Failed to parse"):
                adapter.list_upgradable()

    def test_skips_packages_at_latest(self):
        """If current == latest, the package is not returned."""
        data = json.dumps({
            "already-latest": {
                "current": "2.0.0",
                "wanted": "2.0.0",
                "latest": "2.0.0",
                "dependent": "global",
            },
        })
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter()

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=1, stdout=data)
            packages = adapter.list_upgradable()

        assert packages == []


# ---------------------------------------------------------------------------
# list_upgradable — targeted mode
# ---------------------------------------------------------------------------

class TestListTargeted:
    def test_parses_dryrun_text_lines(self):
        """Parse real npm dry-run text output with add/change/remove lines."""
        dryrun_output = (FIXTURES / "npm_install_dryrun.txt").read_text()

        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter(target_package="openclaw@latest")

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(
                returncode=0, stdout=dryrun_output,
            )
            packages = adapter.list_upgradable()

        assert all(p.source == PackageSource.NPM for p in packages)

        names = {p.name for p in packages}
        assert "openclaw" in names
        assert "axios" in names
        assert "plain-crypto-js" in names
        assert "old-dep" in names

        # Check the new transitive dep.
        new_dep = next(p for p in packages if p.name == "plain-crypto-js")
        assert new_dep.current_version == "(new)"
        assert new_dep.new_version == "1.0.0"

        # Check the changed dep.
        axios = next(p for p in packages if p.name == "axios")
        assert axios.current_version == "1.14.0"
        assert axios.new_version == "1.14.1"

        # Check the removed dep.
        removed = next(p for p in packages if p.name == "old-dep")
        assert removed.current_version == "0.5.0"
        assert removed.new_version == "(removed)"

    def test_filters_same_version_changes(self):
        """Lines where old == new (reinstalls) should be filtered out."""
        dryrun_output = (FIXTURES / "npm_install_dryrun.txt").read_text()

        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter(target_package="openclaw@latest")

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(
                returncode=0, stdout=dryrun_output,
            )
            packages = adapter.list_upgradable()

        # @smithy/core 3.23.12 => 3.23.12 and lodash 4.17.21 => 4.17.21
        # should NOT appear (same version = reinstall, not a real change).
        pkg_names = {p.name for p in packages}
        assert "@smithy/core" not in pkg_names
        assert "lodash" not in pkg_names

    def test_failure_raises(self):
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter(target_package="openclaw@latest")

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(
                returncode=1, stdout="", stderr="ERR! 404 Not Found"
            )
            with pytest.raises(RuntimeError, match="npm install --dry-run.*failed"):
                adapter.list_upgradable()

    def test_no_changes_returns_empty(self):
        """When npm reports no add/change/remove lines, return empty list."""
        stdout = (
            "npm warn some warning\n"
            '{"added": 0, "removed": 0, "changed": 0}\n'
        )
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter(target_package="openclaw@latest")

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=0, stdout=stdout)
            packages = adapter.list_upgradable()

        assert packages == []

    def test_scoped_package_names(self):
        """Scoped packages like @scope/name are parsed correctly.

        Platform-specific adds are filtered, but changes/removes are kept.
        """
        stdout = (
            "add @img/sharp-linux-x64 0.34.5\n"
            "change @anthropic-ai/sdk 0.73.0 => 0.81.0\n"
            "remove @aws-sdk/client-bedrock 3.1019.0\n"
        )
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter(target_package="openclaw@latest")

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=0, stdout=stdout)
            # Simulate linux-x64 so the add line is kept.
            with patch("smart_upgrade.adapters.npm._detect_local_platform", return_value=("linux", "x64")):
                packages = adapter.list_upgradable()

        assert len(packages) == 3

        added = next(p for p in packages if p.name == "@img/sharp-linux-x64")
        assert added.current_version == "(new)"
        assert added.new_version == "0.34.5"

        changed = next(p for p in packages if p.name == "@anthropic-ai/sdk")
        assert changed.current_version == "0.73.0"
        assert changed.new_version == "0.81.0"

        removed = next(p for p in packages if p.name == "@aws-sdk/client-bedrock")
        assert removed.current_version == "3.1019.0"
        assert removed.new_version == "(removed)"

    def test_filters_foreign_platform_packages(self):
        """New packages for other OS/arch are filtered; changes are kept."""
        stdout = (
            "change openclaw 1.2.0 => 1.3.0\n"
            "change sqlite-vec-linux-x64 0.1.7 => 0.1.9\n"
            "add sqlite-vec-darwin-arm64 0.1.9\n"
            "add sqlite-vec-darwin-x64 0.1.9\n"
            "add sqlite-vec-linux-arm64 0.1.9\n"
            "add sqlite-vec-linux-x64 0.1.9\n"          # matches linux-x64
            "add sqlite-vec-windows-x64 0.1.9\n"
            "add @img/sharp-win32-x64 0.34.5\n"
            "add @img/sharp-darwin-arm64 0.34.5\n"
            "add @img/sharp-linux-x64 0.34.5\n"          # matches linux-x64
            "add @img/sharp-wasm32 0.34.5\n"
            "add @emnapi/runtime 1.9.1\n"                 # no platform tokens
        )
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter(target_package="openclaw@latest")

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=0, stdout=stdout)
            with patch("smart_upgrade.adapters.npm._detect_local_platform", return_value=("linux", "x64")):
                packages = adapter.list_upgradable()

        names = {p.name for p in packages}

        # Changes are always kept (already installed).
        assert "openclaw" in names
        assert "sqlite-vec-linux-x64" in names

        # New packages matching linux-x64 are kept.
        new_names = {p.name for p in packages if p.current_version == "(new)"}
        assert "sqlite-vec-linux-x64" in new_names or "sqlite-vec-linux-x64" in names
        assert "@img/sharp-linux-x64" in new_names
        assert "@emnapi/runtime" in new_names  # no platform tokens → kept

        # Foreign platform packages are filtered out.
        assert "sqlite-vec-darwin-arm64" not in names
        assert "sqlite-vec-darwin-x64" not in names
        assert "sqlite-vec-windows-x64" not in names
        assert "sqlite-vec-linux-arm64" not in names
        assert "@img/sharp-win32-x64" not in names
        assert "@img/sharp-darwin-arm64" not in names
        assert "@img/sharp-wasm32" not in names

    def test_does_not_pass_json_flag(self):
        """Targeted mode should NOT pass --json (we parse text lines)."""
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter(target_package="openclaw@latest")

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=0, stdout="")
            adapter.list_upgradable()

        cmd = mock_run.call_args[0][0]
        assert "--json" not in cmd
        assert "--dry-run" in cmd


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------

class TestUpgrade:
    def test_upgrade_target(self):
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter(target_package="openclaw@latest")

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=0)
            adapter.upgrade()

        cmd = mock_run.call_args[0][0]
        assert cmd == ["/usr/local/bin/npm", "install", "-g", "openclaw@latest"]

    def test_upgrade_specific_packages(self):
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter()

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=0)
            adapter.upgrade(["openclaw", "typescript"])

        cmd = mock_run.call_args[0][0]
        assert cmd == ["/usr/local/bin/npm", "install", "-g",
                       "openclaw@latest", "typescript@latest"]

    def test_upgrade_all_global(self):
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter()

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=0)
            adapter.upgrade()

        cmd = mock_run.call_args[0][0]
        assert cmd == ["/usr/local/bin/npm", "update", "-g"]


# ---------------------------------------------------------------------------
# get_package_info
# ---------------------------------------------------------------------------

class TestGetPackageInfo:
    def test_returns_metadata(self):
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter()

        view_data = _npm_view_json()

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=0, stdout=view_data)
            info = adapter.get_package_info("openclaw")

        assert info["homepage"] == "https://github.com/example/openclaw"
        assert "github.com/example/openclaw" in info["source_repo"]
        assert info["maintainer"] == "alice"

    def test_handles_failure(self):
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter()

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=1, stdout="")
            info = adapter.get_package_info("nonexistent")

        assert info == {}

    def test_handles_string_repository(self):
        """npm view sometimes returns repository as a plain string."""
        data = json.dumps({
            "name": "simple",
            "homepage": "https://example.com",
            "repository": "https://github.com/example/simple",
            "_npmUser": "bob",
        })

        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter()

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=0, stdout=data)
            info = adapter.get_package_info("simple")

        assert info["source_repo"] == "https://github.com/example/simple"
        assert info["maintainer"] == "bob"


# ---------------------------------------------------------------------------
# get_changelog
# ---------------------------------------------------------------------------

class TestGetChangelog:
    def test_returns_empty_when_no_github(self):
        """When the package has no GitHub URL, return empty."""
        data = json.dumps({
            "name": "nongithub",
            "homepage": "https://example.com",
        })

        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter()

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=0, stdout=data)
            result = adapter.get_changelog("nongithub")

        assert result == ""


# ---------------------------------------------------------------------------
# _parse_dryrun_lines
# ---------------------------------------------------------------------------

class TestParseDryrunLines:
    def test_parses_add_lines(self):
        stdout = "add express 4.21.2\nadd lodash 4.17.21\n"
        result = NpmAdapter._parse_dryrun_lines(stdout)
        assert len(result) == 2
        assert result[0].name == "express"
        assert result[0].current_version == "(new)"
        assert result[0].new_version == "4.21.2"

    def test_parses_change_lines(self):
        stdout = "change axios 1.14.0 => 1.14.1\n"
        result = NpmAdapter._parse_dryrun_lines(stdout)
        assert len(result) == 1
        assert result[0].name == "axios"
        assert result[0].current_version == "1.14.0"
        assert result[0].new_version == "1.14.1"

    def test_parses_remove_lines(self):
        stdout = "remove old-dep 0.5.0\n"
        result = NpmAdapter._parse_dryrun_lines(stdout)
        assert len(result) == 1
        assert result[0].name == "old-dep"
        assert result[0].new_version == "(removed)"

    def test_filters_same_version_changes(self):
        stdout = "change lodash 4.17.21 => 4.17.21\n"
        result = NpmAdapter._parse_dryrun_lines(stdout)
        assert result == []

    def test_ignores_non_matching_lines(self):
        stdout = (
            "npm warn deprecated something\n"
            "add express 4.21.2\n"
            '{"added": 1, "removed": 0, "changed": 0}\n'
        )
        result = NpmAdapter._parse_dryrun_lines(stdout)
        assert len(result) == 1
        assert result[0].name == "express"

    def test_scoped_packages(self):
        stdout = "add @img/sharp-darwin-arm64 0.34.5\n"
        result = NpmAdapter._parse_dryrun_lines(stdout)
        assert len(result) == 1
        assert result[0].name == "@img/sharp-darwin-arm64"

    def test_empty_input(self):
        assert NpmAdapter._parse_dryrun_lines("") == []
        assert NpmAdapter._parse_dryrun_lines("\n\n") == []

    def test_beta_versions(self):
        stdout = "add @lydell/node-pty 1.2.0-beta.3\n"
        result = NpmAdapter._parse_dryrun_lines(stdout)
        assert len(result) == 1
        assert result[0].new_version == "1.2.0-beta.3"


# ---------------------------------------------------------------------------
# _is_foreign_platform
# ---------------------------------------------------------------------------

class TestIsForeignPlatform:
    """Test the platform-token detection logic for filtering optional deps."""

    # -- No platform tokens (keep everywhere) --

    def test_no_platform_tokens(self):
        assert not _is_foreign_platform("express", "linux", "x64")
        assert not _is_foreign_platform("@anthropic-ai/sdk", "linux", "x64")
        assert not _is_foreign_platform("@emnapi/runtime", "darwin", "arm64")
        assert not _is_foreign_platform("fast-string-width", "win32", "x64")

    # -- OS matching --

    def test_linux_package_on_linux(self):
        assert not _is_foreign_platform("sqlite-vec-linux-x64", "linux", "x64")

    def test_darwin_package_on_linux(self):
        assert _is_foreign_platform("sqlite-vec-darwin-arm64", "linux", "x64")
        assert _is_foreign_platform("@img/sharp-darwin-x64", "linux", "x64")

    def test_win32_package_on_linux(self):
        assert _is_foreign_platform("@img/sharp-win32-x64", "linux", "x64")
        assert _is_foreign_platform("@napi-rs/canvas-win32-x64-msvc", "linux", "x64")

    def test_linux_package_on_darwin(self):
        assert _is_foreign_platform("sqlite-vec-linux-x64", "darwin", "arm64")

    def test_darwin_package_on_darwin(self):
        assert not _is_foreign_platform("@img/sharp-darwin-arm64", "darwin", "arm64")

    def test_linuxmusl_matches_linux_os(self):
        """linuxmusl is still linux for OS matching purposes."""
        assert not _is_foreign_platform("@img/sharp-linuxmusl-x64", "linux", "x64")
        assert _is_foreign_platform("@img/sharp-linuxmusl-x64", "darwin", "arm64")

    # -- Arch matching --

    def test_x64_package_on_x64(self):
        assert not _is_foreign_platform("sqlite-vec-linux-x64", "linux", "x64")

    def test_arm64_package_on_x64(self):
        assert _is_foreign_platform("sqlite-vec-linux-arm64", "linux", "x64")
        assert _is_foreign_platform("@img/sharp-linux-arm64", "linux", "x64")

    def test_x64_package_on_arm64(self):
        assert _is_foreign_platform("@img/sharp-linux-x64", "linux", "arm64")

    def test_arm_package_on_x64(self):
        assert _is_foreign_platform("@img/sharp-linux-arm", "linux", "x64")

    def test_wasm32_filtered_everywhere(self):
        assert _is_foreign_platform("@img/sharp-wasm32", "linux", "x64")
        assert _is_foreign_platform("@img/sharp-wasm32", "darwin", "arm64")

    # -- Combined OS + arch --

    def test_right_os_wrong_arch(self):
        assert _is_foreign_platform("@napi-rs/canvas-linux-arm64-gnu", "linux", "x64")

    def test_wrong_os_right_arch(self):
        assert _is_foreign_platform("@napi-rs/canvas-darwin-x64", "linux", "x64")

    def test_right_os_right_arch(self):
        assert not _is_foreign_platform("@napi-rs/canvas-linux-x64-musl", "linux", "x64")

    # -- Edge cases --

    def test_universal_darwin_not_filtered(self):
        """'universal' is not a known arch token so it doesn't trigger filtering."""
        assert not _is_foreign_platform("@mariozechner/clipboard-darwin-universal", "darwin", "arm64")
        assert not _is_foreign_platform("@mariozechner/clipboard-darwin-universal", "darwin", "x64")

    def test_android_filtered_on_linux(self):
        assert _is_foreign_platform("@napi-rs/canvas-android-arm64", "linux", "x64")
