# smart-upgrade — Specification

> **Version:** 0.1.0 (Draft)
> **Author:** Alexander Falk
> **License:** MIT
> **Last Updated:** 2026-03-29

## 1. Overview

`smart-upgrade` is a security-aware command-line tool that wraps system package managers (`apt` on Debian/Ubuntu, `brew` on macOS) with an AI-powered security review layer. Instead of blindly upgrading packages, it:

1. Queries the package manager for pending upgrades.
2. Invokes Claude Code (via the `claude` CLI) to analyze each pending package for supply-chain threats.
3. Presents findings to the user and either proceeds with the upgrade or pauses for approval.

The tool is designed to counteract the rising trend of supply-chain attacks targeting open-source package repositories.

## 2. Motivation

Open-source supply-chain attacks are accelerating. Threat actors compromise maintainer accounts, inject malicious code into legitimate packages, or create typosquatted clones. Traditional package managers have no mechanism to flag suspicious upstream changes between versions. `smart-upgrade` fills this gap by inserting an AI-driven security review between "check for updates" and "install updates."

## 3. Supported Platforms & Package Managers

| Platform                      | Package Manager | Scope                          |
| ----------------------------- | --------------- | ------------------------------ |
| macOS                         | Homebrew        | Formulae and Casks             |
| Debian / Ubuntu               | APT             | `.deb` packages via `apt`      |
| Raspberry Pi OS (+ other Debian derivatives) | APT | `.deb` packages via `apt`  |

Raspberry Pi OS (formerly Raspbian) is Debian-based and is detected automatically via the `ID_LIKE=debian` field in `/etc/os-release`.  The APT adapter works identically on ARM (`armhf`, `arm64`) and x86 architectures.

Future versions may add support for `dnf`/`yum` (Fedora/RHEL), Snap, Flatpak, and language-level package managers (pip, npm, cargo).

## 4. Architecture

### 4.0 Privilege Separation

A critical design constraint: **`smart-upgrade` always runs as the regular user, never under `sudo`.**

On Linux, only the APT commands (`apt update`, `apt upgrade`) require root privileges. The tool invokes these via `sudo` internally (e.g., `subprocess.run(["sudo", "apt", "update", ...])`), which prompts the user for their password through the normal `sudo` mechanism. All other operations — configuration loading, Claude invocation, threat intelligence queries, audit logging — run unprivileged.

This design avoids the pitfalls of running Python inside `sudo` (which would break virtualenvs, `$HOME`-relative paths, and user-specific `claude` CLI configurations). On macOS, Homebrew already runs without `sudo`, so no elevation is needed at all.

**The user invokes the tool as:** `smart-upgrade` (never `sudo smart-upgrade`).

```
┌─────────────────────────────────────────────────────────┐
│                     smart-upgrade CLI                   │
│                  (Python 3.10+ entry point)             │
│                  (always runs as regular user)          │
├─────────────┬───────────────┬───────────────────────────┤
│  Platform   │   Package     │      Security             │
│  Detection  │   Manager     │      Analysis             │
│  Module     │   Adapters    │      Engine               │
│             │               │                           │
│  - OS ID    │  - APT        │  - Claude Code invoker    │
│  - Config   │    (sudo      │  - Brave Search client    │
│    loading  │     internal) │  - OSV / CVE client       │
│             │  - Brew       │  - Report generator       │
│             │    (no sudo)  │                           │
├─────────────┴───────┬───────┴───────────────────────────┤
│                     │                                   │
│    Whitelist &      │         Logging &                 │
│    Configuration    │         Audit Trail               │
│    Manager          │                                   │
└─────────────────────┴───────────────────────────────────┘
```

### 4.1 Module Breakdown

#### 4.1.1 Platform Detection Module

Detects the current operating system and selects the appropriate package manager adapter.

- Uses `platform.system()` and, on Linux, reads `/etc/os-release` to confirm a Debian-based distribution (Debian, Ubuntu, Raspberry Pi OS, etc.).
- Raises a clear error on unsupported platforms.

#### 4.1.2 Package Manager Adapters

Each adapter implements a common interface:

