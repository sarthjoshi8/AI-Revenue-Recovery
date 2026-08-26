"""
tests/test_differentiation_features.py — Unit tests for:
1. Holdout control group incrementality & causal lift proof
2. Net revenue calculation (Gross Recovery - Intervention Costs)
3. Suppressed actions ledger (deliberately blocked actions)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import pytest

from core.audit import AuditStore
from core.diagnosis.rules_engine import RulesEngine
from core.models import (
    ActionResult,
    ActionType,
    Case,
    CaseGroup,
    CaseStatus,
    Intervention,
    PipelineStage,
    Signal,
    SignalSource,
    WorkflowType,
)
from core.pipeline import PipelineEngine


def dummy_connector_with_cost(case: Case, intervention: Intervention) -> ActionResult:
    cost_map = {
        ActionType.SEND_EMAIL: 0.005,
        ActionType.SEND_SMS: 0.035,
        ActionType.INITIATE_IVR_SCRIPT: 0.15,
        ActionType.TRIGGER_CARD_UPDATER: 0.45,
    }
    return ActionResult(
        success=True,
        connector="test_connector",
        cost=cost_map.get(intervention.action_type, 0.01),
        response_payload={"action_taken": intervention.action_type},
    )


class TestHoldoutAndNetRecovery:

    def test_holdout_group_suppresses_action(self, tmp_path: Path):
        db_path = tmp_path / "test_holdout.db"
        store = AuditStore(db_path)

        engine = PipelineEngine(
            workflow=WorkflowType.CHECKOUT_ABANDONMENT,
            diagnosis_engine=RulesEngine(),
            audit_store=store,
            connector=dummy_connector_with_cost,
            holdout_rate=1.0,  # 100% holdout for testing
        )

        signal = Signal(
            workflow=WorkflowType.CHECKOUT_ABANDONMENT,
            occurred_at=datetime.utcnow(),
            source=SignalSource.SIMULATION,
            account_id="acc_holdout_01",
            revenue_at_risk=200.0,
            payload={"cart_value": 200.0, "time_since_abandonment_minutes": 15},
        )

        case = engine.run(signal)

        assert case.group == CaseGroup.HOLDOUT
        assert case.status == CaseStatus.HOLDOUT_CONTROL
        assert case.attempt_count == 0  # No intervention executed

        # Check suppressed audit log
        entries = store.get_case_audit(case.case_id)
        stages = [e["stage"] for e in entries]
        assert "suppressed" in stages

        store.close()

    def test_net_revenue_calculation(self, tmp_path: Path):
        db_path = tmp_path / "test_net_revenue.db"
        store = AuditStore(db_path)

        engine = PipelineEngine(
            workflow=WorkflowType.SUBSCRIPTION_RECOVERY,
            diagnosis_engine=RulesEngine(),
            audit_store=store,
            connector=dummy_connector_with_cost,
            holdout_rate=0.0,  # 0% holdout for treatment testing
        )

        signal = Signal(
            workflow=WorkflowType.SUBSCRIPTION_RECOVERY,
            occurred_at=datetime.utcnow(),
            source=SignalSource.SIMULATION,
            account_id="acc_net_01",
            revenue_at_risk=100.0,
            payload={"decline_code": "51", "card_expiry": "12/28"},
        )

        case = engine.run(signal)

        assert case.attempt_count == 1
        assert case.total_intervention_cost > 0.0

        # Simulate payment recovery
        from core.models import PaymentEvent
        pe = PaymentEvent(
            case_id=case.case_id,
            account_id=case.account_id,
            amount=100.0,
            status="success",
            occurred_at=datetime.utcnow(),
        )
        store.write_payment_event(pe)
        case_updated = engine.process_payment_event(case, {})

        assert case_updated.status == CaseStatus.RECOVERED
        assert case_updated.revenue_recovered == 100.0
        assert case_updated.net_revenue_recovered == 100.0 - case_updated.total_intervention_cost
        assert case_updated.net_revenue_recovered < 100.0

        store.close()

    def test_suppressed_actions_ledger(self, tmp_path: Path):
        db_path = tmp_path / "test_suppressed_ledger.db"
        store = AuditStore(db_path)

        engine = PipelineEngine(
            workflow=WorkflowType.CHECKOUT_ABANDONMENT,
            diagnosis_engine=RulesEngine(),
            audit_store=store,
            connector=dummy_connector_with_cost,
            holdout_rate=0.0,
        )

        signal = Signal(
            workflow=WorkflowType.CHECKOUT_ABANDONMENT,
            occurred_at=datetime.utcnow(),
            source=SignalSource.SIMULATION,
            account_id="acc_supp_01",
            revenue_at_risk=150.0,
            payload={"cart_value": 150.0, "time_since_abandonment_minutes": 10},
        )

        case = engine.run(signal)
        # Force age limit stopping rule
        case.opened_at = datetime.utcnow() - timedelta(days=10)
        from core.stopping_rules import StoppingRuleEvaluator
        evaluator = StoppingRuleEvaluator()
        stop = evaluator.evaluate(case)
        engine._apply_stop(case, stop, datetime.utcnow())

        entries = store.get_case_audit(case.case_id)
        suppressed_entries = [e for e in entries if e["stage"] == "suppressed"]
        assert len(suppressed_entries) > 0

        store.close()
