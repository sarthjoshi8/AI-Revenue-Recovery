"""
workflows/w5_mandate_retry.py — Mandate retry sequencer.

For UPI/ACH/direct-debit recurring mandates, detects failed debits.
Diagnoses failure code. Schedules compliant retries respecting network-mandated
retry windows and max-attempt rules.

NPCI e-mandate retry rules (simulated):
  - Max 3 retries per presentation cycle
  - Min 24h between retries
  - Mandate must be active and within authorized amount
"""

from __future__ import annotations

from datetime import datetime

from connectors.base import CompositeConnector
from connectors.email_sms import EmailSMSConnector
from connectors.payment_processor import PaymentProcessorConnector
from core.audit import AuditStore
from core.diagnosis.llm_engine import get_diagnosis_engine
from core.models import (
    ActionType,
    Case,
    PaymentEvent,
    Signal,
    SignalSource,
    WorkflowType,
)
from core.pipeline import PipelineEngine


def build_engine(audit_store: AuditStore, holdout_rate: float = 0.15) -> PipelineEngine:
    payment = PaymentProcessorConnector()
    messaging = EmailSMSConnector()
    connector = CompositeConnector({
        ActionType.RETRY_MANDATE_DEBIT: payment,
        ActionType.SEND_SMS: messaging,
        ActionType.REQUEST_MANDATE_REAUTHORIZATION: messaging,
    })
    return PipelineEngine(
        workflow=WorkflowType.MANDATE_RETRY,
        diagnosis_engine=get_diagnosis_engine(),
        audit_store=audit_store,
        connector=connector,
        holdout_rate=holdout_rate,
    )


def build_signal(raw: dict) -> Signal:
    amount = raw.get("amount", 0.0)
    return Signal(
        workflow=WorkflowType.MANDATE_RETRY,
        occurred_at=datetime.fromisoformat(raw["occurred_at"])
        if "occurred_at" in raw
        else datetime.utcnow(),
        source=SignalSource(raw.get("source", "simulation")),
        account_id=raw.get("account_id", ""),
        revenue_at_risk=amount,
        payload={
            "mandate_id": raw.get("mandate_id", ""),
            "mandate_type": raw.get("mandate_type", "upi"),
            "network": raw.get("network", "npci"),
            "amount": amount,
            "currency": raw.get("currency", "INR"),
            "failed_at": raw.get("failed_at", datetime.utcnow().isoformat()),
            "failure_code": raw.get("failure_code", "UNKNOWN"),
            "is_mandate_active": raw.get("is_mandate_active", True),
            "prior_retry_count": raw.get("prior_retry_count", 0),
            "max_retries_allowed": raw.get("max_retries_allowed", 3),
            "customer_phone": raw.get("customer_phone", ""),
        },
    )


def build_payment_event(case: Case, success: bool) -> PaymentEvent:
    return PaymentEvent(
        case_id=case.case_id,
        account_id=case.account_id,
        amount=case.signal.payload.get("amount", case.revenue_at_risk),
        currency=case.signal.payload.get("currency", "INR"),
        status="success" if success else "failure",
        processor=f"{case.signal.payload.get('mandate_type', 'mandate')}_network",
        occurred_at=datetime.utcnow(),
    )


def run_case(raw_signal: dict, audit_store: AuditStore, holdout_rate: float = 0.15) -> Case:
    engine = build_engine(audit_store, holdout_rate=holdout_rate)
    signal = build_signal(raw_signal)
    return engine.run(signal)