```python
class PackageManagerAdapter(Protocol):
    def refresh_index(self) -> None:
        """Update the local package index (e.g., `apt update`, `brew update`)."""
        ...

    def list_upgradable(self) -> list[PendingUpgrade]:
        """Return a list of packages that have upgrades available."""
        ...

    def upgrade(self, packages: list[str] | None = None) -> subprocess.CompletedProcess:
        """Perform the actual upgrade. If packages is None, upgrade all."""
        ...

    def get_package_info(self, package_name: str) -> PackageInfo:
        """Return metadata about a package (maintainer, homepage, source repo)."""
        ...
```

**APT Adapter:**
- `refresh_index()`: Runs `sudo apt update` (sudo invoked internally by the adapter).
- `list_upgradable()`: Parses output of `apt list --upgradable` (no sudo needed).
- `upgrade()`: Runs `sudo apt upgrade` or `sudo apt install <pkg1> <pkg2> ...` for selective upgrades (sudo invoked internally).
- `get_package_info()`: Runs `apt show <package>` and parses the result (no sudo needed).

**Brew Adapter:**
- `refresh_index()`: Runs `brew update`.
- `list_upgradable()`: Runs `brew outdated --json=v2` (provides structured output for both formulae and casks).
- `upgrade()`: Runs `brew upgrade` or `brew upgrade <pkg1> <pkg2> ...`.
- `get_package_info()`: Runs `brew info --json=v2 <package>` and parses the result.

#### 4.1.3 Security Analysis Engine

This is the core of `smart-upgrade`. It orchestrates three layers of analysis for each pending upgrade:

**Layer A — Package List Review:**
- Sends the full list of pending upgrades to Claude Code for a high-level risk assessment.
- Claude reviews package names, version jumps, and maintainer information for anomalies (e.g., unexpected major version bumps, new/changed maintainers, recently transferred ownership).

**Layer B — Threat Intelligence Lookup:**
- For each package (or at least those not on the whitelist), queries:
  - **Brave Search API**: Searches for recent news about security incidents, compromised maintainers, or supply-chain attacks related to the package name and its maintainer.
  - **OSV.dev API** (`https://api.osv.dev/v1/query`): Queries the Open Source Vulnerability database for known vulnerabilities affecting the new version.
  - **NVD/CVE feeds**: Checks NIST's National Vulnerability Database for CVEs associated with the package.
- Results are aggregated and passed to Claude Code for synthesis.

**Layer C — Changelog / Diff Review (Light mode):**
- For Brew packages: Retrieves the formula/cask diff between installed and available versions from the Homebrew GitHub repository, plus the upstream project's release notes or changelog.
- For APT packages: Retrieves the Debian changelog (`apt changelog <package>`) and, where available, the upstream changelog.
- Claude reviews the changelog entries for suspicious patterns (obfuscated code references, unusual binary additions, unexpected dependency changes, credential-handling modifications).

> **Future versions (Medium/Deep modes):** A `--review-depth` CLI option will allow users to select deeper analysis levels. "Medium" would clone the upstream repo and review commits between version tags. "Deep" would perform full source code analysis of changed files. These modes are documented here for planning purposes but are **out of scope for v0.1.0**.

#### 4.1.4 Claude Code Invoker

Handles all interaction with the `claude` CLI:

```python
class ClaudeInvoker:
    def __init__(self, model: str = "opus"):
        self.model = model

    def analyze(self, prompt: str, context: dict) -> AnalysisResult:
        """
        Invoke claude CLI in non-interactive mode.
        Returns structured analysis results.
        """
        ...
```

- Invokes `claude` using `subprocess` with the `-p` (print/non-interactive) flag.
- Passes `--model` flag based on configuration or CLI override.
- Uses carefully crafted system prompts that instruct Claude to:
  - Focus on supply-chain attack indicators.
  - Return structured output (JSON) with risk levels and findings.
  - Be specific about what is suspicious and why.
- Implements timeout handling and retry logic for robustness.

#### 4.1.5 Whitelist & Configuration Manager

Loads and manages the user's configuration and package whitelist.

- Config file location: `~/.config/smart-upgrade/config.yaml`
- Whitelist can be defined inline in the config or in a separate file.
- Whitelisted packages skip Layer B and Layer C analysis (but are still included in the Layer A overview).

#### 4.1.6 Logging & Audit Trail

Every run of `smart-upgrade` produces an audit log entry:

