"""Configuration loading, validation, and default values.

The configuration is loaded from a YAML file (default location:
``~/.config/smart-upgrade/config.yaml``) and merged with CLI overrides.
Missing keys fall back to sensible defaults so the tool works out of the box
without any configuration file at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = Path("~/.config/smart-upgrade/config.yaml").expanduser()
DEFAULT_LOG_DIR = Path("~/.local/share/smart-upgrade/logs").expanduser()


# ---------------------------------------------------------------------------
# Typed configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class ThreatIntelSourceConfig:
    """Toggle and credentials for a single threat-intelligence source."""

    enabled: bool = True
    api_key: str | None = None


@dataclass
class ThreatIntelConfig:
    """Threat intelligence source settings."""

    brave_search: ThreatIntelSourceConfig = field(default_factory=ThreatIntelSourceConfig)
    osv: ThreatIntelSourceConfig = field(default_factory=ThreatIntelSourceConfig)
    nvd: ThreatIntelSourceConfig = field(default_factory=ThreatIntelSourceConfig)


@dataclass
class TimeoutsConfig:
    """Timeout values in seconds."""

    package_index_refresh: int = 120
    claude_analysis: int = 300
    threat_intel_query: int = 30
    upgrade_execution: int = 600


@dataclass
class WhitelistConfig:
    """Per-package-manager whitelists (lists of glob patterns)."""

    apt: list[str] = field(default_factory=list)
    brew: list[str] = field(default_factory=list)
    brew_cask: list[str] = field(default_factory=list)


@dataclass
class Config:
    """Top-level configuration for smart-upgrade."""

    model: str = "opus"
    review_depth: str = "light"
    auto_approve: bool = False
    log_level: str = "info"
    log_directory: Path = field(default_factory=lambda: DEFAULT_LOG_DIR)
    whitelist: WhitelistConfig = field(default_factory=WhitelistConfig)
    threat_intel: ThreatIntelConfig = field(default_factory=ThreatIntelConfig)
    timeouts: TimeoutsConfig = field(default_factory=TimeoutsConfig)


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------

def _parse_threat_source(data: dict[str, Any] | None) -> ThreatIntelSourceConfig:
    """Parse a single threat-intelligence source block."""
    if data is None:
        return ThreatIntelSourceConfig()
    return ThreatIntelSourceConfig(
        enabled=data.get("enabled", True),
        api_key=data.get("api_key"),
    )


def _resolve_api_keys(config: Config) -> None:
    """Fill in API keys from environment variables when not set in the file."""
    brave = config.threat_intel.brave_search
    if brave.api_key is None:
        brave.api_key = os.environ.get("BRAVE_SEARCH_API_KEY")

    nvd = config.threat_intel.nvd
    if nvd.api_key is None:
        nvd.api_key = os.environ.get("NVD_API_KEY")


def load_config(path: Path | None = None) -> Config:
    """Load configuration from *path*, falling back to defaults.

    Parameters
    ----------
    path:
        Explicit path to a YAML config file.  When *None* the default
        location ``~/.config/smart-upgrade/config.yaml`` is tried; if
        that file does not exist the built-in defaults are returned.

    Returns
    -------
    Config
        A fully resolved configuration object.
    """
    config_path = path or DEFAULT_CONFIG_PATH

    if not config_path.exists():
        cfg = Config()
        _resolve_api_keys(cfg)
        return cfg

    with open(config_path, "r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    # --- whitelist ---
    wl_raw = raw.get("whitelist", {}) or {}
    whitelist = WhitelistConfig(
        apt=wl_raw.get("apt", []) or [],
        brew=wl_raw.get("brew", []) or [],
        brew_cask=wl_raw.get("brew-cask", wl_raw.get("brew_cask", [])) or [],
    )

    # --- threat_intel ---
    ti_raw = raw.get("threat_intel", {}) or {}
    threat_intel = ThreatIntelConfig(
        brave_search=_parse_threat_source(ti_raw.get("brave_search")),
        osv=_parse_threat_source(ti_raw.get("osv")),
        nvd=_parse_threat_source(ti_raw.get("nvd")),
    )

    # --- timeouts ---
    to_raw = raw.get("timeouts", {}) or {}
    timeouts = TimeoutsConfig(
        package_index_refresh=to_raw.get("package_index_refresh", 120),
        claude_analysis=to_raw.get("claude_analysis", 300),
        threat_intel_query=to_raw.get("threat_intel_query", 30),
        upgrade_execution=to_raw.get("upgrade_execution", 600),
    )

    # --- log directory ---
    log_dir_str = raw.get("log_directory", str(DEFAULT_LOG_DIR))
    log_directory = Path(log_dir_str).expanduser()

    cfg = Config(
        model=raw.get("model", "opus"),
        review_depth=raw.get("review_depth", "light"),
        auto_approve=raw.get("auto_approve", False),
        log_level=raw.get("log_level", "info"),
        log_directory=log_directory,
        whitelist=whitelist,
        threat_intel=threat_intel,
        timeouts=timeouts,
    )

    _resolve_api_keys(cfg)
    return cfg


def apply_cli_overrides(config: Config, **overrides: Any) -> Config:
    """Apply CLI flag overrides on top of file-based configuration.

    Only non-None values in *overrides* are applied.  Recognised keys:
    ``model``, ``review_depth``, ``auto_approve``, ``log_level``,
    ``config`` (ignored here — already handled), ``log_directory``.
    """
    mapping = {
        "model": "model",
        "review_depth": "review_depth",
        "yes": "auto_approve",
        "log_level": "log_level",
    }
    for cli_key, attr in mapping.items():
        value = overrides.get(cli_key)
        if value is not None:
            setattr(config, attr, value)

    return config
