"""Tests for smart_upgrade.cli — argument parsing and high-level flow."""

from smart_upgrade.cli import build_parser


class TestBuildParser:
    def test_defaults(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.yes is None
        assert args.dry_run is False
        assert args.model is None
        assert args.config is None
        assert args.packages is None
        assert args.show_whitelist is False

    def test_yes_flag(self):
        parser = build_parser()
        args = parser.parse_args(["-y"])
        assert args.yes is True

    def test_dry_run(self):
        parser = build_parser()
        args = parser.parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_model_choice(self):
        parser = build_parser()
        args = parser.parse_args(["--model", "sonnet"])
        assert args.model == "sonnet"

    def test_packages(self):
        parser = build_parser()
        args = parser.parse_args(["--packages", "curl", "git"])
        assert args.packages == ["curl", "git"]

    def test_show_whitelist(self):
        parser = build_parser()
        args = parser.parse_args(["--show-whitelist"])
        assert args.show_whitelist is True

    def test_log_level(self):
        parser = build_parser()
        args = parser.parse_args(["--log-level", "debug"])
        assert args.log_level == "debug"

    def test_combined_flags(self):
        parser = build_parser()
        args = parser.parse_args(["-y", "--dry-run", "--model", "haiku", "--log-level", "warning"])
        assert args.yes is True
        assert args.dry_run is True
        assert args.model == "haiku"
        assert args.log_level == "warning"