- Location: `~/.local/share/smart-upgrade/logs/`
- Format: One YAML file per run, named `YYYY-MM-DD_HH-MM-SS.yaml`
- Contents:
  - Timestamp, platform, package manager
  - Full list of pending upgrades
  - Analysis results per package (risk level, findings, sources consulted)
  - User decision (approved / rejected / skipped per package)
  - Final outcome (which packages were upgraded, which were skipped)

## 5. Data Models

```python
@dataclass
class PendingUpgrade:
    name: str                    # Package name
    current_version: str         # Currently installed version
    new_version: str             # Version available for upgrade
    source: str                  # "apt", "brew-formula", or "brew-cask"
    maintainer: str | None       # Package maintainer if available
    homepage: str | None         # Project homepage URL
    source_repo: str | None      # Source repository URL (e.g., GitHub)

@dataclass
class ThreatIntelResult:
    source: str                  # "brave_search", "osv", "nvd"
    query: str                   # What was searched
    findings: list[str]          # Summary of relevant findings
    raw_data: dict               # Full response for audit logging
    severity: str                # "none", "low", "medium", "high", "critical"

@dataclass
class AnalysisResult:
    package_name: str
    risk_level: str              # "clear", "low", "medium", "high", "critical"
    findings: list[Finding]
    recommendation: str          # "proceed", "review", "block"
    details: str                 # Human-readable explanation

@dataclass
class Finding:
    category: str                # "maintainer_change", "suspicious_code", "known_cve",
                                 # "supply_chain_news", "version_anomaly", etc.
    severity: str                # "info", "low", "medium", "high", "critical"
    description: str             # What was found
    source: str                  # Where the finding came from
    reference_url: str | None    # Link to advisory, news article, etc.
```

## 6. Command-Line Interface

```
usage: smart-upgrade [options]

Security-aware system package upgrade tool.

options:
  -h, --help                 Show this help message and exit
  -y, --yes                  Auto-approve upgrades when no security concerns are found
                             (mirrors apt upgrade -y behavior)
  --dry-run                  Perform analysis but do not execute any upgrades
  --model MODEL              Claude model to use for analysis
                             (default: opus; options: opus, sonnet, haiku)
  --review-depth DEPTH       Depth of source review (default: light)
                             (reserved for future: light, medium, deep)
  --config PATH              Path to config file
                             (default: ~/.config/smart-upgrade/config.yaml)
  --packages PKG [PKG ...]   Only consider specific packages for upgrade
  --show-whitelist           Display the current package whitelist and exit
  --log-level LEVEL          Logging verbosity (default: info)
                             (options: debug, info, warning, error)
  --version                  Show version and exit
```

### 6.1 Interactive Flow

```
$ smart-upgrade                    # Never run with sudo — the tool handles elevation internally

[1/5] Detecting platform... macOS (Homebrew)
[2/5] Refreshing package index... done (brew update)
[3/5] Checking for upgradable packages... found 12 upgrades

  Formulae (9):
    curl          8.7.1  →  8.8.0
    git           2.44.0 →  2.45.0
    openssl@3     3.3.0  →  3.3.1
    python@3.12   3.12.3 →  3.12.4
    ...

  Casks (3):
    firefox       125.0  →  126.0
    iterm2        3.5.2  →  3.5.3
    visual-studio-code  1.89 → 1.90

  Whitelisted (skipping deep analysis): curl, git, openssl@3, python@3.12

[4/5] Running security analysis...
  ├─ Querying threat intelligence databases... done
  ├─ Retrieving changelogs... done
  └─ Claude analysis in progress... done

╔══════════════════════════════════════════════════════════════╗
║                    SECURITY ANALYSIS REPORT                  ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  CLEAR (10 packages):                                        ║
║    curl, git, openssl@3, python@3.12, firefox, iterm2,       ║
║    visual-studio-code, ...                                   ║
║                                                              ║
║  ⚠ REVIEW RECOMMENDED (1 package):                           ║
║                                                              ║
║    some-package 2.0.0 → 3.0.0                                ║
║    ├─ Major version bump with maintainer change              ║
║    ├─ New maintainer account created 3 days ago              ║
║    └─ CVE-2026-XXXXX reported against v2.x (fixed in v3?)    ║
║                                                              ║
║  🛑 BLOCKED (1 package):                                     ║
║                                                              ║
║    suspicious-pkg 1.2.3 → 1.2.4                              ║
║    ├─ Brave Search: maintainer account reported compromised  ║
║    ├─ Changelog includes obfuscated base64 payload           ║
║    └─ RECOMMENDATION: Do not upgrade until verified          ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝

[5/5] Upgrade decision:

  Auto-approved (10 packages): curl, git, openssl@3, ...

  some-package 2.0.0 → 3.0.0 — Review recommended
  Upgrade this package? [y/N/s(kip)]: n

  suspicious-pkg 1.2.3 → 1.2.4 — BLOCKED
  ⚠ Security concerns detected. Upgrade anyway? [y/N]: n

Upgrading 10 packages... done.
Skipped: some-package, suspicious-pkg

Audit log saved: ~/.local/share/smart-upgrade/logs/2026-03-29_14-23-01.yaml
```

