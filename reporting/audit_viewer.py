"""
reporting/audit_viewer.py — Per-case audit trail viewer.

Usage:
    python -m reporting.audit_viewer                    # List recent cases
    python -m reporting.audit_viewer --case-id <id>     # Full audit trail for one case
    python -m reporting.audit_viewer --workflow w1       # All cases for a workflow
    python -m reporting.audit_viewer --pending-approvals # Show pending human approvals
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from core.audit import AuditStore

DB_PATH = Path("output/audit.db")


def _fmt_ts(ts_str: str) -> str:
    try:
        return datetime.fromisoformat(ts_str).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts_str


def _fmt_payload(payload_json: str, indent: int = 6) -> str:
    try:
        data = json.loads(payload_json)
        lines = json.dumps(data, indent=2).split("\n")
        pad = " " * indent
        return "\n".join(pad + line for line in lines)
    except Exception:
        return payload_json


def print_case_audit(case_id: str, store: AuditStore) -> None:
    case = store.get_case(case_id)
    if not case:
        print(f"  Case not found: {case_id}")
        return

    entries = store.get_case_audit(case_id)
    events = store.get_payment_events_for_case(case_id)

    print(f"\n{'═' * 70}")
    print(f"  CASE AUDIT TRAIL")
    print(f"{'═' * 70}")
    print(f"  Case ID:    {case_id}")
    print(f"  Workflow:   {case['workflow']}")
    print(f"  Account:    {case['account_id']}")
    print(f"  Status:     {case['status'].upper()}")
    print(f"  At Risk:    ${case['revenue_at_risk']:,.2f}")
    print(f"  Recovered:  ${case['revenue_recovered']:,.2f}")
    print(f"  Opened:     {_fmt_ts(case['opened_at'])}")
    if case.get("closed_at"):
        print(f"  Closed:     {_fmt_ts(case['closed_at'])}")
    if case.get("close_reason"):
        print(f"  Reason:     {case['close_reason']}")
    print(f"  Attempts:   {case['attempt_count']}")
    print()

    if not entries:
        print("  No audit entries found.")
        return

    for i, entry in enumerate(entries):
        stage = entry["stage"].upper()
        ts = _fmt_ts(entry["ts_utc"])
        attempt = entry["attempt_num"]
        operator = entry["operator_id"]
        model_ver = entry["model_version"]

        stage_color = {
            "DETECT": "→",
            "DIAGNOSE": "🔍",
            "DECIDE": "⚡",
            "ACT": "▶",
            "MEASURE": "📊",
            "STOP": "🛑",
            "AUDIT": "📋",
        }.get(stage, "•")

        print(f"  {stage_color} [{ts}] Stage={stage:10s} Attempt={attempt}  Operator={operator}  Model={model_ver}")
        print(_fmt_payload(entry["payload_json"]))
        print()

    if events:
        print(f"  {'─' * 60}")
        print(f"  PAYMENT EVENTS ({len(events)})")
        for evt in events:
            status_icon = "✅" if evt["status"] == "success" else "❌"
            print(
                f"  {status_icon} [{_fmt_ts(evt['occurred_at'])}]  "
                f"${evt['amount']:,.2f}  {evt['currency']}  "
                f"status={evt['status']}  processor={evt['processor']}"
            )
    print(f"\n{'═' * 70}\n")


def print_recent_cases(store: AuditStore, limit: int = 20) -> None:
    cases = store.get_all_cases()
    cases = sorted(cases, key=lambda c: c["opened_at"], reverse=True)[:limit]

    print(f"\n{'═' * 100}")
    print(f"  {'CASE_ID':<38} {'WORKFLOW':<28} {'STATUS':<18} {'AT RISK':>10}  {'RECOVERED':>10}  {'ATT':>4}")
    print(f"{'─' * 100}")
    for c in cases:
        wf = c["workflow"][:26]
        status = c["status"]
        risk = f"${c['revenue_at_risk']:,.0f}"
        recovered = f"${c['revenue_recovered']:,.0f}"
        attempts = c["attempt_count"]
        print(f"  {c['case_id']:<38} {wf:<28} {status:<18} {risk:>10}  {recovered:>10}  {attempts:>4}")
    print(f"{'═' * 100}\n")


def print_workflow_summary(workflow: str, store: AuditStore) -> None:
    # Normalize workflow key to full name if needed
    wf_map = {
        "w1": "payment_degradation",
        "w2": "checkout_abandonment",
        "w3": "subscription_recovery",
        "w4": "b2b_receivables",
        "w5": "mandate_retry",
        "w6": "hinglish_voice",
        "w7": "promise_to_pay",
    }
    wf_name = wf_map.get(workflow, workflow)
    cases = store.get_cases_by_workflow(wf_name)

    print(f"\n  Workflow: {wf_name} — {len(cases)} cases\n")
    status_counts: dict[str, int] = {}
    for c in cases:
        status_counts[c["status"]] = status_counts.get(c["status"], 0) + 1
    for status, count in sorted(status_counts.items()):
        print(f"    {status:<20} {count}")
    print()


def print_suppressed_actions(store: AuditStore) -> None:
    entries = store.get_recent_entries(limit=500)
    suppressed = [e for e in entries if e["stage"] == "suppressed"]

    print(f"\n{'═' * 85}")
    print(f"  SUPPRESSED-ACTIONS LEDGER ('DELIBERATELY DID NOT DO') — {len(suppressed)} EVENTS")
    print(f"{'═' * 85}")
    print(f"  {'TIMESTAMP':<20} {'CASE ID':<24} {'WORKFLOW':<22} {'SUPPRESSION REASON'}")
    print(f"{'─' * 85}")

    for e in suppressed:
        ts = _fmt_ts(e["ts_utc"])
        payload = json.loads(e["payload_json"])
        reason = payload.get("suppression_reason", payload.get("detail", "unknown"))
        detail = payload.get("detail", payload.get("error", ""))
        print(f"  {ts:<20} {e['case_id'][:22]:<24} {e['workflow'][:20]:<22} [yellow]{reason}[/yellow]")
        if detail:
            print(f"    └─ Details: {detail[:70]}")

    print(f"{'═' * 85}\n")


def print_holdout_analysis(store: AuditStore) -> None:
    cases = store.get_all_cases()
    treatment = [c for c in cases if c.get("group_name") == "treatment"]
    holdout = [c for c in cases if c.get("group_name") == "holdout"]

    treatment_risk = sum(c["revenue_at_risk"] for c in treatment)
    holdout_risk = sum(c["revenue_at_risk"] for c in holdout)

    treatment_rec = sum(c["revenue_recovered"] for c in treatment)
    holdout_rec = sum(c["revenue_recovered"] for c in holdout)

    treatment_cost = sum(c.get("total_intervention_cost", 0.0) for c in treatment)
    net_rec = max(0.0, treatment_rec - treatment_cost)

    treatment_rate = (treatment_rec / treatment_risk * 100) if treatment_risk > 0 else 0.0
    holdout_rate = (holdout_rec / holdout_risk * 100) if holdout_risk > 0 else 0.0
    lift_pct = max(0.0, treatment_rate - holdout_rate)
    incremental_rec = max(0.0, (lift_pct / 100.0) * treatment_risk)

    print(f"\n{'═' * 70}")
    print("  INCREMENTALITY & UNIT ECONOMICS PROOF (TREATMENT VS HOLDOUT)")
    print(f"{'═' * 70}")
    print(f"  Treatment Cases (Agent Active):   {len(treatment):>10,}")
    print(f"  Holdout Cases (Control Group):    {len(holdout):>10,}")
    print(f"  Treatment Gross Recovered:        ${treatment_rec:>12,.2f} ({treatment_rate:.1f}% rate)")
    print(f"  Holdout Organic Baseline:         ${holdout_rec:>12,.2f} ({holdout_rate:.1f}% rate)")
    print(f"  Causal Incremental Lift:          +{lift_pct:>11.1f}%")
    print(f"  Incremental Revenue Recovered:    ${incremental_rec:>12,.2f}")
    print(f"  Total Intervention Cost:          ${treatment_cost:>12,.2f}")
    print(f"  NET REVENUE RECOVERED:            ${net_rec:>12,.2f} (Gross − Cost)")
    print(f"{'═' * 70}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Revenue Recovery — Audit Viewer")
    parser.add_argument("--case-id", help="Show full audit trail for one case")
    parser.add_argument("--workflow", help="Show all cases for a workflow (w1–w7)")
    parser.add_argument("--pending-approvals", action="store_true", help="Show pending human approvals")
    parser.add_argument("--suppressed-actions", action="store_true", help="Show suppressed actions ledger (deliberately blocked actions)")
    parser.add_argument("--holdout-analysis", action="store_true", help="Show holdout control group vs treatment incrementality analysis")
    parser.add_argument("--recent", type=int, default=20, help="Show N most recent cases (default 20)")
    parser.add_argument("--db", help="Path to audit DB", default="output/audit.db")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"  Audit DB not found at {db_path}. Run the batch runner first.")
        sys.exit(1)

    store = AuditStore(db_path)

    if args.case_id:
        print_case_audit(args.case_id, store)
    elif args.workflow:
        print_workflow_summary(args.workflow, store)
    elif args.pending_approvals:
        print_pending_approvals(store)
    elif args.suppressed_actions:
        print_suppressed_actions(store)
    elif args.holdout_analysis:
        print_holdout_analysis(store)
    else:
        print_recent_cases(store, limit=args.recent)

    store.close()


if __name__ == "__main__":
    main()
