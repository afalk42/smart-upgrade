"""Wrapper around the ``claude`` CLI for non-interactive, programmatic use.

The invoker shells out to ``claude -p`` (print / non-interactive mode) and
parses the JSON response that comes back.  A timeout is enforced to prevent
hangs, and retry logic handles transient failures.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

# Maximum number of retries for transient Claude CLI failures.
_MAX_RETRIES = 2


class ClaudeNotFoundError(RuntimeError):
    """Raised when the ``claude`` CLI is not on ``$PATH``."""


class ClaudeInvoker:
    """Programmatic interface to the Claude Code CLI.

    Parameters
    ----------
    model:
        Which Claude model to request (``opus``, ``sonnet``, ``haiku``).
    timeout:
        Maximum seconds to wait for a response.
    """

    def __init__(self, model: str = "opus", timeout: int = 300) -> None:
        self.model = model
        self.timeout = timeout
        self._verify_claude_available()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, prompt: str) -> dict[str, Any]:
        """Send *prompt* to Claude and return the parsed JSON response.

        The prompt should instruct Claude to reply with raw JSON (no markdown
        fences).  If the response cannot be parsed as JSON the raw text is
        returned under the key ``"raw_response"``.

        Raises
        ------
        ClaudeNotFoundError
            If the ``claude`` CLI cannot be found.
        RuntimeError
            If Claude fails after all retries.
        """
        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                raw = self._invoke(prompt)
                return self._parse_json(raw)
            except subprocess.TimeoutExpired:
                logger.warning("Claude timed out (attempt %d/%d)", attempt, _MAX_RETRIES)
                last_error = RuntimeError("Claude analysis timed out")
            except RuntimeError as exc:
                logger.warning("Claude invocation error (attempt %d/%d): %s", attempt, _MAX_RETRIES, exc)
                last_error = exc

        raise last_error or RuntimeError("Claude analysis failed after retries")

    def raw_query(self, prompt: str) -> str:
        """Send *prompt* to Claude and return the raw text response."""
        return self._invoke(prompt)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_claude_available() -> None:
        if shutil.which("claude") is None:
            raise ClaudeNotFoundError(
                "The 'claude' CLI was not found on $PATH.\n"
                "Install Claude Code: https://docs.anthropic.com/en/docs/claude-code\n"
                "Then ensure 'claude' is available in your shell."
            )

    def _invoke(self, prompt: str) -> str:
        """Run ``claude -p --model <model>`` with the given prompt."""
        cmd = [
            "claude",
            "-p",
            "--model", self.model,
            "--output-format", "text",
        ]
        logger.debug("Invoking Claude (model=%s, timeout=%ds)", self.model, self.timeout)

        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited with code {result.returncode}:\n{result.stderr.strip()}"
            )

        return result.stdout.strip()

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Attempt to parse *text* as JSON.

        Claude sometimes wraps JSON in markdown code fences — strip those
        before parsing.  If parsing fails, return the raw text in a wrapper
        dict so callers always get a dict back.
        """
        cleaned = text.strip()
        # Strip optional markdown fences.
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            # Remove first line (```json or ```) and last line (```)
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            cleaned = "\n".join(lines).strip()

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
            return {"data": parsed}
        except json.JSONDecodeError:
            logger.warning("Could not parse Claude response as JSON; returning raw text")
            return {"raw_response": text}