## 7. Configuration File

Location: `~/.config/smart-upgrade/config.yaml`

```yaml
# smart-upgrade configuration
# See: https://github.com/<owner>/smart-upgrade

# Claude model to use for security analysis
# Options: opus, sonnet, haiku
model: opus

# Default review depth
# Options: light (v0.1.0), medium (future), deep (future)
review_depth: light

# Auto-approve upgrades when no security concerns are found
# Equivalent to --yes flag. Can be overridden by CLI.
auto_approve: false

# Logging level: debug, info, warning, error
log_level: info

# Audit log directory
log_directory: ~/.local/share/smart-upgrade/logs

# Whitelisted packages skip Layer B (threat intel) and Layer C (changelog review).
# They are still included in the Layer A high-level overview.
# Use this for packages you trust implicitly (core OS packages, well-known tools).
whitelist:
  apt:
    - coreutils
    - libc6
    - libstdc++6
    - bash
    - systemd
    - apt
    - dpkg
    - linux-image-*      # Glob patterns supported
    - linux-headers-*

  brew:
    - curl
    - git
    - openssl@3
    - python@3.*
    - node
    - wget
    - coreutils

  brew-cask:
    - firefox
    - google-chrome
    - visual-studio-code
    - iterm2
    - docker

# Threat intelligence sources
# You can disable specific sources if needed.
threat_intel:
  brave_search:
    enabled: true
    # API key is read from environment variable BRAVE_SEARCH_API_KEY
    # Alternatively, set it here (not recommended for shared configs):
    # api_key: "your-key-here"

  osv:
    enabled: true
    # OSV.dev is free and requires no API key

  nvd:
    enabled: true
    # NVD API key (optional, but recommended to avoid rate limits)
    # Read from environment variable NVD_API_KEY, or set here:
    # api_key: "your-key-here"

# Timeouts (in seconds)
timeouts:
  package_index_refresh: 120
  claude_analysis: 300
  threat_intel_query: 30
  upgrade_execution: 600
```

## 8. Security Analysis Prompt Design

The prompts sent to Claude Code are critical to the tool's effectiveness. They will be stored as template files within the project and versioned alongside the code.

### 8.1 Layer A — Package List Review Prompt

```
You are a cybersecurity analyst specializing in software supply-chain security.
Review the following list of pending package upgrades and flag any anomalies.

Look for:
- Unexpected major version bumps
- Packages that are unusual or unexpected for this system type
- Known patterns associated with supply-chain attacks (typosquatting, etc.)
- Packages with very recent or very infrequent release histories

Return your analysis as JSON with this schema:
{
  "packages": [
    {
      "name": "...",
      "risk_level": "clear|low|medium|high|critical",
      "flags": ["list of concerns if any"],
      "notes": "brief explanation"
    }
  ],
  "overall_risk": "clear|low|medium|high|critical",
  "summary": "one paragraph overview"
}

Pending upgrades:
{{pending_upgrades_json}}
```

### 8.2 Layer B — Threat Intelligence Synthesis Prompt

