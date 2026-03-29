"""Tests for smart_upgrade.analysis.claude_invoker."""

from unittest.mock import patch

import pytest

from smart_upgrade.analysis.claude_invoker import ClaudeInvoker, ClaudeNotFoundError


class TestClaudeAvailability:
    @patch("shutil.which", return_value=None)
    def test_raises_when_not_found(self, _mock_which):
        with pytest.raises(ClaudeNotFoundError, match="not found"):
            ClaudeInvoker()

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_ok_when_found(self, _mock_which):
        invoker = ClaudeInvoker()
        assert invoker.model == "opus"


class TestParseJson:
    def test_plain_json(self):
        result = ClaudeInvoker._parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_markdown_fences(self):
        text = '```json\n{"key": "value"}\n```'
        result = ClaudeInvoker._parse_json(text)
        assert result == {"key": "value"}

    def test_plain_fences(self):
        text = '```\n{"key": "value"}\n```'
        result = ClaudeInvoker._parse_json(text)
        assert result == {"key": "value"}

    def test_non_json_returns_raw(self):
        result = ClaudeInvoker._parse_json("This is not JSON at all")
        assert "raw_response" in result
        assert result["raw_response"] == "This is not JSON at all"

    def test_json_list_wrapped_in_dict(self):
        result = ClaudeInvoker._parse_json("[1, 2, 3]")
        assert result == {"data": [1, 2, 3]}


class TestAnalyze:
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    @patch("smart_upgrade.analysis.claude_invoker.subprocess.run")
    def test_success(self, mock_run, _mock_which):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = '{"risk_level": "clear", "summary": "all good"}'
        mock_run.return_value.stderr = ""

        invoker = ClaudeInvoker(timeout=10)
        result = invoker.analyze("test prompt")

        assert result["risk_level"] == "clear"
        # Verify the command includes -p and --model
        cmd = mock_run.call_args[0][0]
        assert "-p" in cmd
        assert "--model" in cmd

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    @patch("smart_upgrade.analysis.claude_invoker.subprocess.run")
    def test_timeout_retries(self, mock_run, _mock_which):
        import subprocess as sp

        mock_run.side_effect = sp.TimeoutExpired(cmd="claude", timeout=10)

        invoker = ClaudeInvoker(timeout=10)
        with pytest.raises(RuntimeError, match="timed out"):
            invoker.analyze("test prompt")

        # Should have retried
        assert mock_run.call_count == 2

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    @patch("smart_upgrade.analysis.claude_invoker.subprocess.run")
    def test_nonzero_exit_retries(self, mock_run, _mock_which):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "Some error"

        invoker = ClaudeInvoker(timeout=10)
        with pytest.raises(RuntimeError, match="exited with code 1"):
            invoker.analyze("test prompt")

        assert mock_run.call_count == 2
