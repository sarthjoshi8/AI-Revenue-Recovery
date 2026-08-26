"""
tests/test_attribution.py — Unit tests for revenue attribution engine.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.attribution import AttributionEngine
from core.models import Case, CaseStatus, Signal, SignalSource, WorkflowType


def _make_case(
    workflow: WorkflowType = WorkflowType.SUBSCRIPTION_RECOVERY,
    revenue_at_risk: float = 99.99,
    opened_hours_ago: float = 1.0,
) -> Case:
    now = datetime.utcnow()
    opened = now - timedelta(hours=opened_hours_ago)
    signal = Signal(
        workflow=workflow,
        occurred_at=opened,
        source=SignalSource.SIMULATION,
        account_id="acc_attr_test",
        revenue_at_risk=revenue_at_risk,
    )
    return Case(
        workflow=workflow,
        signal=signal,
        status=CaseStatus.ACTIVE,
        revenue_at_risk=revenue_at_risk,
        account_id="acc_attr_test",
        opened_at=opened,
        last_updated_at=now,
    )


def _make_event(
    account_id: str,
    status: str,
    amount: float = 99.99,
    hours_after_open: float = 2.0,
    case_id: str | None = None,
    opened_at: datetime | None = None,
) -> dict:
    base = opened_at or datetime.utcnow()
    occurred = base + timedelta(hours=hours_after_open)
    return {
        "event_id": "evt_test_001",
        "case_id": case_id,
        "account_id": account_id,
        "amount": amount,
        "currency": "USD",
        "status": status,
        "processor": "test",
        "occurred_at": occurred.isoformat(),
    }


class TestAttributionEngine:

    def test_success_event_within_window_recovers(self):
        case = _make_case()
        evt = _make_event(
            account_id=case.account_id,
            status="success",
            amount=99.99,
            hours_after_open=10,
            opened_at=case.opened_at,
        )
        engine = AttributionEngine()
        result = engine.measure(case, [evt])
        assert result.recovered
        assert result.amount == 99.99

    def test_failure_event_does_not_recover(self):
        case = _make_case()
        evt = _make_event(
            account_id=case.account_id,
            status="failure",
            hours_after_open=2,
            opened_at=case.opened_at,
        )
        engine = AttributionEngine()
        result = engine.measure(case, [evt])
        assert not result.recovered

    def test_event_outside_window_does_not_recover(self):
        case = _make_case(workflow=WorkflowType.CHECKOUT_ABANDONMENT)  # 24h window
        evt = _make_event(
            account_id=case.account_id,
            status="success",
            hours_after_open=30,  # Outside 24h window
            opened_at=case.opened_at,
        )
        engine = AttributionEngine()
        result = engine.measure(case, [evt])
        assert not result.recovered

    def test_event_before_case_opened_does_not_recover(self):
        case = _make_case()
        # Event occurred before case opened
        evt = {
            "event_id": "evt_before",
            "case_id": None,
            "account_id": case.account_id,
            "amount": 99.99,
            "currency": "USD",
            "status": "success",
            "processor": "test",
            "occurred_at": (case.opened_at - timedelta(hours=1)).isoformat(),
        }
        engine = AttributionEngine()
        result = engine.measure(case, [evt])
        assert not result.recovered

    def test_wrong_account_does_not_recover(self):
        case = _make_case()
        evt = _make_event(
            account_id="acc_different",  # Different account
            status="success",
            hours_after_open=2,
            opened_at=case.opened_at,
        )
        engine = AttributionEngine()
        result = engine.measure(case, [evt])
        assert not result.recovered

    def test_linked_case_id_mismatch_does_not_recover(self):
        case = _make_case()
        evt = _make_event(
            account_id=case.account_id,
            status="success",
            hours_after_open=2,
            case_id="other_case_id",  # Linked to different case
            opened_at=case.opened_at,
        )
        engine = AttributionEngine()
        result = engine.measure(case, [evt])
        assert not result.recovered

    def test_empty_events_returns_no_recovery(self):
        case = _make_case()
        engine = AttributionEngine()
        result = engine.measure(case, [])
        assert not result.recovered

    def test_apply_sets_case_recovered(self):
        case = _make_case()
        evt = _make_event(
            account_id=case.account_id,
            status="success",
            hours_after_open=2,
            opened_at=case.opened_at,
        )
        engine = AttributionEngine()
        result = engine.measure(case, [evt])
        updated = engine.apply(case, result)
        assert updated.status == CaseStatus.RECOVERED
        assert updated.revenue_recovered == 99.99
        assert updated.recovered_at is not None