```
You are a cybersecurity analyst. The following threat intelligence data was gathered
for package "{{package_name}}" (upgrading from {{old_version}} to {{new_version}}).

Analyze the data and determine if there is any indication of:
- Compromised maintainer accounts
- Known supply-chain attacks targeting this package
- Active CVEs that may indicate exploitation
- Any other security concerns

Brave Search results:
{{brave_results}}

OSV database results:
{{osv_results}}

NVD/CVE results:
{{nvd_results}}

Return JSON:
{
  "risk_level": "clear|low|medium|high|critical",
  "findings": [
    {
      "category": "...",
      "severity": "...",
      "description": "...",
      "source": "...",
      "reference_url": "..."
    }
  ],
  "recommendation": "proceed|review|block",
  "explanation": "..."
}
```

### 8.3 Layer C — Changelog Review Prompt

```
You are a cybersecurity analyst reviewing the changelog/diff for a package upgrade.
Look for indicators of supply-chain compromise:

- Obfuscated or encoded payloads (base64, hex-encoded strings)
- New network calls or connections to unknown endpoints
- Credential harvesting (reading env vars, SSH keys, browser storage, crypto wallets)
- Unexpected binary additions or modifications
- Changes to build scripts that download external resources
- Dependency additions that seem unrelated to the package's purpose
- Modifications to authentication, authorization, or cryptographic code

Package: {{package_name}}
Upgrade: {{old_version}} → {{new_version}}
Source: {{source_type}}

Changelog/Diff:
{{changelog_content}}

Return JSON with the same schema as Layer B.
```

## 9. Project Structure

```
smart-upgrade/
├── SPECIFICATION.md              # This file
├── LICENSE                       # MIT License
├── README.md                     # User-facing documentation
├── pyproject.toml                # Python project metadata & dependencies
├── setup.py                      # Optional: for editable installs
│
├── smart_upgrade/                # Main Python package
│   ├── __init__.py               # Package version and metadata
│   ├── __main__.py               # Entry point: `python -m smart_upgrade`
│   ├── cli.py                    # Argument parsing and CLI orchestration
│   ├── config.py                 # Configuration loading and validation
│   ├── platform_detect.py        # OS/platform detection
│   │
│   ├── adapters/                 # Package manager adapters
│   │   ├── __init__.py
│   │   ├── base.py               # Protocol / base class definition
│   │   ├── apt.py                # APT adapter for Debian/Ubuntu
│   │   └── brew.py               # Homebrew adapter (formulae + casks)
│   │
│   ├── analysis/                 # Security analysis engine
│   │   ├── __init__.py
│   │   ├── engine.py             # Orchestrates the three analysis layers
│   │   ├── claude_invoker.py     # Claude CLI wrapper
│   │   ├── threat_intel.py       # Brave Search, OSV, NVD clients
│   │   └── changelog.py          # Changelog/diff retrieval
│   │
│   ├── models.py                 # Data classes (PendingUpgrade, Finding, etc.)
│   ├── whitelist.py              # Whitelist matching logic (incl. glob patterns)
│   ├── audit.py                  # Audit log writer
│   ├── ui.py                     # Terminal output formatting and user prompts
│   │
│   └── prompts/                  # Claude prompt templates
│       ├── layer_a_review.txt
│       ├── layer_b_threat_intel.txt
│       └── layer_c_changelog.txt
│
├── tests/                        # Test suite
│   ├── __init__.py
│   ├── test_cli.py
│   ├── test_config.py
│   ├── test_platform_detect.py
│   ├── test_apt_adapter.py
│   ├── test_brew_adapter.py
│   ├── test_analysis_engine.py
│   ├── test_claude_invoker.py
│   ├── test_threat_intel.py
│   ├── test_whitelist.py
│   ├── test_audit.py
│   └── fixtures/                 # Test data (mock apt/brew output, etc.)
│       ├── apt_list_upgradable.txt
│       ├── brew_outdated.json
│       └── sample_changelog.txt
│
├── scripts/
│   └── smart-upgrade             # Shell wrapper for easy invocation
│
└── docs/
    ├── ARCHITECTURE.md           # Detailed architecture documentation
    ├── CONTRIBUTING.md           # Contribution guidelines
    └── THREAT_MODEL.md           # What threats this tool does/doesn't protect against
```

## 10. Dependencies

### 10.1 Design Principle: Minimal External Dependencies

To keep the tool lightweight, easy to install, and auditable, external dependencies are minimized. The Python standard library provides most of what is needed:

