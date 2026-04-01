# CLAUDE.md -- Instructions for AI Agents

This file provides context for Claude Code and other AI agents working on the
`smart-upgrade` project. Read this before making changes.

## Project Overview

`smart-upgrade` is a security-aware CLI tool that wraps system package managers
(`apt` on Debian/Ubuntu, `brew` on macOS, `npm` for global Node.js packages)
with an AI-powered security review layer. It uses the Claude CLI
programmatically to analyze pending upgrades for supply-chain threats before
installing them.

**Key design document:** `SPECIFICATION.md` contains the full specification,
including architecture, data models, execution flow, and threat model. Read it
before making architectural changes.

## Tech Stack

- **Supported platforms:** macOS (Homebrew), Debian, Ubuntu, Raspberry Pi OS,
  and other Debian-based distributions (APT), npm global packages (cross-platform)
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
  -> adapters/{apt,brew,npm}.py (package manager wrappers)
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
   Homebrew and npm need no elevation. See SPECIFICATION.md Section 4.0 and 13.4.

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
| `adapters/apt.py` | APT wrapper | Parses `apt list --upgradable`, enriches with origin via `apt-cache policy` and metadata via `apt show` (batched), changelog with GitHub fallback, uses `sudo` internally |
| `adapters/brew.py` | Brew wrapper | Parses `brew outdated --json=v2`, enriches metadata via `brew info`, fetches GitHub release notes for changelogs |
| `adapters/npm.py` | npm wrapper | Two modes: targeted (`--npm pkg@ver`) parses `npm install --dry-run` text output, global (`--npm`) parses `npm outdated --json`. Filters foreign-platform optional deps by OS/arch/libc. Enriches metadata via `npm view --json`, changelogs via GitHub API |
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
- **APT metadata enrichment.** Similarly, `apt list --upgradable` does NOT
  include maintainer, homepage, or source info.  The APT adapter runs
  `apt show <all-packages>` (batched, single invocation) to populate
  `maintainer` and `homepage` on each `PendingUpgrade`.  It also parses
  the `Source:` field and, for packages still missing Homepage after the
  first pass, tries a second `apt show` lookup using the source package
  name (useful for ESM packages whose binary metadata often omits
  Homepage while the source-named package has it).  Homepages are cached
  in `self._homepages` for use by the changelog GitHub fallback.
- **APT changelog retrieval.** `apt changelog` only works for packages in
  official Debian/Ubuntu repos.  For third-party packages (e.g. Brave
  Browser from its own APT repo), it fails.  The APT adapter handles this
  by falling back to GitHub release notes when the package's homepage URL
  points to GitHub.  If neither source is available, the changelog is
  skipped with an INFO-level log (not a WARNING).  This mirrors the
  Homebrew adapter's approach via `_fetch_github_release_notes()`.
- **Audit log deduplication.** The `decisions` section of the audit log is
  serialized compactly via `_serialize_decisions()` — only package name,
  approved flag, skipped_reason, risk_level, and recommendation. Do NOT
  use the generic `_to_serializable()` for decisions, as it would duplicate
  the full PendingUpgrade and AnalysisResult data already present above.
- **APT origin enrichment.** The APT adapter runs `apt-cache policy` (no root
  needed) after `apt list --upgradable` to resolve each package's repository
  origin label (e.g. `"Ubuntu"`, `"Debian"`, `"Raspberry Pi Foundation"`).
  The suite name from `apt list` (e.g. `jammy-updates`) is first mapped to
  an origin via the archive → origin mapping from `_parse_policy_origins()`.
  When this mapping is ambiguous (e.g. on Raspberry Pi OS where both Debian
  and Raspberry Pi Foundation repos share the `bookworm` archive name), a
  fallback runs `apt-cache policy <unresolved-packages>` to identify each
  package's candidate repo URL, then cross-references it against the global
  policy's release metadata via `_parse_policy_source_origins()`.  Origin
  enrichment is fault-tolerant: if any `apt-cache policy` call fails,
  packages simply get `apt_origin=None` and origin-based whitelisting is
  silently skipped.
- **APT origin-based whitelisting.** `WhitelistConfig.apt_trusted_origins` is
  a list of APT origin labels.  When set, `is_whitelisted()` in `whitelist.py`
  checks `package.apt_origin` against this list in addition to name-based
  glob patterns.  The config key is `apt-trusted-origins` (with dashes) in
  YAML, accepting `apt_trusted_origins` (with underscores) as a fallback,
  mirroring the `brew-cask` / `brew_cask` convention.  Common origin labels
  by distribution:
  - **Debian:** `"Debian"`
  - **Ubuntu:** `"Ubuntu"`
  - **Raspberry Pi OS (Bookworm+):** `"Raspberry Pi Foundation"` (for packages from `archive.raspberrypi.com`)
  - **Raspberry Pi OS (older):** `"Raspbian"` (for packages from `archive.raspbian.org`)

  A Raspberry Pi OS (Bookworm) user's config might include:
  ```yaml
  whitelist:
    apt-trusted-origins:
      - Debian
      - Raspberry Pi Foundation
  ```
  On Raspberry Pi OS, the Debian and RPi Foundation repos share the
  same archive name (``bookworm``).  The APT adapter handles this by
  falling back to per-package ``apt-cache policy`` when the global
  archive → origin mapping is ambiguous (see ``_enrich_origins()`` in
  ``apt.py``).
