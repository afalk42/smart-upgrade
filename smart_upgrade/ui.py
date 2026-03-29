"""Terminal UI — rich-based output for progress, tables, and reports.

All user-facing output goes through this module so the rest of the codebase
stays free of presentation concerns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from smart_upgrade.models import (
    AnalysisResult,
    PendingUpgrade,
    Recommendation,
    RiskLevel,
)

console = Console()


# ---------------------------------------------------------------------------
# Colour mapping
# ---------------------------------------------------------------------------

_RISK_COLOURS = {
    RiskLevel.CLEAR: "green",
    RiskLevel.LOW: "yellow",
    RiskLevel.MEDIUM: "dark_orange",
    RiskLevel.HIGH: "red",
    RiskLevel.CRITICAL: "bold red",
}

_RISK_ICONS = {
    RiskLevel.CLEAR: "[green]CLEAR[/green]",
    RiskLevel.LOW: "[yellow]LOW[/yellow]",
    RiskLevel.MEDIUM: "[dark_orange]MEDIUM[/dark_orange]",
    RiskLevel.HIGH: "[red]HIGH[/red]",
    RiskLevel.CRITICAL: "[bold red]CRITICAL[/bold red]",
}

_REC_ICONS = {
    Recommendation.PROCEED: "[green]proceed[/green]",
    Recommendation.REVIEW: "[yellow]review recommended[/yellow]",
    Recommendation.BLOCK: "[bold red]BLOCKED[/bold red]",
}


# ---------------------------------------------------------------------------
# Progress context manager
# ---------------------------------------------------------------------------

def create_progress() -> Progress:
    """Create a :class:`rich.progress.Progress` instance for step tracking."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    )


# ---------------------------------------------------------------------------
# Step banners
# ---------------------------------------------------------------------------

def step(number: int, total: int, message: str) -> None:
    """Print a numbered step indicator: ``[1/5] message``."""
    console.print(f"[bold cyan][{number}/{total}][/bold cyan] {message}")


# ---------------------------------------------------------------------------
# Package list display
# ---------------------------------------------------------------------------

def show_pending_upgrades(
    packages: list[PendingUpgrade],
    whitelisted_names: set[str],
) -> None:
    """Display a table of pending upgrades, grouped by source."""
    from smart_upgrade.models import PackageSource

    groups: dict[str, list[PendingUpgrade]] = {}
    for pkg in packages:
        label = {
            PackageSource.APT: "APT Packages",
            PackageSource.BREW_FORMULA: "Homebrew Formulae",
            PackageSource.BREW_CASK: "Homebrew Casks",
        }.get(pkg.source, str(pkg.source))
        groups.setdefault(label, []).append(pkg)

    for group_name, group_pkgs in groups.items():
        table = Table(title=group_name, show_header=True, header_style="bold")
        table.add_column("Package", style="cyan", no_wrap=True)
        table.add_column("Installed", style="dim")
        table.add_column("", justify="center")
        table.add_column("Available", style="green")
        table.add_column("Whitelisted", justify="center")

        for pkg in sorted(group_pkgs, key=lambda p: p.name):
            wl = "[dim]yes[/dim]" if pkg.name in whitelisted_names else ""
            table.add_row(
                pkg.name,
                pkg.current_version,
                "->",
                pkg.new_version,
                wl,
            )

        console.print(table)
        console.print()


# ---------------------------------------------------------------------------
# Analysis report
# ---------------------------------------------------------------------------

def show_analysis_report(results: list[AnalysisResult]) -> None:
    """Display the security analysis report as a rich panel."""
    clear: list[AnalysisResult] = []
    review: list[AnalysisResult] = []
    blocked: list[AnalysisResult] = []

    for r in results:
        if r.recommendation == Recommendation.BLOCK:
            blocked.append(r)
        elif r.recommendation == Recommendation.REVIEW:
            review.append(r)
        else:
            clear.append(r)

    lines: list[str] = []

    # Clear packages
    if clear:
        names = ", ".join(r.package_name for r in clear)
        lines.append(f"[green]CLEAR ({len(clear)} packages):[/green]")
        lines.append(f"  {names}")
        lines.append("")

    # Review recommended
    if review:
        lines.append(f"[yellow]REVIEW RECOMMENDED ({len(review)} packages):[/yellow]")
        for r in review:
            lines.append(f"\n  [yellow]{r.package_name}[/yellow]")
            for f in r.findings:
                lines.append(f"    - {f.description} [{f.source}]")
            if r.details:
                for detail_line in r.details.strip().splitlines():
                    lines.append(f"    {detail_line}")
        lines.append("")

    # Blocked
    if blocked:
        lines.append(f"[bold red]BLOCKED ({len(blocked)} packages):[/bold red]")
        for r in blocked:
            lines.append(f"\n  [bold red]{r.package_name}[/bold red]")
            for f in r.findings:
                lines.append(f"    - {f.description} [{f.source}]")
            if r.details:
                for detail_line in r.details.strip().splitlines():
                    lines.append(f"    {detail_line}")
            lines.append("    RECOMMENDATION: Do not upgrade until verified")
        lines.append("")

    content = "\n".join(lines) if lines else "[green]No security concerns found.[/green]"
    panel = Panel(content, title="Security Analysis Report", border_style="bold")
    console.print(panel)