- **HTTP client**: `urllib.request` (stdlib) instead of `requests` — the threat intel API calls are simple REST GETs/POSTs that don't justify a third-party dependency.
- **JSON handling**: `json` (stdlib).
- **Subprocess management**: `subprocess` (stdlib).
- **Platform detection**: `platform` (stdlib).
- **Path handling**: `pathlib` (stdlib).
- **Date/time**: `datetime` (stdlib).
- **Argument parsing**: `argparse` (stdlib).

Only two external runtime dependencies are needed:

```
# Runtime
pyyaml >= 6.0            # Configuration file parsing (YAML is not in stdlib)
rich >= 13.0             # Terminal formatting: progress bars, tables, styled panels, color

# Development
pytest >= 8.0
pytest-mock >= 3.12
```

### 10.2 Why `rich` (not `textual`)

`rich` provides exactly the terminal output capabilities this tool needs: styled text, tables, progress bars, panels, and live-updating displays. `textual` (a full widget-based TUI framework built on `rich`) would be overkill — `smart-upgrade` follows a linear CLI flow, not an interactive application with focus management, mouse support, or screen layouts. Using `rich` directly keeps the dependency tree small and the code straightforward.

### 10.3 System Dependencies

- **`claude` CLI**: Must be installed separately and available on `$PATH`. The tool will check for its presence at startup and provide installation instructions if missing.
- **`sudo`** (Linux only): Required for APT operations. The tool invokes `sudo` internally; it does not require the user to run the tool itself under `sudo`.

## 11. Execution Flow (Detailed)

```
main()
  │
  ├─ 1. Parse CLI arguments
  ├─ 2. Load configuration (merge config file + CLI overrides)
  ├─ 3. Detect platform → select adapter
  │
  ├─ 4. adapter.refresh_index()
  │     └─ Runs `sudo apt update` (Linux) or `brew update` (macOS)
  │        Note: sudo is invoked internally by the adapter, not by the user
  │
  ├─ 5. adapter.list_upgradable()
  │     └─ Returns list[PendingUpgrade]
  │
  ├─ 6. If no upgrades available → print message and exit
  │
  ├─ 7. Display pending upgrades to user
  │     └─ Show table: package, current version, new version, source
  │
  ├─ 8. Partition packages into whitelisted vs. non-whitelisted
  │
  ├─ 9. Run Security Analysis Engine:
  │      │
  │      ├─ Layer A: Send full package list to Claude (all packages)
  │      │
  │      ├─ Layer B: For non-whitelisted packages:
  │      │   ├─ Query Brave Search for each package
  │      │   ├─ Query OSV.dev for each package
  │      │   ├─ Query NVD for each package
  │      │   └─ Send aggregated results to Claude for synthesis
  │      │
  │      └─ Layer C: For non-whitelisted packages:
  │          ├─ Retrieve changelog/diff for each package
  │          └─ Send to Claude for review
  │
  ├─ 10. Display Security Analysis Report
  │      ├─ Group packages by risk level
  │      ├─ Show findings for flagged packages
  │      └─ Show recommendations
  │
  ├─ 11. Collect user decisions:
  │      ├─ If --yes and all clear → auto-approve all
  │      ├─ If --yes and some flagged → auto-approve clear, prompt for flagged
  │      ├─ If no --yes → prompt for all (y/N default)
  │      └─ If --dry-run → skip upgrade, just report
  │
  ├─ 12. Execute upgrades for approved packages
  │      └─ adapter.upgrade(approved_packages)
  │
  ├─ 13. Write audit log
  │
  └─ 14. Print summary and exit
```

## 12. Error Handling

| Scenario                                         | Behavior                                                              |
| ------------------------------------------------ | --------------------------------------------------------------------- |
| `claude` CLI not found on PATH                   | Error message with installation instructions; abort (no `--skip-analysis` escape hatch — use `apt`/`brew` directly if Claude is unavailable) |
| Brave Search API key missing                     | Warning; skip Brave Search layer, continue with OSV + NVD             |
| NVD API key missing                              | Warning; proceed with rate-limited access                             |
| Claude analysis times out                        | Warning; present partial results; ask user how to proceed             |
| Network failure during threat intel              | Warning per source; continue with available data                      |
| Package manager command fails                    | Error; print stderr; abort with non-zero exit code                    |
| Unsupported platform detected                    | Error message listing supported platforms                             |
| Config file syntax error                         | Error with line number; fall back to defaults with warning            |
| `sudo` password prompt fails (Linux/APT)         | Error; remind user that `smart-upgrade` invokes `sudo` internally — they may need to ensure their user has sudo privileges |

