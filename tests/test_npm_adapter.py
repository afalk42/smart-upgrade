"""Tests for smart_upgrade.adapters.npm."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from smart_upgrade.adapters.npm import NpmAdapter
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
    def test_parses_dryrun_json(self):
        dryrun_output = (FIXTURES / "npm_install_dryrun.json").read_text()

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

    def test_unparseable_output(self):
        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter(target_package="openclaw@latest")

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=1, stdout="error stuff")
            with pytest.raises(RuntimeError, match="unparseable output"):
                adapter.list_upgradable()

    def test_handles_progress_lines_before_json(self):
        """npm sometimes emits text lines before the JSON object."""
        dryrun_output = (FIXTURES / "npm_install_dryrun.json").read_text()
        stdout_with_progress = (
            "npm warn deprecated old-thing@1.0.0\n"
            "added 0, changed 2, removed 1\n"
            + dryrun_output
        )

        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter(target_package="openclaw@latest")

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(
                returncode=0, stdout=stdout_with_progress,
            )
            packages = adapter.list_upgradable()

        assert len(packages) == 4  # openclaw, axios, plain-crypto-js, old-dep

    def test_empty_change_set(self):
        data = json.dumps({"add": [], "added": 0, "change": [], "changed": 0,
                           "remove": [], "removed": 0})

        with patch("smart_upgrade.adapters.npm.shutil.which", return_value="/usr/local/bin/npm"):
            adapter = NpmAdapter(target_package="openclaw@latest")

        with patch("smart_upgrade.adapters.npm.subprocess.run") as mock_run:
            mock_run.return_value = _mock_run(returncode=0, stdout=data)
            packages = adapter.list_upgradable()

        assert packages == []


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
# _parse_npm_json
# ---------------------------------------------------------------------------

class TestParseNpmJson:
    def test_parses_clean_json(self):
        data = '{"add": [], "change": []}'
        result = NpmAdapter._parse_npm_json(data)
        assert result == {"add": [], "change": []}

    def test_parses_with_prefix_lines(self):
        data = 'npm warn deprecated\nsome text\n{"add": [], "change": []}'
        result = NpmAdapter._parse_npm_json(data)
        assert result == {"add": [], "change": []}

    def test_returns_none_for_no_json(self):
        result = NpmAdapter._parse_npm_json("no json here at all")
        assert result is None

    def test_returns_none_for_invalid_json(self):
        result = NpmAdapter._parse_npm_json("{broken json")
        assert result is None