- **APT upgrade streaming.** The APT adapter's `upgrade()` method does NOT
  capture stdout/stderr — output streams directly to the terminal so users
  can see apt's download/install progress and respond to interactive dpkg
  prompts (e.g. config-file conflict questions).  This means `result.stdout`
  and `result.stderr` are `None` after the call; `cli.py` handles this
  gracefully when checking for errors.  The Homebrew adapter still captures
  output (brew upgrades are fast and non-interactive).
- **Analysis progress callback.** The engine emits a ``"package_start"``
  progress event before each non-whitelisted package's Layer B/C analysis,
  including the package name, version change, and counter.  ``cli.py``
  renders this as a Rich-formatted line.  The default log level is now
  ``warning`` (not ``info``), so ``[INFO]`` messages are hidden unless
  ``--log-level info`` is passed — the Rich progress messages are the
  primary user-facing output during analysis.
- **subprocess calls.** APT adapter uses `sudo` -- tests must mock `subprocess.run`.
  The APT adapter makes three subprocess calls in `list_upgradable()`:
  `apt list --upgradable`, `apt-cache policy`, and `apt show <all-packages>`
  (plus an optional fourth `apt show` for source-package Homepage fallback,
  and an optional fifth `apt-cache policy <packages>` for the per-package
  origin resolution fallback on systems with ambiguous archive names like
  Raspberry Pi OS).  Existing tests that mock a single return value still
  pass because `_enrich_origins()` and `_enrich_metadata()` gracefully
  handle unparseable output.  Tests using `side_effect` lists need mock
  values for each expected call.  Note that `upgrade()` does NOT capture
  output (streams to terminal), so its `CompletedProcess` has `None` for
  stdout/stderr.
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
- **npm ``--dry-run`` output format.**  ``npm install -g <pkg> --dry-run``
  does NOT emit structured JSON with package arrays for global installs.
  Instead it prints **text progress lines** (``add <name> <version>``,
  ``change <name> <old> => <new>``, ``remove <name> <version>``) followed
  by a JSON summary containing only counts.  The npm adapter parses the
  text lines via regexes (``_ADD_RE``, ``_CHANGE_RE``, ``_REMOVE_RE``),
  not the JSON.  The ``--json`` flag is deliberately NOT passed in targeted
  mode.  Lines where ``old == new`` are reinstalls and are filtered out.
- **npm ``outdated`` exit code.**  ``npm outdated -g --json`` exits with
  code 1 when outdated packages exist (valid JSON on stdout).  The adapter
  uses ``check=False`` and validates JSON rather than the return code.
- **npm platform-specific optional deps.**  ``npm install --dry-run``
  reports ALL optional dependencies across every platform (darwin, win32,
  linux-arm64, wasm32, etc.), not just those that would install on the
  current system.  The adapter detects OS, architecture, and C library
  (glibc vs musl) tokens in package names via ``_is_foreign_platform()``
  and filters out packages meant for a different platform.  Only packages
  with ``current_version == "(new)"`` are filtered — version changes of
  already-installed packages are obviously on the right platform.  The
  libc detection uses ``ctypes.util.find_library("c")`` to distinguish
  musl from glibc on Linux.
- **npm targeted mode is atomic.**  In targeted mode (``--npm pkg@ver``),
  npm resolves the entire dependency tree as a unit.  Individual transitive
  dependencies cannot be cherry-picked for upgrade.  The decision flow in
  ``_collect_npm_targeted_decisions()`` presents a single yes/no prompt
  based on the worst recommendation across all packages.  The upgrade call
  uses ``adapter.upgrade()`` with no package list (not individual names),
  which runs the original ``npm install -g <target>`` command.
- **npm removed packages skipped.**  Packages that would be removed by the
  upgrade (``new_version == "(removed)"``) are excluded from the returned
  list entirely.  A removal cannot introduce a supply-chain threat, so
  there is nothing to security-review.
- **npm ``refresh_index()`` is a no-op.**  Unlike APT and Homebrew, npm has
  no local index to refresh — the registry is always live.
- **npm changelog retrieval.**  Same pattern as the Homebrew adapter:
  extract ``repository.url`` from ``npm view --json``, parse to
  ``owner/repo``, fetch GitHub Releases API.  The ``repository`` field
  may be a dict with a ``url`` key or a plain string — both are handled.
  The URL often has a ``git+https://`` prefix and ``.git`` suffix which
  the ``_GITHUB_RE`` regex handles.