## 13. Security Considerations

### 13.1 What This Tool Protects Against

- **Compromised maintainer accounts**: Detects via threat intelligence lookups and news search.
- **Malicious code injection**: Detects via changelog/diff review (Light mode).
- **Typosquatting**: Detects via Claude's package list review.
- **Known vulnerabilities**: Detects via OSV and NVD lookups.
- **Suspicious version patterns**: Detects via Claude's anomaly detection.

### 13.2 What This Tool Does NOT Protect Against

- **Zero-day supply-chain attacks with no public indicators**: If an attack is completely novel and unreported, the threat intelligence layer will miss it. The changelog review layer may still catch suspicious code patterns.
- **Attacks on the package manager infrastructure itself**: If `apt` or `brew` repositories are compromised at the distribution level, this tool operates on the same compromised data.
- **Sophisticated obfuscation**: Light mode reviews changelogs, not full source. A well-hidden backdoor in a large codebase may not be visible in a changelog.
- **Binary package tampering**: APT packages are pre-compiled. Without deep source analysis, binary-level tampering is not detectable by this tool.

### 13.3 Trust Model

- The tool trusts the local `claude` CLI installation.
- The tool trusts the HTTPS connections to Brave Search, OSV.dev, and NVD.
- API keys are read from environment variables (preferred) or the config file.
- Audit logs are written with read-only permissions for the current user.

### 13.4 Privilege Separation

The tool employs strict privilege separation:

- **All analysis, configuration, and UI logic runs as the regular user.** This includes Claude invocation, threat intelligence queries, config loading, and audit logging.
- **Only package manager commands that require root are run via `sudo`** (specifically `apt update` and `apt upgrade` on Debian/Ubuntu). The `sudo` call is made internally by the APT adapter.
- **On macOS, no elevation is ever needed.** Homebrew is designed to run without `sudo`.
- **The tool must never be invoked as `sudo smart-upgrade`.** If it detects it is running as root (and the platform is Linux), it should warn the user and suggest running without `sudo`.

This design ensures that:
1. Python virtualenvs work correctly (no `sudo` wiping `$PATH`).
2. User-specific configuration (`~/.config/...`) resolves to the correct home directory.
3. The `claude` CLI uses the user's own API credentials and configuration.
4. The attack surface of privileged code is minimized to only the necessary package manager operations.

## 14. Future Roadmap (Out of Scope for v0.1.0)

These features are documented for planning purposes:

1. **Medium review depth**: Clone upstream repos and review inter-version commits.
2. **Deep review depth**: Full source code analysis of changed files.
3. **Additional package managers**: dnf/yum, Snap, Flatpak, pip, npm, cargo.
4. **GitHub Advisory Database integration**: Direct queries to GitHub's security advisory API.
5. **Socket.dev integration**: Package analysis for known malicious patterns.
6. **Webhook/notification support**: Send alerts to Slack, email, or other channels.
7. **Scheduled/automated runs**: Cron-based operation with notification-only mode (no upgrade, just report).
8. **Package pinning**: Ability to pin specific packages to specific versions with security justification.
9. **Rollback support**: Track upgrades and provide easy rollback if a post-upgrade issue is discovered.
10. **Multi-machine dashboard**: Aggregate results across multiple machines running `smart-upgrade`.

## 15. Glossary

| Term | Definition |
|---|---|
| **Adapter** | A module implementing the package manager interface for a specific package manager (APT, Brew). |
| **Layer A/B/C** | The three stages of security analysis: (A) package list review, (B) threat intelligence, (C) changelog/diff review. |
| **Whitelist** | A user-configured list of trusted packages that skip deep security analysis. |
| **Finding** | A single security observation from any analysis layer, with a severity and category. |
| **Audit log** | A per-run YAML file recording everything that happened during the upgrade process. |
| **Light mode** | The default review depth — analyzes changelogs and release notes, not full source code. |
