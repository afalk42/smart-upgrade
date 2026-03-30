# CLAUDE.md -- Instructions for AI Agents

This file provides context for Claude Code and other AI agents working on the
`smart-upgrade` project. Read this before making changes.

## Project Overview

`smart-upgrade` is a security-aware CLI tool that wraps system package managers
(`apt` on Debian/Ubuntu, `brew` on macOS) with an AI-powered security review
layer. It uses the Claude CLI programmatically to analyze pending upgrades for
supply-chain threats before installing them.

**Key design document:** `SPECIFICATION.md` contains the full specification,
including architecture, data models, execution flow, and threat model. Read it
before making architectural changes.

## Tech Stack

- **Language:** Python 3.10+ (uses `from __future__ import annotations`)
- **Dependencies (runtime):** `pyyaml`, `rich` -- deliberately minimal
- **Dependencies (dev):** `pytest`, `pytest-mock`
- **HTTP:** `urllib.request` (stdlib) -- no `requests` library
- **CLI framework:** `argparse` (stdlib)
- **TUI output:** `rich` (not `textual` -- see SPECIFICATION.md Section 10.2)

## Architecture

```
cli.py (entry point, orchestration)
  -> config.py (YAML config loading)
  -> platform_detect.py (OS detection)
  -> adapters/{apt,brew}.py (package manager wrappers)
  -> analysis/engine.py (3-layer security analysis orchestrator)
       -> analysis/claude_invoker.py (claude CLI wrapper)
       -> analysis/threat_intel.py (Brave/OSV/NVD API clients)
       -> analysis/changelog.py (changelog retrieval)
  -> whitelist.py (glob-based + origin-based package whitelisting)
  -> audit.py (YAML audit log writer)
  -> ui.py (rich-based terminal output)
```

**Data flows one direction:** `cli.py` orchestrates everything. The analysis
engine, adapters, and UI modules do not call each other directly.

## Critical Design Constraints

1. **Never run as root.** The tool always runs as the regular user. Only the
   APT adapter invokes `sudo` internally for `apt update` and `apt upgrade`.
   Homebrew needs no elevation. See SPECIFICATION.md Section 4.0 and 13.4.

2. **Claude CLI is required.** There is no `--skip-analysis` flag. If `claude`
   is not on `$PATH`, the tool exits with an error. Users who want to skip
   analysis should use `apt upgrade` or `brew upgrade` directly.

3. **Minimal dependencies.** Use stdlib wherever possible. HTTP calls use
   `urllib.request`, not `requests`. Only add new dependencies with strong
   justification.

4. **Prompt templates are versioned files.** The prompts in `smart_upgrade/prompts/`
   are critical to the tool's security analysis quality. Changes to prompts should
   be treated as carefully as code changes.

## Code Conventions

- **Type hints everywhere.** All functions have full type annotations.
- **Dataclasses for data, Protocols for interfaces.** See `models.py` and
  `adapters/base.py`.
- **Enums for fixed sets.** `RiskLevel`, `Recommendation`, `PackageSource`,
  `FindingCategory` are all enums.
- **Risk level comparison.** Don't compare `RiskLevel.value` strings directly
  (alphabetical order != severity order). Use `_more_severe_risk()` and
  `_more_severe_rec()` from `analysis/engine.py`.
- **Logging.** Use `logging.getLogger(__name__)` in each module. User-facing
  output goes through `ui.py`, not `print()`.

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_config.py -v