# ---------------------------------------------------------------------------
# User prompts
# ---------------------------------------------------------------------------

def prompt_upgrade_all() -> bool:
    """Ask the user whether to proceed with upgrading all clear packages.

    Returns
    -------
    bool
        True if the user approves.
    """
    console.print()
    answer = console.input("[bold]Proceed with upgrade? \\[Y/n]: [/bold]").strip().lower()
    return answer in ("y", "yes", "")


def prompt_package_decision(result: AnalysisResult) -> str:
    """Ask the user what to do with a flagged package.

    Returns
    -------
    str
        ``"y"`` (approve), ``"n"`` (reject), or ``"s"`` (skip).
    """
    risk_display = _RISK_ICONS.get(result.risk_level, str(result.risk_level))
    rec_display = _REC_ICONS.get(result.recommendation, str(result.recommendation))
    console.print(f"\n  [bold]{result.package_name}[/bold] -- Risk: {risk_display} | {rec_display}")

    if result.recommendation == Recommendation.BLOCK:
        prompt_text = "  Security concerns detected. Upgrade anyway? \\[y/N]: "
    else:
        prompt_text = "  Upgrade this package? \\[y/N/s(kip)]: "

    answer = console.input(prompt_text).strip().lower()

    if answer in ("y", "yes"):
        return "y"
    elif answer in ("s", "skip"):
        return "s"
    else:
        return "n"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def show_summary(
    upgraded: list[str],
    skipped: list[str],
    audit_path: Path | None,
) -> None:
    """Print the final summary after upgrades complete."""
    console.print()

    if upgraded:
        console.print(f"[green]Upgraded ({len(upgraded)}):[/green] {', '.join(upgraded)}")
    else:
        console.print("[dim]No packages were upgraded.[/dim]")

    if skipped:
        console.print(f"[yellow]Skipped ({len(skipped)}):[/yellow] {', '.join(skipped)}")

    if audit_path:
        console.print(f"\n[dim]Audit log saved: {audit_path}[/dim]")


def show_no_upgrades() -> None:
    """Print a message when no upgrades are available."""
    console.print("[green]All packages are up to date. Nothing to upgrade.[/green]")


def show_whitelist(whitelist_data: dict[str, list[str]]) -> None:
    """Display the current whitelist."""
    if not whitelist_data:
        console.print("[dim]No packages are whitelisted.[/dim]")
        return

    for section, patterns in whitelist_data.items():
        table = Table(title=section, show_header=False)
        table.add_column("Pattern", style="cyan")
        for p in patterns:
            table.add_row(p)
        console.print(table)
        console.print()


def show_error(message: str) -> None:
    """Display an error message."""
    console.print(f"[bold red]Error:[/bold red] {message}")


def show_warning(message: str) -> None:
    """Display a warning message."""
    console.print(f"[yellow]Warning:[/yellow] {message}")


def show_dry_run_notice() -> None:
    """Inform the user that this is a dry run."""
    console.print(Panel("[bold yellow]DRY RUN — no packages will be upgraded[/bold yellow]"))


def show_root_warning() -> None:
    """Warn that the tool should not be run as root / under sudo."""
    console.print(
        Panel(
            "[bold yellow]Warning:[/bold yellow] smart-upgrade is running as root.\n"
            "This is not recommended. Run as your normal user instead — the tool\n"
            "invokes sudo internally only for commands that require it.\n\n"
            "Usage: [bold]smart-upgrade[/bold] (not [dim]sudo smart-upgrade[/dim])",
            border_style="yellow",
        )
    )
