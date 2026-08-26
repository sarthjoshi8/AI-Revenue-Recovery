"""
workflows/w4_b2b_receivables.py — B2B receivables chaser workflow.

Detects invoices crossing aging thresholds. Executes graduated chase sequence:
  friendly reminder → formal notice → AR escalation (APPROVAL GATE)
  → collections referral (APPROVAL GATE + legal language flag)

HUMAN APPROVAL GATE fires before any collections escalation.
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
        ActionType.SEND_FRIENDLY_REMINDER: messaging,
        ActionType.SEND_FORMAL_NOTICE: messaging,
        ActionType.ESCALATE_TO_AR: crm,
        ActionType.ESCALATE_TO_COLLECTIONS: crm,
        ActionType.HUMAN_REVIEW: crm,
    })
    return PipelineEngine(
        workflow=WorkflowType.B2B_RECEIVABLES,
        diagnosis_engine=get_diagnosis_engine(),
        audit_store=audit_store,
        connector=connector,
        holdout_rate=holdout_rate,
    )


def build_signal(raw: dict) -> Signal:
    amount = raw.get("amount", 0.0)
    days_overdue = raw.get("days_overdue", 0)
    return Signal(
        workflow=WorkflowType.B2B_RECEIVABLES,
        occurred_at=datetime.fromisoformat(raw["occurred_at"])
        if "occurred_at" in raw
        else datetime.utcnow(),
        source=SignalSource(raw.get("source", "simulation")),
        account_id=raw.get("account_id", ""),
        revenue_at_risk=amount,
        payload={
            "invoice_id": raw.get("invoice_id", ""),
            "invoice_number": raw.get("invoice_number", ""),
            "amount": amount,
            "currency": raw.get("currency", "USD"),
            "due_date": raw.get("due_date", ""),
            "days_overdue": days_overdue,
            "account_tier": raw.get("account_tier", "smb"),
            "payment_history_score": raw.get("payment_history_score", 0.5),
            "has_active_dispute": raw.get("has_active_dispute", False),
            "contract_terms": raw.get("contract_terms", "net30"),
            "customer_email": raw.get("customer_email", ""),
            "contact_name": raw.get("contact_name", ""),
        },
    )


def build_payment_event(case: Case, success: bool) -> PaymentEvent:
    return PaymentEvent(
        case_id=case.case_id,
        account_id=case.account_id,
        amount=case.signal.payload.get("amount", case.revenue_at_risk),
        currency=case.signal.payload.get("currency", "USD"),
        status="success" if success else "failure",
        processor="bank_transfer",
        occurred_at=datetime.utcnow(),
    )


def run_case(raw_signal: dict, audit_store: AuditStore, holdout_rate: float = 0.15) -> Case:
    engine = build_engine(audit_store, holdout_rate=holdout_rate)
    signal = build_signal(raw_signal)
    return engine.run(signal)
