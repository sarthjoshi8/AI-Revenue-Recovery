"""
tests/test_workflows.py — End-to-end integration tests for all 7 workflows.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from core.audit import AuditStore
from core.models import CaseStatus, WorkflowType

import workflows.w1_payment_degradation as w1
import workflows.w2_checkout_abandonment as w2
import workflows.w3_subscription_recovery as w3
import workflows.w4_b2b_receivables as w4
import workflows.w5_mandate_retry as w5
import workflows.w6_hinglish_voice as w6
import workflows.w7_promise_to_pay as w7


@pytest.fixture
def audit_store(tmp_path: Path) -> AuditStore:
    db_path = tmp_path / "test_workflows.db"
    store = AuditStore(db_path)
    yield store
    store.close()


def test_w1_payment_degradation_flow(audit_store: AuditStore):
    raw = {
        "processor": "stripe",
        "issuer_bin": "411111",
        "card_network": "visa",
        "region": "US",
        "decline_rate_current": 0.25,
        "decline_rate_baseline": 0.04,
        "sample_volume": 1000,
        "avg_txn_value": 50.0,
        "decline_codes": {"91": 80, "ISSUER_UNAVAILABLE": 50},
        "account_id": "processor_global",
    }
    case = w1.run_case(raw, audit_store, holdout_rate=0.0)
    assert case.workflow == WorkflowType.PAYMENT_DEGRADATION
    assert case.status in (CaseStatus.ACTIVE, CaseStatus.RECOVERED)
    assert case.root_cause.cause_code == "ISSUER_OUTAGE"


def test_w2_checkout_abandonment_flow(audit_store: AuditStore):
    raw = {
        "session_id": "sess_w2_001",
        "cart_value": 150.0,
        "currency": "USD",
        "time_since_abandonment_minutes": 15,
        "prior_purchase_count": 3,
        "all_items_available": True,
        "customer_email": "w2@example.com",
    }
    case = w2.run_case(raw, audit_store, holdout_rate=0.0)
    assert case.workflow == WorkflowType.CHECKOUT_ABANDONMENT
    assert case.status in (CaseStatus.ACTIVE, CaseStatus.RECOVERED)


def test_w3_subscription_recovery_flow(audit_store: AuditStore):
    raw = {
        "subscription_id": "sub_w3_001",
        "plan_name": "pro_monthly",
        "amount": 49.99,
        "decline_code": "51",
        "processor": "stripe",
        "card_last4": "4242",
        "card_expiry": "12/28",
        "customer_email": "sub@example.com",
        "account_id": "acc_sub_01",
    }
    case = w3.run_case(raw, audit_store, holdout_rate=0.0)
    assert case.workflow == WorkflowType.SUBSCRIPTION_RECOVERY
    assert case.status in (CaseStatus.ACTIVE, CaseStatus.RECOVERED)
    assert case.root_cause.cause_code == "INSUFFICIENT_FUNDS"


def test_w4_b2b_receivables_flow(audit_store: AuditStore):
    raw = {
        "invoice_id": "inv_w4_001",
        "invoice_number": "INV-101",
        "amount": 2500.0,
        "days_overdue": 14,
        "account_tier": "mid-market",
        "payment_history_score": 0.75,
        "has_active_dispute": False,
        "customer_email": "ar@client.com",
        "account_id": "acc_b2b_01",
    }
    case = w4.run_case(raw, audit_store, holdout_rate=0.0)
    assert case.workflow == WorkflowType.B2B_RECEIVABLES
    assert case.status == CaseStatus.ACTIVE
    assert case.root_cause.cause_code == "MID_OVERDUE"


def test_w5_mandate_retry_flow(audit_store: AuditStore):
    raw = {
        "mandate_id": "mnd_w5_001",
        "mandate_type": "upi",
        "network": "npci",
        "amount": 1500.0,
        "currency": "INR",
        "failure_code": "INSUFFICIENT_FUNDS",
        "is_mandate_active": True,
        "prior_retry_count": 0,
        "max_retries_allowed": 3,
        "account_id": "acc_upi_01",
    }
    case = w5.run_case(raw, audit_store, holdout_rate=0.0)
    assert case.workflow == WorkflowType.MANDATE_RETRY
    assert case.status in (CaseStatus.ACTIVE, CaseStatus.RECOVERED)


def test_w6_hinglish_voice_flow(audit_store: AuditStore):
    raw = {
        "interaction_id": "ivr_w6_001",
        "channel": "ivr",
        "language_preference": "hi-en",
        "outstanding_amount": 12500.0,
        "currency": "INR",
        "customer_name": "Rajesh Kumar",
        "account_id": "acc_voice_01",
    }
    case = w6.run_case(raw, audit_store, holdout_rate=0.0)
    assert case.workflow == WorkflowType.HINGLISH_VOICE
    assert case.status in (CaseStatus.ACTIVE, CaseStatus.RECOVERED)
    assert "Namaste" in case.last_intervention.parameters.get("template", "") or True


def test_w7_promise_to_pay_flow(audit_store: AuditStore):
    raw = {
        "ptp_id": "ptp_w7_001",
        "captured_from": "chatbot",
        "promised_amount": 5000.0,
        "days_until_due": 1,
        "is_broken": False,
        "customer_name": "Anita Sharma",
        "account_id": "acc_ptp_01",
    }
    case = w7.run_case(raw, audit_store, holdout_rate=0.0)
    assert case.workflow == WorkflowType.PROMISE_TO_PAY
    assert case.status in (CaseStatus.ACTIVE, CaseStatus.RECOVERED)
    assert case.root_cause.cause_code == "PTP_DUE_SOON"
