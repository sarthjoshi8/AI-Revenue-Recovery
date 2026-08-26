"""
tests/test_stopping_rules.py — Unit tests for stopping rule evaluator.

Verifies:
  - Max-attempts cap fires correctly
  - Hard-stop flags close cases immediately
  - Cooldown prevents premature re-run
  - Age limit closes stale cases
  - Terminal state cases are never re-evaluated
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.models import Case, CaseStatus, Signal, SignalSource, WorkflowType
from core.stopping_rules import StoppingRuleEvaluator


def _make_case(
    workflow: WorkflowType = WorkflowType.CHECKOUT_ABANDONMENT,
    status: CaseStatus = CaseStatus.OPEN,
    attempt_count: int = 0,
    age_days: int = 0,
    metadata: dict | None = None,
) -> Case:
    now = datetime.utcnow()
    opened = now - timedelta(days=age_days)
    signal = Signal(
        workflow=workflow,
        occurred_at=opened,
        source=SignalSource.SIMULATION,
        account_id="acc_test_001",
        revenue_at_risk=100.0,
    )
    return Case(
        workflow=workflow,
        signal=signal,
        status=status,
        attempt_count=attempt_count,
        revenue_at_risk=100.0,
        account_id="acc_test_001",
        opened_at=opened,
        last_updated_at=now - timedelta(hours=1),
        metadata=metadata or {},
    )


class TestMaxAttemptsCap:

    def test_cap_fires_at_max(self):
        case = _make_case(
            workflow=WorkflowType.CHECKOUT_ABANDONMENT,
            attempt_count=3,  # max is 3 for W2
        )
        ev = StoppingRuleEvaluator()
        result = ev.evaluate(case)
        assert result.should_stop
        assert result.new_status == CaseStatus.CAPPED
        assert result.stop_type == "cap_reached"

    def test_cap_does_not_fire_below_max(self):
        case = _make_case(
            workflow=WorkflowType.CHECKOUT_ABANDONMENT,
            attempt_count=2,
        )
        ev = StoppingRuleEvaluator()
        result = ev.evaluate(case)
        # Should not stop (no hard-stop flags, not capped, not too old)
        # Note: may still stop on cooldown — that's expected behavior
        assert result.stop_type != "cap_reached"

    def test_subscription_cap_at_5(self):
        case = _make_case(
            workflow=WorkflowType.SUBSCRIPTION_RECOVERY,
            attempt_count=5,
        )
        ev = StoppingRuleEvaluator()
        result = ev.evaluate(case)
        assert result.should_stop
        assert result.new_status == CaseStatus.CAPPED

    def test_zero_attempts_never_capped(self):
        case = _make_case(attempt_count=0)
        ev = StoppingRuleEvaluator()
        result = ev.evaluate(case)
        assert result.stop_type != "cap_reached"


class TestHardStopFlags:

    def test_payment_success_flag_stops(self):
        case = _make_case(metadata={"payment_success": True})
        ev = StoppingRuleEvaluator()
        result = ev.evaluate(case)
        assert result.should_stop
        assert result.stop_type == "hard_stop"
        assert result.new_status == CaseStatus.RECOVERED

    def test_opt_out_flag_stops(self):
        case = _make_case(metadata={"opt_out": True})
        ev = StoppingRuleEvaluator()
        result = ev.evaluate(case)
        assert result.should_stop
        assert result.stop_type == "hard_stop"
        assert result.new_status == CaseStatus.OPTED_OUT

    def test_dispute_flag_stops(self):
        case = _make_case(
            workflow=WorkflowType.SUBSCRIPTION_RECOVERY,
            metadata={"dispute": True},
        )
        ev = StoppingRuleEvaluator()
        result = ev.evaluate(case)
        assert result.should_stop
        assert result.stop_type == "hard_stop"
        assert result.new_status == CaseStatus.DISPUTED

    def test_no_flag_does_not_stop(self):
        case = _make_case(metadata={"some_other_flag": True})
        ev = StoppingRuleEvaluator()
        result = ev.evaluate(case)
        assert result.stop_type != "hard_stop"

    def test_b2b_payment_received_stops(self):
        case = _make_case(
            workflow=WorkflowType.B2B_RECEIVABLES,
            metadata={"payment_received": True},
        )
        ev = StoppingRuleEvaluator()
        result = ev.evaluate(case)
        assert result.should_stop
        assert result.stop_type == "hard_stop"


class TestTerminalState:

    @pytest.mark.parametrize("status", [
        CaseStatus.RECOVERED,
        CaseStatus.CAPPED,
        CaseStatus.OPTED_OUT,
        CaseStatus.DISPUTED,
        CaseStatus.ESCALATED,
        CaseStatus.CLOSED,
    ])
    def test_terminal_cases_always_stop(self, status: CaseStatus):
        case = _make_case(status=status)
        ev = StoppingRuleEvaluator()
        result = ev.evaluate(case)
        assert result.should_stop
        assert result.stop_type == "terminal"


class TestCooldown:

    def test_cooldown_fires_when_too_soon(self):
        case = _make_case(
            workflow=WorkflowType.CHECKOUT_ABANDONMENT,
            status=CaseStatus.ACTIVE,
            attempt_count=1,
        )
        # last_updated just 1 minute ago — cooldown is 6h
        case.last_updated_at = datetime.utcnow() - timedelta(minutes=1)
        ev = StoppingRuleEvaluator()
        result = ev.evaluate(case)
        assert result.should_stop
        assert result.stop_type == "cooldown"
        assert result.new_status is None  # cooldown doesn't change status

    def test_cooldown_clears_after_window(self):
        case = _make_case(
            workflow=WorkflowType.CHECKOUT_ABANDONMENT,
            status=CaseStatus.ACTIVE,
            attempt_count=1,
        )
        case.last_updated_at = datetime.utcnow() - timedelta(hours=8)
        ev = StoppingRuleEvaluator()
        result = ev.evaluate(case)
        assert result.stop_type != "cooldown"


class TestAgeLimit:

    def test_age_limit_closes_stale_case(self):
        case = _make_case(
            workflow=WorkflowType.CHECKOUT_ABANDONMENT,
            age_days=5,  # max age for checkout is 3 days
        )
        ev = StoppingRuleEvaluator()
        result = ev.evaluate(case)
        assert result.should_stop
        assert result.stop_type == "age_limit"
        assert result.new_status == CaseStatus.CLOSED

    def test_b2b_90_day_case_closes(self):
        case = _make_case(
            workflow=WorkflowType.B2B_RECEIVABLES,
            age_days=91,
        )
        ev = StoppingRuleEvaluator()
        result = ev.evaluate(case)
        assert result.should_stop
        assert result.stop_type in ("age_limit", "cap_reached")
