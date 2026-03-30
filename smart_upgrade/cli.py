"""CLI entry point and main orchestration logic.

This module ties together every component:
1. Parse CLI arguments
2. Load configuration
3. Detect platform and select the package-manager adapter
4. Refresh the package index
5. List upgradable packages
6. Run the security analysis engine
7. Present findings and collect user decisions
8. Execute approved upgrades
9. Write the audit log
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from smart_upgrade import __version__
from smart_upgrade.analysis.claude_invoker import ClaudeNotFoundError
from smart_upgrade.analysis.engine import AnalysisEngine
from smart_upgrade.audit import build_audit_entry, write_audit_log
from smart_upgrade.config import Config, apply_cli_overrides, load_config
from smart_upgrade.models import (
    AnalysisResult,
    PendingUpgrade,
    Recommendation,
    UpgradeDecision,
)
from smart_upgrade.platform_detect import (
    UnsupportedPlatformError,
    detect_platform,
    is_running_as_root,
)
from smart_upgrade.ui import (
    console,
    create_progress,
    show_analysis_report,
    show_dry_run_notice,
    show_error,
    show_no_upgrades,
    show_pending_upgrades,
    show_root_warning,
    show_summary,
    show_warning,
    show_whitelist,
    step,
)
from smart_upgrade.whitelist import format_whitelist_display, partition_packages

logger = logging.getLogger("smart_upgrade")

TOTAL_STEPS = 5


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="smart-upgrade",
        description="Security-aware system package upgrade tool powered by Claude AI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  smart-upgrade                  # Interactive upgrade with security review\n"
            "  smart-upgrade -y               # Auto-approve if all clear\n"
            "  smart-upgrade --dry-run        # Analyse only, don't upgrade\n"
            "  smart-upgrade --model sonnet   # Use a faster/cheaper model\n"
        ),
    )

    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        default=None,
        help="Auto-approve upgrades when no security concerns are found",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform analysis but do not execute any upgrades",
    )
    parser.add_argument(
        "--model",
        choices=["opus", "sonnet", "haiku"],
        default=None,
        help="Claude model for analysis (default: opus)",
    )
    parser.add_argument(
        "--review-depth",
        choices=["light"],
        default=None,
        help="Depth of source review (default: light; medium/deep reserved for future)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config file (default: ~/.config/smart-upgrade/config.yaml)",
    )
    parser.add_argument(
        "--packages",
        nargs="+",
        metavar="PKG",
        default=None,
        help="Only consider specific packages for upgrade",
    )
    parser.add_argument(
        "--show-whitelist",
        action="store_true",
        help="Display the current package whitelist and exit",
    )
    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default=None,
        help="Logging verbosity (default: info)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    return parser


# ---------------------------------------------------------------------------
# Adapter factory
# ---------------------------------------------------------------------------

def _create_adapter(platform_id: str):
    """Instantiate the correct package-manager adapter."""
    if platform_id == "macos":
        from smart_upgrade.adapters.brew import BrewAdapter
        return BrewAdapter()
    elif platform_id == "linux-apt":
        from smart_upgrade.adapters.apt import AptAdapter
        return AptAdapter()
    else:
        raise UnsupportedPlatformError(f"No adapter for platform: {platform_id}")


# ---------------------------------------------------------------------------
# Decision collection
# ---------------------------------------------------------------------------

def _collect_decisions(
    packages: list[PendingUpgrade],
    results: list[AnalysisResult],
    auto_approve: bool,
    dry_run: bool,
) -> list[UpgradeDecision]:
    """Prompt the user (or auto-approve) and return decisions for each package."""
    from smart_upgrade.ui import prompt_package_decision, prompt_upgrade_all

    result_map = {r.package_name: r for r in results}
    decisions: list[UpgradeDecision] = []

    # Separate packages by recommendation.
    clear_pkgs: list[PendingUpgrade] = []
    flagged_pkgs: list[PendingUpgrade] = []

    for pkg in packages:
        r = result_map.get(pkg.name)
        if r and r.recommendation != Recommendation.PROCEED:
            flagged_pkgs.append(pkg)
        else:
            clear_pkgs.append(pkg)

    # --- Handle clear packages ---
    if dry_run:
        for pkg in clear_pkgs:
            decisions.append(UpgradeDecision(
                package=pkg,
                analysis=result_map.get(pkg.name),
                approved=False,
                skipped_reason="dry run",
            ))
    elif auto_approve:
        for pkg in clear_pkgs:
            decisions.append(UpgradeDecision(
                package=pkg,
                analysis=result_map.get(pkg.name),
                approved=True,
            ))
    else:
        if clear_pkgs:
            console.print(
                f"\n[green]{len(clear_pkgs)} packages[/green] passed security review with no concerns."
            )
            approved = prompt_upgrade_all()
            for pkg in clear_pkgs:
                decisions.append(UpgradeDecision(
                    package=pkg,
                    analysis=result_map.get(pkg.name),
                    approved=approved,
                    skipped_reason=None if approved else "user declined",
                ))

    # --- Handle flagged packages (always prompt, even with -y) ---
    if flagged_pkgs and dry_run:
        for pkg in flagged_pkgs:
            decisions.append(UpgradeDecision(
                package=pkg,
                analysis=result_map.get(pkg.name),
                approved=False,
                skipped_reason="dry run",
            ))
    elif flagged_pkgs:
        console.print(
            f"\n[yellow]{len(flagged_pkgs)} packages[/yellow] have security concerns and require your decision:"
        )
        for pkg in flagged_pkgs:
            r = result_map.get(pkg.name)
            if r is None:
                continue
            answer = prompt_package_decision(r)
            if answer == "y":
                decisions.append(UpgradeDecision(package=pkg, analysis=r, approved=True))
            elif answer == "s":
                decisions.append(UpgradeDecision(package=pkg, analysis=r, approved=False, skipped_reason="user skipped"))
            else:
                decisions.append(UpgradeDecision(package=pkg, analysis=r, approved=False, skipped_reason="user rejected"))

    return decisions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Entry point for smart-upgrade.

    Returns
    -------
    int
        Exit code (0 on success, non-zero on failure).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # --- Load configuration ---
    try:
        config = load_config(args.config)
    except Exception as exc:
        show_error(f"Failed to load config: {exc}")
        return 1

    config = apply_cli_overrides(
        config,
        model=args.model,
        yes=args.yes,
        log_level=args.log_level,
        review_depth=args.review_depth,
    )

    # --- Set up logging ---
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # --- Show whitelist and exit if requested ---
    if args.show_whitelist:
        show_whitelist(format_whitelist_display(config.whitelist))
        return 0

    # --- Warn if running as root ---
    if is_running_as_root():
        show_root_warning()

    # --- Dry run banner ---
    if args.dry_run:
        show_dry_run_notice()

    # ======================================================================
    # Step 1: Detect platform
    # ======================================================================
    step(1, TOTAL_STEPS, "Detecting platform...")
    try:
        platform_id = detect_platform()
    except UnsupportedPlatformError as exc:
        show_error(str(exc))
        return 1

    adapter = _create_adapter(platform_id)
    console.print(f"  Platform: [bold]{platform_id}[/bold] ({adapter.name})")

    # ======================================================================
    # Step 2: Refresh package index
    # ======================================================================
    step(2, TOTAL_STEPS, f"Refreshing package index ({adapter.name})...")
    try:
        with create_progress() as progress:
            task = progress.add_task(f"Running {adapter.name} update...", total=None)
            adapter.refresh_index()
            progress.update(task, completed=True)
    except RuntimeError as exc:
        show_error(str(exc))
        return 1

    # ======================================================================
    # Step 3: List upgradable packages
    # ======================================================================
    step(3, TOTAL_STEPS, "Checking for upgradable packages...")
    try:
        packages = adapter.list_upgradable()
    except RuntimeError as exc:
        show_error(str(exc))
        return 1

    # Filter to requested packages if --packages was given.
    if args.packages:
        requested = set(args.packages)
        packages = [p for p in packages if p.name in requested]

    if not packages:
        show_no_upgrades()
        return 0

    console.print(f"  Found [bold]{len(packages)}[/bold] upgradable packages.\n")

    # Partition whitelist.
    _, _, whitelisted_names = partition_packages(packages, config.whitelist)
    show_pending_upgrades(packages, whitelisted_names)

    if whitelisted_names:
        console.print(
            f"  [dim]Whitelisted ({len(whitelisted_names)}): {', '.join(sorted(whitelisted_names))} "
            f"(skipping deep analysis)[/dim]\n"
        )

    # ======================================================================
    # Step 4: Security analysis
    # ======================================================================
    step(4, TOTAL_STEPS, "Running security analysis...")

    def _analysis_progress(stage: str, detail: str) -> None:
        logger.info("[%s] %s", stage, detail)
        if stage == "package_start":
            console.print(f"  [bold cyan]Evaluating possible upgrade:[/bold cyan] {detail}")

    results: list[AnalysisResult] = []
    try:
        engine = AnalysisEngine(
            config=config,
            adapter=adapter,
            progress_callback=_analysis_progress,
        )
        results = engine.analyze(packages, whitelisted_names)
    except ClaudeNotFoundError as exc:
        show_error(str(exc))
        return 1
    except Exception as exc:
        show_warning(f"Security analysis encountered an error: {exc}")
        show_warning("Partial results may be shown below.")

    show_analysis_report(results)

    # ======================================================================
    # Step 5: Collect decisions and upgrade
    # ======================================================================
    step(5, TOTAL_STEPS, "Upgrade decision...")

    decisions = _collect_decisions(
        packages=packages,
        results=results,
        auto_approve=config.auto_approve,
        dry_run=args.dry_run,
    )

    approved_names = [d.package.name for d in decisions if d.approved]
    skipped_names = [d.package.name for d in decisions if not d.approved]

    # --- Execute upgrades ---
    errors: list[str] = []
    if approved_names and not args.dry_run:
        console.print(f"\n[bold]Upgrading {len(approved_names)} packages...[/bold]")
        try:
            result = adapter.upgrade(approved_names)
            if result.returncode != 0:
                # stderr may be None when the adapter streams output
                # directly to the terminal (e.g. APT).
                stderr_text = ""
                if result.stderr:
                    stderr_text = (
                        result.stderr.strip()
                        if isinstance(result.stderr, str)
                        else result.stderr.decode(errors="replace").strip()
                    )
                err_msg = f"Upgrade returned exit code {result.returncode}"
                if stderr_text:
                    err_msg += f": {stderr_text}"
                show_warning(err_msg)
                errors.append(err_msg)
        except Exception as exc:
            err_msg = f"Upgrade failed: {exc}"
            show_error(err_msg)
            errors.append(err_msg)

    # --- Write audit log ---
    audit_path = None
    try:
        entry = build_audit_entry(
            platform=platform_id,
            package_manager=adapter.name,
            pending=packages,
            results=results,
            decisions=decisions,
            upgraded=approved_names if not args.dry_run else [],
            skipped=skipped_names,
            errors=errors,
        )
        audit_path = write_audit_log(entry, config.log_directory)
    except Exception as exc:
        show_warning(f"Failed to write audit log: {exc}")

    # --- Final summary ---
    show_summary(
        upgraded=approved_names if not args.dry_run else [],
        skipped=skipped_names,
        audit_path=audit_path,
    )

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
