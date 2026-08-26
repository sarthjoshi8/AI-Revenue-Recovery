"""
reporting/dashboard.py — Rich terminal dashboard for AI Revenue Recovery.

Uses the `rich` library to render a live, beautiful terminal dashboard of:
  - Executive KPI summary cards
  - Per-workflow recovery performance table
  - Guardrail enforcement stats
  - Audit trail feed

Usage:
    python -m reporting.dashboard
    python -m reporting.dashboard --db output/audit.db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.layout import Layout
from rich.columns import Columns

from core.audit import AuditStore

console = Console()


def render_dashboard(db_path: Path = Path("output/audit.db")) -> None:
    if not db_path.exists():
        console.print(f"[bold red]Audit database not found at {db_path}.[/bold red]")
        console.print("Run [bold cyan]python -m runner.batch_runner[/bold cyan] first to generate data.")
        return

    store = AuditStore(db_path)
    cases = store.get_all_cases()
    recent_entries = store.get_recent_entries(limit=15)
    pending_approvals = store.get_pending_approvals()

    if not cases:
        console.print("[yellow]No cases found in audit database.[/yellow]")
        store.close()
        return

    # Calculate metrics
    total_cases = len(cases)
    treatment_cases = [c for c in cases if c.get("group_name") == "treatment"]
    holdout_cases = [c for c in cases if c.get("group_name") == "holdout"]

    total_risk = sum(c["revenue_at_risk"] for c in cases)
    treatment_risk = sum(c["revenue_at_risk"] for c in treatment_cases)
    holdout_risk = sum(c["revenue_at_risk"] for c in holdout_cases)

    gross_recovered = sum(c["revenue_recovered"] for c in treatment_cases)
    holdout_recovered = sum(c["revenue_recovered"] for c in holdout_cases)
    total_cost = sum(c.get("total_intervention_cost", 0.0) for c in treatment_cases)
    net_recovered = max(0.0, gross_recovered - total_cost)

    treatment_rate = (gross_recovered / treatment_risk * 100) if treatment_risk > 0 else 0.0
    holdout_rate = (holdout_recovered / holdout_risk * 100) if holdout_risk > 0 else 0.0
    lift_pct = max(0.0, treatment_rate - holdout_rate)
    incremental_rec = max(0.0, (lift_pct / 100.0) * treatment_risk)

    suppressed_count = sum(1 for e in recent_entries if e["stage"] == "suppressed")
    capped_cases = sum(1 for c in cases if c["status"] == "capped")

    # Header
    console.clear()
    console.print(
        Panel.fit(
            "[bold white]⚡ AI REVENUE RECOVERY — CAUSAL INCREMENTALITY & NET RECOVERY DASHBOARD[/bold white]\n"
            "[dim]Proving Causal Lift vs. Holdout Control Groups • Reporting Net Revenue (Gross − Cost) • Suppressed-Actions Ledger[/dim]",
            border_style="bright_blue",
        )
    )

    # Key metrics cards
    kpi_cards = [
        Panel(f"[bold white]${gross_recovered:,.2f}[/bold white]\n[dim]Treatment ({treatment_rate:.1f}% rate)[/dim]", title="[dim]Gross Recovered[/dim]", border_style="blue"),
        Panel(f"[bold green]${incremental_rec:,.2f}[/bold green]\n[dim]+{lift_pct:.1f}% vs Control Baseline[/dim]", title="[dim]Causal Incremental Lift[/dim]", border_style="green"),
        Panel(f"[bold cyan]${net_recovered:,.2f}[/bold cyan]\n[dim]Gross − Cost (${total_cost:,.2f})[/dim]", title="[dim]NET REVENUE RECOVERED[/dim]", border_style="cyan"),
        Panel(f"[bold yellow]{len(holdout_cases)}[/bold yellow]\n[dim]Control ({holdout_rate:.1f}% organic)[/dim]", title="[dim]Holdout Group[/dim]", border_style="yellow"),
        Panel(f"[bold red]{len(pending_approvals)}[/bold red]\n[dim]Approval Gates Active[/dim]", title="[dim]Pending Approvals[/dim]", border_style="red"),
    ]
    console.print(Columns(kpi_cards))
    console.print()

    # Workflow Breakdown Table
    table = Table(title="Workflow Incrementality & Net Unit Economics", border_style="bright_blue", header_style="bold magenta")
    table.add_column("Workflow", style="cyan", no_wrap=True)
    table.add_column("Treatment / Holdout", justify="right")
    table.add_column("Gross Recovered", justify="right", style="blue")
    table.add_column("Holdout Baseline", justify="right", style="yellow")
    table.add_column("Causal Lift", justify="right", style="green")
    table.add_column("Intervention Cost", justify="right", style="red")
    table.add_column("NET RECOVERED", justify="right", style="bold green")

    workflows = sorted(list({c["workflow"] for c in cases}))
    for wf in workflows:
        wf_cases = [c for c in cases if c["workflow"] == wf]
        wf_treat = [c for c in wf_cases if c.get("group_name") == "treatment"]
        wf_hold = [c for c in wf_cases if c.get("group_name") == "holdout"]

        wf_treat_risk = sum(c["revenue_at_risk"] for c in wf_treat)
        wf_hold_risk = sum(c["revenue_at_risk"] for c in wf_hold)

        wf_gross = sum(c["revenue_recovered"] for c in wf_treat)
        wf_hold_rec = sum(c["revenue_recovered"] for c in wf_hold)
        wf_cost = sum(c.get("total_intervention_cost", 0.0) for c in wf_treat)
        wf_net = max(0.0, wf_gross - wf_cost)

        t_rate = (wf_gross / wf_treat_risk * 100) if wf_treat_risk > 0 else 0.0
        h_rate = (wf_hold_rec / wf_hold_risk * 100) if wf_hold_risk > 0 else 0.0
        w_lift_pct = max(0.0, t_rate - h_rate)
        w_inc_rec = max(0.0, (w_lift_pct / 100.0) * wf_treat_risk)

        table.add_row(
            wf,
            f"{len(wf_treat)} / {len(wf_hold)}",
            f"${wf_gross:,.2f}",
            f"${wf_hold_rec:,.2f} ({h_rate:.1f}%)",
            f"+{w_lift_pct:.1f}% (${w_inc_rec:,.2f})",
            f"${wf_cost:,.2f}",
            f"${wf_net:,.2f}",
        )

    console.print(table)
    console.print()

    # Recent Audit Log Feed
    audit_table = Table(title="Recent Audit Log Feed (Immutable Append-Only Log)", border_style="dim white")
    audit_table.add_column("Timestamp", style="dim")
    audit_table.add_column("Case ID", style="dim cyan")
    audit_table.add_column("Workflow")
    audit_table.add_column("Stage", style="bold")
    audit_table.add_column("Payload Summary", style="white")

    for e in recent_entries:
        payload = json.loads(e["payload_json"])
        summary = ""
        stage = e["stage"].upper()
        if stage == "DETECT":
            summary = f"Risk: ${payload.get('revenue_at_risk', 0):,.2f}"
        elif stage == "DIAGNOSE":
            summary = f"Cause: {payload.get('cause_code', '?')} (conf={payload.get('confidence', 0):.2f})"
        elif stage == "DECIDE":
            if payload.get("gate") == "human_approval_required":
                summary = f"[bold yellow]APPROVAL GATE: {payload.get('action_type', '?')}[/bold yellow]"
            else:
                summary = f"Action: {payload.get('action_type', '?')}"
        elif stage == "ACT":
            status_str = "SUCCESS" if payload.get("success") else "FAILED"
            summary = f"Act: {payload.get('action_type', '?')} ({status_str})"
        elif stage == "MEASURE":
            if payload.get("recovered"):
                summary = f"[bold green]RECOVERED ${payload.get('amount', 0):,.2f}[/bold green]"
            else:
                summary = "Measurement: Pending recovery"
        elif stage == "STOP":
            summary = f"[bold red]STOP: {payload.get('stop_type', '?')}[/bold red] - {payload.get('reason', '')[:40]}"

        audit_table.add_row(
            e["ts_utc"][:19],
            e["case_id"][:12] + "…",
            e["workflow"][:20],
            stage,
            summary,
        )

    console.print(audit_table)
    store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Revenue Recovery — Rich Terminal Dashboard")
    parser.add_argument("--db", default="output/audit.db", help="Path to audit DB")
    args = parser.parse_args()
    render_dashboard(Path(args.db))


if __name__ == "__main__":
    main()
