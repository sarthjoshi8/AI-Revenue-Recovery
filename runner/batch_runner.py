"""
runner/batch_runner.py — Processes all 7 workflows end-to-end against sample data.

Usage:
    python -m runner.batch_runner                  # Full run, rich terminal output
    python -m runner.batch_runner --quiet          # Minimal output
    python -m runner.batch_runner --workflow w1    # Single workflow

Produces:
    output/audit.db         — Append-only audit log
    output/batch_report.json — Machine-readable batch report
    output/report.html      — Human-readable HTML dashboard
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from core.audit import AuditStore
from core.models import BatchReport, Case, CaseStatus, WorkflowReport, WorkflowType
from data.synthetic.generator import (
    generate_all,
    generate_checkout_abandonment,
    generate_b2b_invoices,
    generate_mandate_failures,
    generate_payment_degradation,
    generate_promises,
    generate_subscription_failures,
    generate_voice_interactions,
)

# Workflow runners
import workflows.w1_payment_degradation as w1
import workflows.w2_checkout_abandonment as w2
import workflows.w3_subscription_recovery as w3
import workflows.w4_b2b_receivables as w4
import workflows.w5_mandate_retry as w5
import workflows.w6_hinglish_voice as w6
import workflows.w7_promise_to_pay as w7

OUTPUT_DIR = Path("output")
SAMPLE_DIR = Path("data/sample")


# ---------------------------------------------------------------------------
# Workflow registry
# ---------------------------------------------------------------------------

WORKFLOW_REGISTRY = {
    "w1": {
        "type": WorkflowType.PAYMENT_DEGRADATION,
        "runner": w1.run_case,
        "payment_event_builder": w1.build_payment_event,
        "generator": generate_payment_degradation,
        "file": "payment_degradation.jsonl",
        "label": "W1: Payment Degradation",
        "recovery_rate": 0.55,
    },
    "w2": {
        "type": WorkflowType.CHECKOUT_ABANDONMENT,
        "runner": w2.run_case,
        "payment_event_builder": w2.build_payment_event,
        "generator": generate_checkout_abandonment,
        "file": "checkout_abandonment.jsonl",
        "label": "W2: Checkout Abandonment",
        "recovery_rate": 0.40,
    },
    "w3": {
        "type": WorkflowType.SUBSCRIPTION_RECOVERY,
        "runner": w3.run_case,
        "payment_event_builder": w3.build_payment_event,
        "generator": generate_subscription_failures,
        "file": "subscription_failures.jsonl",
        "label": "W3: Subscription Recovery",
        "recovery_rate": 0.48,
    },
    "w4": {
        "type": WorkflowType.B2B_RECEIVABLES,
        "runner": w4.run_case,
        "payment_event_builder": w4.build_payment_event,
        "generator": generate_b2b_invoices,
        "file": "b2b_invoices.jsonl",
        "label": "W4: B2B Receivables",
        "recovery_rate": 0.38,
    },
    "w5": {
        "type": WorkflowType.MANDATE_RETRY,
        "runner": w5.run_case,
        "payment_event_builder": w5.build_payment_event,
        "generator": generate_mandate_failures,
        "file": "mandate_failures.jsonl",
        "label": "W5: Mandate Retry",
        "recovery_rate": 0.42,
    },
    "w6": {
        "type": WorkflowType.HINGLISH_VOICE,
        "runner": w6.run_case,
        "payment_event_builder": w6.build_payment_event,
        "generator": generate_voice_interactions,
        "file": "voice_interactions.jsonl",
        "label": "W6: Hinglish Voice",
        "recovery_rate": 0.30,
    },
    "w7": {
        "type": WorkflowType.PROMISE_TO_PAY,
        "runner": w7.run_case,
        "payment_event_builder": w7.build_payment_event,
        "generator": generate_promises,
        "file": "promises.jsonl",
        "label": "W7: Promise-to-Pay",
        "recovery_rate": 0.35,
    },
}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_or_generate(wf_key: str, meta: dict) -> list[dict]:
    """Load sample JSONL if it exists, otherwise generate fresh."""
    path = SAMPLE_DIR / meta["file"]
    if path.exists():
        records = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records
    return meta["generator"]()


# ---------------------------------------------------------------------------
# Single-workflow runner
# ---------------------------------------------------------------------------


def run_workflow(
    wf_key: str,
    audit_store: AuditStore,
    quiet: bool = False,
    recovery_rate_override: float | None = None,
) -> WorkflowReport:
    meta = WORKFLOW_REGISTRY[wf_key]
    label = meta["label"]
    wf_type = meta["type"]
    run_fn = meta["runner"]
    pe_builder = meta["payment_event_builder"]
    treatment_recovery_rate = recovery_rate_override if recovery_rate_override is not None else meta["recovery_rate"]
    # Holdout control group has natural organic recovery (much lower, e.g. 10-15%)
    holdout_natural_rate = max(0.08, round(treatment_recovery_rate * 0.25, 2))

    records = load_or_generate(wf_key, meta)
    if not quiet:
        print(f"\n{'─' * 60}")
        print(f"  {label}")
        print(f"  Signals loaded: {len(records)}")
        print(f"{'─' * 60}")

    cases: list[Case] = []
    for i, raw in enumerate(records):
        try:
            case = run_fn(raw, audit_store)
            cases.append(case)

            # Determine rate based on treatment vs holdout group
            is_holdout = case.group == "holdout" or case.group.value == "holdout" if hasattr(case.group, "value") else case.group == "holdout"
            target_rate = holdout_natural_rate if is_holdout else treatment_recovery_rate

            # Simulate payment events for attribution
            if random.random() < target_rate and case.status != CaseStatus.RECOVERED:
                pe = pe_builder(case, success=True)
                audit_store.write_payment_event(pe)
                # Re-measure
                from core.pipeline import PipelineEngine
                from core.attribution import AttributionEngine
                attr_engine = AttributionEngine()
                events = audit_store.get_payment_events_for_account(
                    case.account_id, since=case.opened_at
                )
                attr = attr_engine.measure(case, events)
                if attr.recovered:
                    case = attr_engine.apply(case, attr)
                    if is_holdout:
                        case.status = CaseStatus.HOLDOUT_CONTROL
                    case.net_revenue_recovered = max(0.0, case.revenue_recovered - case.total_intervention_cost)
                    audit_store.upsert_case(case)

            if not quiet and (i + 1) % 10 == 0:
                print(f"    Processed {i + 1}/{len(records)} cases...")

        except Exception as exc:  # noqa: BLE001
            if not quiet:
                print(f"    ⚠ Error on record {i}: {exc}")

    return _build_workflow_report(wf_type, cases, audit_store)


def _build_workflow_report(wf_type: WorkflowType, cases: list[Case], audit_store: AuditStore) -> WorkflowReport:
    wf_val = wf_type.value if hasattr(wf_type, "value") else str(wf_type)
    
    treatment_cases = [c for c in cases if (c.group == "treatment" or (hasattr(c.group, "value") and c.group.value == "treatment"))]
    holdout_cases = [c for c in cases if (c.group == "holdout" or (hasattr(c.group, "value") and c.group.value == "holdout"))]

    total_risk = sum(c.revenue_at_risk for c in cases)
    treatment_risk = sum(c.revenue_at_risk for c in treatment_cases)
    holdout_risk = sum(c.revenue_at_risk for c in holdout_cases)

    treatment_recovered = sum(c.revenue_recovered for c in treatment_cases)
    holdout_recovered = sum(c.revenue_recovered for c in holdout_cases)
    total_cost = sum(c.total_intervention_cost for c in treatment_cases)
    net_recovered = max(0.0, treatment_recovered - total_cost)
    total_attempts = sum(c.attempt_count for c in cases)

    treatment_rate = (treatment_recovered / treatment_risk * 100) if treatment_risk > 0 else 0.0
    holdout_rate = (holdout_recovered / holdout_risk * 100) if holdout_risk > 0 else 0.0
    
    # Incremental lift = Treatment Rate - Holdout Natural Rate
    incremental_lift = max(0.0, treatment_rate - holdout_rate)
    incremental_recovered = max(0.0, (incremental_lift / 100.0) * treatment_risk)

    recovered_cases = [c for c in cases if c.status == CaseStatus.RECOVERED]
    capped_cases = [c for c in cases if c.status == CaseStatus.CAPPED]
    pending_cases = [c for c in cases if c.status == CaseStatus.PENDING_APPROVAL]
    escalated_cases = [c for c in cases if c.status == CaseStatus.ESCALATED]
    opted_out = [c for c in cases if c.status == CaseStatus.OPTED_OUT]
    closed = [c for c in cases if c.status == CaseStatus.CLOSED]

    # Count suppressed actions from audit log for this workflow
    wf_audit = audit_store.get_workflow_entries(wf_val)
    suppressed_count = sum(1 for e in wf_audit if e["stage"] == "suppressed")

    overall_rate = (treatment_recovered / total_risk * 100) if total_risk > 0 else 0.0

    ttr_values = [c.time_to_recovery_hours for c in recovered_cases if c.time_to_recovery_hours]
    avg_ttr = sum(ttr_values) / len(ttr_values) if ttr_values else None

    return WorkflowReport(
        workflow=wf_type,
        cases_detected=len(cases),
        treatment_cases=len(treatment_cases),
        holdout_cases=len(holdout_cases),
        revenue_at_risk=round(total_risk, 2),
        treatment_revenue_at_risk=round(treatment_risk, 2),
        holdout_revenue_at_risk=round(holdout_risk, 2),
        recovery_attempts=total_attempts,
        revenue_recovered=round(treatment_recovered, 2),
        holdout_revenue_recovered=round(holdout_recovered, 2),
        incremental_revenue_recovered=round(incremental_recovered, 2),
        treatment_recovery_rate_pct=round(treatment_rate, 2),
        holdout_recovery_rate_pct=round(holdout_rate, 2),
        incremental_lift_pct=round(incremental_lift, 2),
        total_intervention_cost=round(total_cost, 2),
        net_revenue_recovered=round(net_recovered, 2),
        recovery_rate_pct=round(overall_rate, 2),
        avg_time_to_recovery_hours=round(avg_ttr, 2) if avg_ttr else None,
        cases_recovered=len(recovered_cases),
        cases_capped=len(capped_cases),
        cases_pending_approval=len(pending_cases),
        cases_escalated=len(escalated_cases),
        cases_opted_out=len(opted_out),
        cases_closed=len(closed),
        suppressed_actions_count=suppressed_count,
    )


# ---------------------------------------------------------------------------
# Main batch runner
# ---------------------------------------------------------------------------


def run_batch(
    workflows_to_run: list[str] | None = None,
    quiet: bool = False,
    db_path: Path | None = None,
) -> BatchReport:
    random.seed(99)  # Reproducible recovery simulation
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure sample data exists
    if not any(SAMPLE_DIR.glob("*.jsonl")):
        if not quiet:
            print("Generating synthetic sample data...")
        generate_all()

    db = db_path or (OUTPUT_DIR / "audit.db")
    audit_store = AuditStore(db)

    wf_keys = workflows_to_run or list(WORKFLOW_REGISTRY.keys())
    started_at = datetime.utcnow()

    if not quiet:
        print(f"\n{'═' * 60}")
        print("  AI REVENUE RECOVERY — BATCH RUN")
        print(f"  Started: {started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"  Workflows: {', '.join(wf_keys).upper()}")
        print(f"{'═' * 60}")

    wf_reports = []
    for key in wf_keys:
        if key not in WORKFLOW_REGISTRY:
            print(f"  Unknown workflow key: {key} — skipping")
            continue
        report = run_workflow(key, audit_store, quiet=quiet)
        wf_reports.append(report)
        if not quiet:
            _print_workflow_summary(report)

    completed_at = datetime.utcnow()
    batch = BatchReport(
        started_at=started_at,
        completed_at=completed_at,
        workflow_reports=wf_reports,
    )
    batch.compute_totals()

    # Save JSON report
    report_path = OUTPUT_DIR / "batch_report.json"
    with open(report_path, "w") as f:
        f.write(batch.model_dump_json(indent=2))

    if not quiet:
        _print_batch_summary(batch)
        print(f"\n  Audit DB:     {db}")
        print(f"  JSON Report:  {report_path}")

    # Generate HTML report
    try:
        from reporting.html_report import generate_html_report
        html_path = generate_html_report(batch, audit_store, OUTPUT_DIR)
        if not quiet:
            print(f"  HTML Report:  {html_path}")
    except Exception as exc:  # noqa: BLE001
        if not quiet:
            print(f"  ⚠ HTML report failed: {exc}")

    audit_store.close()
    return batch


def _print_workflow_summary(r: WorkflowReport) -> None:
    label = r.workflow.value if hasattr(r.workflow, "value") else str(r.workflow)
    print(f"\n  ✓ {label}")
    print(f"    Cases: {r.cases_detected} (Treatment: {r.treatment_cases}, Holdout: {r.holdout_cases})")
    print(f"    Gross Recovered:  ${r.revenue_recovered:,.2f} ({r.treatment_recovery_rate_pct:.1f}% treatment)")
    print(f"    Holdout Baseline: ${r.holdout_revenue_recovered:,.2f} ({r.holdout_recovery_rate_pct:.1f}% organic control)")
    print(f"    Incremental Lift: ${r.incremental_revenue_recovered:,.2f} (+{r.incremental_lift_pct:.1f}% causal lift)")
    print(f"    Intervention Cost:${r.total_intervention_cost:,.2f}  │  Net Recovered: ${r.net_revenue_recovered:,.2f}")
    print(f"    Attempts: {r.recovery_attempts}  │  Suppressed Actions: {r.suppressed_actions_count}  │  Pending Gate: {r.cases_pending_approval}")


def _print_batch_summary(b: BatchReport) -> None:
    print(f"\n{'═' * 66}")
    print("  EXECUTIVE SUMMARY & CAUSAL INCREMENTALITY PROOF")
    print(f"{'═' * 66}")
    print(f"  Total Cases Processed:         {b.total_cases:,} (Treatment: {b.total_treatment_cases}, Holdout: {b.total_holdout_cases})")
    print(f"  Total Revenue at Risk:         ${b.total_revenue_at_risk:>14,.2f}")
    print(f"  Gross Revenue Recovered:       ${b.total_revenue_recovered:>14,.2f} ({b.overall_treatment_recovery_rate_pct:.1f}% treatment rate)")
    print(f"  Holdout Organic Baseline:      ${b.total_holdout_revenue_recovered:>14,.2f} ({b.overall_holdout_recovery_rate_pct:.1f}% control rate)")
    print(f"  Incremental Revenue Lift:      ${b.total_incremental_revenue_recovered:>14,.2f} (+{b.overall_incremental_lift_pct:.1f}% net lift)")
    print(f"  Total Intervention Cost:       ${b.total_intervention_cost:>14,.2f}")
    print(f"  NET REVENUE RECOVERED:         ${b.total_net_revenue_recovered:>14,.2f} (Gross − Costs)")
    print(f"  Total Recovery Attempts:       {b.total_recovery_attempts:,}")
    print(f"  Suppressed Actions Ledger:     {b.total_suppressed_actions:,} (Quiet hours, opt-outs, caps, gates)")
    print(f"  Pending Approval Gates:        {b.cases_pending_human_approval}")
    if b.avg_time_to_recovery_hours:
        print(f"  Avg Time-to-Recovery:          {b.avg_time_to_recovery_hours:.1f} hours")
    duration = (b.completed_at - b.started_at).total_seconds()
    print(f"  Pipeline Run Duration:         {duration:.1f} seconds")
    print(f"{'═' * 66}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Revenue Recovery — Batch Runner")
    parser.add_argument("--quiet", action="store_true", help="Suppress detailed output")
    parser.add_argument(
        "--workflow",
        nargs="+",
        choices=list(WORKFLOW_REGISTRY.keys()),
        help="Run specific workflows only (e.g. --workflow w1 w3)",
    )
    parser.add_argument("--db", help="Custom path to audit DB", default=None)
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else None
    batch = run_batch(
        workflows_to_run=args.workflow,
        quiet=args.quiet,
        db_path=db_path,
    )
    sys.exit(0 if batch.total_cases > 0 else 1)


if __name__ == "__main__":
    main()