# Run a specific test class
pytest tests/test_whitelist.py::TestIsWhitelisted -v
```

Tests are organized by module. All external calls (subprocess, HTTP) are mocked.
Test fixtures live in `tests/fixtures/`.

**When adding a new module**, create a corresponding `tests/test_<module>.py`.

## Common Tasks

### Adding a new package manager adapter

1. Create `smart_upgrade/adapters/<name>.py` implementing the same interface as
   `apt.py` / `brew.py` (see `base.py` for the Protocol).
2. Add the platform detection logic in `platform_detect.py`.
3. Add the adapter instantiation in `cli.py:_create_adapter()`.
4. Add whitelist support in `config.py` (WhitelistConfig) and `whitelist.py`.
5. Add tests in `tests/test_<name>_adapter.py`.
6. Update `SPECIFICATION.md` Section 3 (supported platforms table).

### Adding a new threat intelligence source

1. Add the query function in `analysis/threat_intel.py` following the pattern of
   `query_osv()` / `query_nvd()` / `query_brave_search()`.
2. Add it to `gather_threat_intel()` with an enable flag.
3. Add config support in `config.py` (ThreatIntelConfig).
4. Update the Layer B prompt template if the new source needs special handling.
5. Add tests in `tests/test_threat_intel.py`.

### Modifying Claude prompts

1. Edit the template in `smart_upgrade/prompts/`.
2. Verify the JSON schema in the prompt matches what `analysis/engine.py` expects.
3. Test with `smart-upgrade --dry-run` to verify Claude returns parseable output.

## File-by-File Reference

| File | Purpose | Key details |
|---|---|---|
| `cli.py` | Entry point | `main()` function, 5-step orchestration flow |
| `config.py` | Config loading | YAML from `~/.config/smart-upgrade/config.yaml`, env vars for API keys |
| `models.py` | Data structures | All dataclasses and enums live here |
| `platform_detect.py` | OS detection | Reads `/etc/os-release` on Linux, `platform.system()` for macOS |
| `adapters/apt.py` | APT wrapper | Parses `apt list --upgradable`, enriches with origin info via `apt-cache policy`, uses `sudo` internally |
| `adapters/brew.py` | Brew wrapper | Parses `brew outdated --json=v2`, enriches metadata via `brew info`, fetches GitHub release notes for changelogs |
| `analysis/engine.py` | Analysis orchestrator | Runs Layers A/B/C, merges results |
| `analysis/claude_invoker.py` | Claude CLI wrapper | `claude -p --model <model>`, retry logic, JSON parsing |
| `analysis/threat_intel.py` | Threat intel clients | Brave Search, OSV.dev, NVD -- all via `urllib.request`. OSV only for valid ecosystems (Debian, PyPI, etc. -- not Homebrew) |
| `analysis/changelog.py` | Changelog retrieval | Delegates to adapter's `get_changelog()` method |
| `whitelist.py` | Whitelist matching | `fnmatch` glob patterns, per-package-manager lists, APT origin-based auto-whitelisting |
| `audit.py` | Audit logging | YAML files in `~/.local/share/smart-upgrade/logs/`, 0600 permissions. Decisions are serialized compactly (name + approved + reason only, no duplicated analysis data) |
| `ui.py` | Terminal output | All `rich` usage is here -- tables, panels, progress, prompts |

## Environment Variables

| Variable | Purpose |
|---|---|
| `BRAVE_SEARCH_API_KEY` | Brave Search API key for threat intelligence |
| `NVD_API_KEY` | NIST NVD API key (optional, improves rate limits) |
| `SMART_UPGRADE_PYTHON` | Override Python interpreter in the shell wrapper script |

## Things to Watch Out For

- **Enum string comparisons.** `RiskLevel` values are strings ("clear", "low",
  etc.). Alphabetical order does NOT match severity order ("critical" < "high"
  alphabetically). Always use `_RISK_ORDER` / `_more_severe_risk()`.
- **Rich markup in prompts.** `console.input()` interprets Rich markup. Square
  brackets like `[Y/n]` are treated as style tags and silently swallowed.
  Escape the opening bracket: `\\[Y/n]`. This applies to all strings passed
  to `console.print()` and `console.input()`.
- **OSV ecosystem coverage.** OSV.dev only tracks packages in specific
  ecosystems (Debian, PyPI, npm, etc.). Homebrew is NOT a valid ecosystem.
  The `_VALID_OSV_ECOSYSTEMS` set in `threat_intel.py` gates this — brew
  packages are skipped with a DEBUG log. NVD provides vulnerability
  coverage for brew packages instead.
- **Brew changelog retrieval.** `brew log` does not work reliably (shallow
  clones, flag incompatibilities). The brew adapter instead fetches GitHub
  release notes via the API by extracting the owner/repo from `brew info`'s
  source URL. Non-GitHub packages get no changelog (Layer C is skipped).
- **Brew metadata enrichment.** `brew outdated --json=v2` does NOT include
  homepage, source URL, or tap info. The brew adapter runs a separate
  `brew info --json=v2 <all-packages>` call (batched, single invocation)
  to populate these fields on each `PendingUpgrade`.
- **Audit log deduplication.** The `decisions` section of the audit log is
  serialized compactly via `_serialize_decisions()` — only package name,
  approved flag, skipped_reason, risk_level, and recommendation. Do NOT
  use the generic `_to_serializable()` for decisions, as it would duplicate
  the full PendingUpgrade and AnalysisResult data already present above.
- **APT origin enrichment.** The APT adapter runs `apt-cache policy` (one call,
  no root needed) after `apt list --upgradable` to resolve each package's
  repository origin label (e.g. `"Ubuntu"`, `"Debian"`).  The suite name from
  `apt list` (e.g. `jammy-updates`) is mapped to an origin via the `release`
  lines in the policy output.  The `_parse_policy_origins()` function in
  `apt.py` only returns unambiguous mappings — if two repos share the same
  archive name with different origins, that archive is omitted and the
  package won't be auto-whitelisted.  Origin enrichment is fault-tolerant:
  if `apt-cache policy` fails, packages simply get `apt_origin=None` and
  origin-based whitelisting is silently skipped.
- **APT origin-based whitelisting.** `WhitelistConfig.apt_trusted_origins` is
  a list of APT origin labels.  When set, `is_whitelisted()` in `whitelist.py`
  checks `package.apt_origin` against this list in addition to name-based
  glob patterns.  The config key is `apt-trusted-origins` (with dashes) in
  YAML, accepting `apt_trusted_origins` (with underscores) as a fallback,
  mirroring the `brew-cask` / `brew_cask` convention.
- **subprocess calls.** APT adapter uses `sudo` -- tests must mock `subprocess.run`.
  The APT adapter now makes two subprocess calls in `list_upgradable()`:
  `apt list --upgradable` and `apt-cache policy`.  Existing tests that mock
  a single return value still pass because `_enrich_origins()` gracefully
  handles unparseable policy output (empty origin map, no crash).
- **Prompt injection.** Package names and changelog content are inserted into
  Claude prompts. The prompts instruct Claude to return JSON, but malicious
  content in changelogs could theoretically attempt prompt injection. The
  `_parse_json()` method in `claude_invoker.py` handles malformed responses
  gracefully.
- **API rate limits.** NVD without an API key is limited to ~5 requests per
  30 seconds. The tool queries packages sequentially, not in parallel.
- **HTTP error handling.** The `_http_get` and `_http_post_json` helpers in
  `threat_intel.py` parse structured error bodies from API responses (e.g.,
  Brave's `error.detail` field) for clearer warning messages. Always catch
  `HTTPError` separately from generic `URLError` to extract these details.
