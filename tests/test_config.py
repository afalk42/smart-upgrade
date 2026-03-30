"""Tests for smart_upgrade.config."""

import textwrap
from pathlib import Path

import pytest

from smart_upgrade.config import Config, apply_cli_overrides, load_config


class TestLoadConfigDefaults:
    def test_returns_defaults_when_no_file(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.model == "opus"
        assert cfg.review_depth == "light"
        assert cfg.auto_approve is False
        assert cfg.log_level == "info"
        assert cfg.whitelist.apt == []
        assert cfg.whitelist.brew == []
        assert cfg.whitelist.brew_cask == []
        assert cfg.threat_intel.brave_search.enabled is True
        assert cfg.threat_intel.osv.enabled is True
        assert cfg.threat_intel.nvd.enabled is True

    def test_timeouts_defaults(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.timeouts.package_index_refresh == 120
        assert cfg.timeouts.claude_analysis == 300


class TestLoadConfigFromFile:
    def test_loads_basic_values(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(textwrap.dedent("""\
            model: sonnet
            auto_approve: true
            log_level: debug
        """))
        cfg = load_config(config_file)
        assert cfg.model == "sonnet"
        assert cfg.auto_approve is True
        assert cfg.log_level == "debug"

    def test_loads_whitelist(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(textwrap.dedent("""\
            whitelist:
              apt:
                - coreutils
                - linux-image-*
              brew:
                - curl
                - git
              brew-cask:
                - firefox
        """))
        cfg = load_config(config_file)
        assert "coreutils" in cfg.whitelist.apt
        assert "linux-image-*" in cfg.whitelist.apt
        assert "curl" in cfg.whitelist.brew
        assert "firefox" in cfg.whitelist.brew_cask

    def test_loads_apt_trusted_origins(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(textwrap.dedent("""\
            whitelist:
              apt-trusted-origins:
                - Ubuntu
                - Debian
        """))
        cfg = load_config(config_file)
        assert cfg.whitelist.apt_trusted_origins == ["Ubuntu", "Debian"]

    def test_loads_apt_trusted_origins_underscore(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(textwrap.dedent("""\
            whitelist:
              apt_trusted_origins:
                - Ubuntu
        """))
        cfg = load_config(config_file)
        assert cfg.whitelist.apt_trusted_origins == ["Ubuntu"]

    def test_default_apt_trusted_origins(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.whitelist.apt_trusted_origins == []

    def test_loads_threat_intel(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(textwrap.dedent("""\
            threat_intel:
              brave_search:
                enabled: false
              osv:
                enabled: true
              nvd:
                enabled: true
                api_key: "test-key"
        """))
        cfg = load_config(config_file)
        assert cfg.threat_intel.brave_search.enabled is False
        assert cfg.threat_intel.nvd.api_key == "test-key"

    def test_loads_timeouts(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(textwrap.dedent("""\
            timeouts:
              claude_analysis: 600
              threat_intel_query: 60
        """))
        cfg = load_config(config_file)
        assert cfg.timeouts.claude_analysis == 600
        assert cfg.timeouts.threat_intel_query == 60

    def test_empty_file(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")
        cfg = load_config(config_file)
        assert cfg.model == "opus"  # Falls back to defaults

    def test_api_key_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "env-brave-key")
        monkeypatch.setenv("NVD_API_KEY", "env-nvd-key")
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.threat_intel.brave_search.api_key == "env-brave-key"
        assert cfg.threat_intel.nvd.api_key == "env-nvd-key"


class TestApplyCliOverrides:
    def test_model_override(self):
        cfg = Config()
        apply_cli_overrides(cfg, model="haiku")
        assert cfg.model == "haiku"

    def test_yes_override(self):
        cfg = Config()
        apply_cli_overrides(cfg, yes=True)
        assert cfg.auto_approve is True

    def test_none_values_ignored(self):
        cfg = Config()
        apply_cli_overrides(cfg, model=None, yes=None)
        assert cfg.model == "opus"
        assert cfg.auto_approve is False
