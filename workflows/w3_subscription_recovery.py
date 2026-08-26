"""
workflows/w3_subscription_recovery.py — Failed-subscription recovery (involuntary churn).

Detects failed renewal charges. Classifies decline reason. Runs smart dunning:
  - Timed retries aligned to payday/billing cycles
  - Card-update prompts
  - Grace-period messaging

Stopping: payment success, voluntary cancel, dunning-cap (5 attempts).
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
        ActionType.RETRY_WITH_BACKOFF: payment,
        ActionType.TRIGGER_CARD_UPDATER: payment,
        ActionType.SEND_DUNNING_MESSAGE: messaging,
        ActionType.SEND_GRACE_PERIOD_NOTICE: messaging,
        ActionType.OFFER_PAYMENT_PLAN: messaging,
    })
    return PipelineEngine(
        workflow=WorkflowType.SUBSCRIPTION_RECOVERY,
        diagnosis_engine=get_diagnosis_engine(),
        audit_store=audit_store,
        connector=connector,
        holdout_rate=holdout_rate,
    )


def build_signal(raw: dict) -> Signal:
    amount = raw.get("amount", 0.0)
    return Signal(
        workflow=WorkflowType.SUBSCRIPTION_RECOVERY,
        occurred_at=datetime.fromisoformat(raw["occurred_at"])
        if "occurred_at" in raw
        else datetime.utcnow(),
        source=SignalSource(raw.get("source", "simulation")),
        account_id=raw.get("account_id", ""),
        revenue_at_risk=amount,
        payload={
            "subscription_id": raw.get("subscription_id", ""),
            "plan_name": raw.get("plan_name", ""),
            "amount": amount,
            "currency": raw.get("currency", "USD"),
            "failed_at": raw.get("failed_at", datetime.utcnow().isoformat()),
            "decline_code": raw.get("decline_code", "UNKNOWN"),
            "processor": raw.get("processor", "stripe"),
            "card_last4": raw.get("card_last4", "****"),
            "card_expiry": raw.get("card_expiry", ""),
            "is_first_failure": raw.get("is_first_failure", True),
            "prior_successful_charges": raw.get("prior_successful_charges", 0),
            "customer_email": raw.get("customer_email", ""),
        },
    )


def build_payment_event(case: Case, success: bool) -> PaymentEvent:
    return PaymentEvent(
        case_id=case.case_id,
        account_id=case.account_id,
        amount=case.signal.payload.get("amount", case.revenue_at_risk),
        currency=case.signal.payload.get("currency", "USD"),
        status="success" if success else "failure",
        processor=case.signal.payload.get("processor", "stripe"),
        occurred_at=datetime.utcnow(),
    )


def run_case(raw_signal: dict, audit_store: AuditStore, holdout_rate: float = 0.15) -> Case:
    engine = build_engine(audit_store, holdout_rate=holdout_rate)
    signal = build_signal(raw_signal)
    return engine.run(signal)
