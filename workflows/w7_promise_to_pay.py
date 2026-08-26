"""
workflows/w7_promise_to_pay.py — Promise-to-Pay (PTP) tracker workflow.

Logs PTP commitments from any channel (agent, chatbot, IVR).
Tracks due dates. Auto-detects broken promises (date passed, no payment).
Escalates broken PTPs into B2B receivables or subscription dunning.
"""

from __future__ import annotations

from datetime import datetime

from connectors.base import CompositeConnector
from connectors.crm import CRMConnector
from connectors.email_sms import EmailSMSConnector
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
    messaging = EmailSMSConnector()
    crm = CRMConnector()
    connector = CompositeConnector({
        ActionType.LOG_PROMISE: crm,
        ActionType.SEND_PTP_REMINDER: messaging,
        ActionType.ESCALATE_BROKEN_PTP: crm,
    })
    return PipelineEngine(
        workflow=WorkflowType.PROMISE_TO_PAY,
        diagnosis_engine=get_diagnosis_engine(),
        audit_store=audit_store,
        connector=connector,
        holdout_rate=holdout_rate,
    )


def build_signal(raw: dict) -> Signal:
    promised_amount = raw.get("promised_amount", 0.0)
    is_broken = raw.get("is_broken", False)

    # Revenue at risk is higher for broken promises (recovery less likely)
    risk_factor = 0.9 if is_broken else 0.7
    return Signal(
        workflow=WorkflowType.PROMISE_TO_PAY,
        occurred_at=datetime.fromisoformat(raw["occurred_at"])
        if "occurred_at" in raw
        else datetime.utcnow(),
        source=SignalSource(raw.get("source", "simulation")),
        account_id=raw.get("account_id", ""),
        revenue_at_risk=promised_amount * risk_factor,
        payload={
            "ptp_id": raw.get("ptp_id", ""),
            "captured_from": raw.get("captured_from", "agent"),
            "promised_amount": promised_amount,
            "currency": raw.get("currency", "USD"),
            "promise_date": raw.get("promise_date", ""),
            "days_until_due": raw.get("days_until_due", 0),
            "is_broken": is_broken,
            "related_invoice_id": raw.get("related_invoice_id"),
            "related_subscription_id": raw.get("related_subscription_id"),
            "customer_name": raw.get("customer_name", "Customer"),
            "customer_email": raw.get("customer_email", ""),
        },
    )


def build_payment_event(case: Case, success: bool) -> PaymentEvent:
    return PaymentEvent(
        case_id=case.case_id,
        account_id=case.account_id,
        amount=case.signal.payload.get("promised_amount", case.revenue_at_risk),
        currency=case.signal.payload.get("currency", "USD"),
        status="success" if success else "failure",
        processor="bank_transfer",
        occurred_at=datetime.utcnow(),
    )


def run_case(raw_signal: dict, audit_store: AuditStore, holdout_rate: float = 0.15) -> Case:
    engine = build_engine(audit_store, holdout_rate=holdout_rate)
    signal = build_signal(raw_signal)
    return engine.run(signal)
