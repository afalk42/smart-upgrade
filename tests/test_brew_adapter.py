"""Tests for smart_upgrade.adapters.brew."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from smart_upgrade.adapters.brew import BrewAdapter
from smart_upgrade.models import PackageSource

FIXTURES = Path(__file__).parent / "fixtures"


class TestListUpgradable:
    def test_parses_json(self):
        brew_output = (FIXTURES / "brew_outdated.json").read_text()

        with patch("smart_upgrade.adapters.brew.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = brew_output
            mock_run.return_value.stderr = ""

            adapter = BrewAdapter()
            packages = adapter.list_upgradable()

        formulae = [p for p in packages if p.source == PackageSource.BREW_FORMULA]
        casks = [p for p in packages if p.source == PackageSource.BREW_CASK]

        assert len(formulae) == 3
        assert len(casks) == 2

        curl = next(p for p in formulae if p.name == "curl")
        assert curl.current_version == "8.7.1"
        assert curl.new_version == "8.8.0"
        assert curl.homepage == "https://curl.se"

        firefox = next(p for p in casks if p.name == "firefox")
        assert firefox.current_version == "125.0"
        assert firefox.new_version == "126.0"

    def test_empty_output(self):
        with patch("smart_upgrade.adapters.brew.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"formulae": [], "casks": []}'
            mock_run.return_value.stderr = ""

            adapter = BrewAdapter()
            packages = adapter.list_upgradable()

        assert packages == []

    def test_command_failure(self):
        with patch("smart_upgrade.adapters.brew.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "Error"

            adapter = BrewAdapter()
            with pytest.raises(RuntimeError, match="brew outdated"):
                adapter.list_upgradable()

    def test_invalid_json(self):
        with patch("smart_upgrade.adapters.brew.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "not json"
            mock_run.return_value.stderr = ""

            adapter = BrewAdapter()
            with pytest.raises(RuntimeError, match="Failed to parse"):
                adapter.list_upgradable()


class TestRefreshIndex:
    def test_calls_brew_update(self):
        with patch("smart_upgrade.adapters.brew.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""

            adapter = BrewAdapter()
            adapter.refresh_index()

        cmd = mock_run.call_args[0][0]
        assert cmd == ["brew", "update"]


class TestUpgrade:
    def test_upgrade_all(self):
        with patch("smart_upgrade.adapters.brew.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0

            adapter = BrewAdapter()
            adapter.upgrade()

        cmd = mock_run.call_args[0][0]
        assert cmd == ["brew", "upgrade"]

    def test_upgrade_specific(self):
        with patch("smart_upgrade.adapters.brew.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0

            adapter = BrewAdapter()
            adapter.upgrade(["curl", "firefox"])

        cmd = mock_run.call_args[0][0]
        assert cmd == ["brew", "upgrade", "curl", "firefox"]
