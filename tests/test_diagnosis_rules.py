"""
tests/test_diagnosis_rules.py — Unit tests for the rules-based diagnosis engine.

Verifies each workflow's diagnosis rules produce the correct cause_code
and recommended actions for known input signals.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from core.diagnosis.rules_engine import RulesEngine
from core.models import (
    ActionType,
    Case,
    CaseStatus,
    Signal,
    SignalSource,
    WorkflowType,
)


def _case(workflow: WorkflowType, payload: dict, revenue: float = 100.0) -> Case:
    signal = Signal(
        workflow=workflow,
        occurred_at=datetime.utcnow(),
        source=SignalSource.SIMULATION,
        account_id="acc_diag_test",
        revenue_at_risk=revenue,
        payload=payload,
    )
    return Case(
        workflow=workflow,
        signal=signal,
        status=CaseStatus.OPEN,
        revenue_at_risk=revenue,
        account_id="acc_diag_test",
        opened_at=datetime.utcnow(),
        last_updated_at=datetime.utcnow(),
    )


engine = RulesEngine()


class TestW1Diagnosis:

    def test_issuer_outage_detected(self):
        case = _case(WorkflowType.PAYMENT_DEGRADATION, {
            "decline_rate_current": 0.30,
            "decline_rate_baseline": 0.04,
            "decline_codes": {"91": 100, "ISSUER_UNAVAILABLE": 80, "96": 40},
            "processor": "stripe",
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "ISSUER_OUTAGE"
        assert ActionType.RETRY_WITH_BACKOFF in rc.recommended_action_types

    def test_expired_card_bulk_detected(self):
        case = _case(WorkflowType.PAYMENT_DEGRADATION, {
            "decline_rate_current": 0.25,
            "decline_rate_baseline": 0.04,
            "decline_codes": {"54": 120, "EXPIRED_CARD": 80},
            "processor": "adyen",
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "BULK_CARD_EXPIRY"
        assert ActionType.TRIGGER_CARD_UPDATER in rc.recommended_action_types
        assert not rc.is_retriable  # Expired cards don't auto-recover via retry

    def test_3ds_friction_detected(self):
        case = _case(WorkflowType.PAYMENT_DEGRADATION, {
            "decline_rate_current": 0.20,
            "decline_rate_baseline": 0.04,
            "decline_codes": {"3DS_FAIL": 80, "N3": 40, "3DS_TIMEOUT": 20},
            "processor": "braintree",
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "3DS_FRICTION"

    def test_processor_routing_fault(self):
        case = _case(WorkflowType.PAYMENT_DEGRADATION, {
            "decline_rate_current": 0.30,
            "decline_rate_baseline": 0.04,
            "decline_codes": {"05": 10, "51": 8, "91": 5, "14": 6},
            "processor": "checkout_com",
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "PROCESSOR_ROUTING_FAULT"
        assert ActionType.ROUTE_ALTERNATE_PROCESSOR in rc.recommended_action_types


class TestW2Diagnosis:

    def test_items_unavailable_detected(self):
        case = _case(WorkflowType.CHECKOUT_ABANDONMENT, {
            "cart_value": 150.0,
            "all_items_available": False,
            "time_since_abandonment_minutes": 30,
            "prior_purchase_count": 5,
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "ITEMS_UNAVAILABLE"

    def test_high_value_abandonment_detected(self):
        case = _case(WorkflowType.CHECKOUT_ABANDONMENT, {
            "cart_value": 350.0,
            "all_items_available": True,
            "time_since_abandonment_minutes": 20,
            "prior_purchase_count": 5,
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "HIGH_VALUE_ABANDONMENT"
        assert rc.confidence >= 0.8

    def test_cold_cart_detected(self):
        case = _case(WorkflowType.CHECKOUT_ABANDONMENT, {
            "cart_value": 50.0,
            "all_items_available": True,
            "time_since_abandonment_minutes": 2000,  # >24h
            "prior_purchase_count": 0,
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "COLD_CART"


class TestW3Diagnosis:

    def test_expired_card_detected(self):
        case = _case(WorkflowType.SUBSCRIPTION_RECOVERY, {
            "decline_code": "54",
            "card_expiry": "01/22",
            "is_first_failure": True,
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "EXPIRED_CARD"
        assert ActionType.TRIGGER_CARD_UPDATER in rc.recommended_action_types

    def test_insufficient_funds_detected(self):
        case = _case(WorkflowType.SUBSCRIPTION_RECOVERY, {
            "decline_code": "51",
            "card_expiry": "12/28",
            "is_first_failure": True,
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "INSUFFICIENT_FUNDS"
        assert rc.is_retriable

    def test_hard_decline_not_retriable(self):
        case = _case(WorkflowType.SUBSCRIPTION_RECOVERY, {
            "decline_code": "41",  # Lost card
            "card_expiry": "12/28",
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "HARD_DECLINE"
        assert not rc.is_retriable


class TestW4Diagnosis:

    def test_active_dispute_blocks(self):
        case = _case(WorkflowType.B2B_RECEIVABLES, {
            "days_overdue": 45,
            "has_active_dispute": True,
            "payment_history_score": 0.5,
            "account_tier": "smb",
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "ACTIVE_DISPUTE"
        assert not rc.is_retriable

    def test_early_overdue_gets_friendly_reminder(self):
        case = _case(WorkflowType.B2B_RECEIVABLES, {
            "days_overdue": 5,
            "has_active_dispute": False,
            "payment_history_score": 0.8,
            "account_tier": "enterprise",
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "EARLY_OVERDUE"
        assert ActionType.SEND_FRIENDLY_REMINDER in rc.recommended_action_types

    def test_critically_overdue_escalates(self):
        case = _case(WorkflowType.B2B_RECEIVABLES, {
            "days_overdue": 90,
            "has_active_dispute": False,
            "payment_history_score": 0.3,
            "account_tier": "smb",
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "CRITICALLY_OVERDUE"
        assert ActionType.ESCALATE_TO_COLLECTIONS in rc.recommended_action_types


class TestW5Diagnosis:

    def test_expired_mandate_requires_reauth(self):
        case = _case(WorkflowType.MANDATE_RETRY, {
            "failure_code": "MANDATE_EXPIRED",
            "is_mandate_active": False,
            "prior_retry_count": 0,
            "max_retries_allowed": 3,
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "MANDATE_EXPIRED"
        assert ActionType.REQUEST_MANDATE_REAUTHORIZATION in rc.recommended_action_types

    def test_network_retry_limit_blocks(self):
        case = _case(WorkflowType.MANDATE_RETRY, {
            "failure_code": "INSUFFICIENT_FUNDS",
            "is_mandate_active": True,
            "prior_retry_count": 3,
            "max_retries_allowed": 3,
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "MAX_RETRIES_NETWORK"
        assert not rc.is_retriable


class TestW7Diagnosis:

    def test_broken_promise_escalates(self):
        case = _case(WorkflowType.PROMISE_TO_PAY, {
            "is_broken": True,
            "days_until_due": -5,
            "captured_from": "agent",
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "BROKEN_PROMISE"
        assert ActionType.ESCALATE_BROKEN_PTP in rc.recommended_action_types

    def test_ptp_due_soon_sends_reminder(self):
        case = _case(WorkflowType.PROMISE_TO_PAY, {
            "is_broken": False,
            "days_until_due": 1,
            "captured_from": "chatbot",
        })
        rc = engine.diagnose(case)
        assert rc.cause_code == "PTP_DUE_SOON"
        assert ActionType.SEND_PTP_REMINDER in rc.recommended_action_types


class TestEngineMetadata:

    def test_model_version_is_tagged(self):
        case = _case(WorkflowType.CHECKOUT_ABANDONMENT, {
            "cart_value": 50.0, "all_items_available": True,
            "time_since_abandonment_minutes": 60, "prior_purchase_count": 2,
        })
        rc = engine.diagnose(case)
        assert rc.model_version.startswith("rules-")

    def test_confidence_within_range(self):
        case = _case(WorkflowType.SUBSCRIPTION_RECOVERY, {
            "decline_code": "51", "card_expiry": "12/28",
        })
        rc = engine.diagnose(case)
        assert 0.0 <= rc.confidence <= 1.0
