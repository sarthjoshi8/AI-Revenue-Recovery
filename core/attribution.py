"""
core/attribution.py — Revenue attribution engine.

STRICT RULE:
  A case is marked RECOVERED only when a PaymentEvent with status='success'
  is found for the same account_id (or explicit case_id link) within the
  attribution window AFTER the case was opened.

  "Revenue at risk" and "revenue recovered" are always reported separately.
  An action being taken does NOT count as revenue recovered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from core.models import Case, CaseStatus, PaymentEvent, WorkflowType


# Attribution windows per workflow (hours after case opened)
DEFAULT_ATTRIBUTION_WINDOWS: dict[WorkflowType, float] = {
    WorkflowType.PAYMENT_DEGRADATION: 4,
    WorkflowType.CHECKOUT_ABANDONMENT: 24,
    WorkflowType.SUBSCRIPTION_RECOVERY: 168,    # 7 days
    WorkflowType.B2B_RECEIVABLES: 720,          # 30 days
    WorkflowType.MANDATE_RETRY: 72,
    WorkflowType.HINGLISH_VOICE: 72,
    WorkflowType.PROMISE_TO_PAY: 720,           # 30 days (promise may be weeks out)
}


@dataclass
class AttributionResult:
    recovered: bool
    amount: float = 0.0
    matched_event_id: Optional[str] = None
    matched_at: Optional[datetime] = None
    reason: str = ""


class AttributionEngine:
    """
    Matches payment success events to open cases and computes recovered revenue.
    """

    def __init__(self, windows: Optional[dict[WorkflowType, float]] = None) -> None:
        self._windows = windows or DEFAULT_ATTRIBUTION_WINDOWS

    def get_window_hours(self, workflow: WorkflowType) -> float:
        return self._windows.get(workflow, 72.0)

    def measure(
        self,
        case: Case,
        payment_events: list[dict],
        now: Optional[datetime] = None,
    ) -> AttributionResult:
        """
        Given a list of raw payment event dicts (from AuditStore),
        determine if any constitute a successful recovery for this case.
        """
        now = now or datetime.utcnow()
        workflow = WorkflowType(case.workflow) if isinstance(case.workflow, str) else case.workflow
        window_hours = self.get_window_hours(workflow)
        window_end = case.opened_at + timedelta(hours=window_hours)

        for evt in payment_events:
            if evt.get("status") != "success":
                continue

            # Must be for this account
            if evt.get("account_id") != case.account_id:
                continue

            occurred_at_str = evt.get("occurred_at", "")
            try:
                occurred_at = datetime.fromisoformat(occurred_at_str)
            except (ValueError, TypeError):
                continue

            # Must be AFTER the case opened (causality)
            if occurred_at <= case.opened_at:
                continue

            # Must be WITHIN the attribution window
            if occurred_at > window_end:
                continue

            # Optional: must be linked by case_id if the event has one
            event_case_id = evt.get("case_id")
            if event_case_id and event_case_id != case.case_id:
                continue

            # Match found
            amount = evt.get("amount", case.revenue_at_risk)
            return AttributionResult(
                recovered=True,
                amount=amount,
                matched_event_id=evt.get("event_id"),
                matched_at=occurred_at,
                reason=(
                    f"Matched payment event {evt.get('event_id')} "
                    f"for account {case.account_id} at {occurred_at.isoformat()}"
                ),
            )

        # Check if still within window (case is just pending)
        if now <= window_end:
            return AttributionResult(
                recovered=False,
                reason=f"No matching success event yet; window open until {window_end.isoformat()}",
            )

        return AttributionResult(
            recovered=False,
            reason=f"Attribution window ({window_hours}h) expired without a matching success event",
        )

    def apply(self, case: Case, result: AttributionResult) -> Case:
        """
        Update the case status based on attribution result.
        Returns the mutated case (caller must persist).
        """
        if result.recovered:
            case.status = CaseStatus.RECOVERED
            case.revenue_recovered = result.amount
            case.recovered_at = result.matched_at or datetime.utcnow()
            case.close_reason = result.reason
            case.closed_at = case.recovered_at
        return case
