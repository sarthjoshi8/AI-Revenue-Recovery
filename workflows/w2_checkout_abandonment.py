"""
workflows/w2_checkout_abandonment.py — Checkout drop-off recovery workflow.

Detects abandoned carts/sessions. Scores recoverability. Triggers capped,
sequenced nudges (email → SMS → push) with throttling and quiet hours.

Stopping conditions: purchase, opt-out, max attempts (3), cart > 3 days old.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from connectors.crm import CRMConnector
from connectors.email_sms import EmailSMSConnector
from core.audit import AuditStore
from core.diagnosis.llm_engine import get_diagnosis_engine
from core.models import (
    Case,
    PaymentEvent,
    Signal,
    SignalSource,
    WorkflowType,
)
from core.pipeline import PipelineEngine


def build_engine(audit_store: AuditStore, holdout_rate: float = 0.15) -> PipelineEngine:
    connector = EmailSMSConnector()
    return PipelineEngine(
        workflow=WorkflowType.CHECKOUT_ABANDONMENT,
        diagnosis_engine=get_diagnosis_engine(),
        audit_store=audit_store,
        connector=connector,
        holdout_rate=holdout_rate,
    )


def build_signal(raw: dict) -> Signal:
    cart_value = raw.get("cart_value", 0.0)
    minutes_since = raw.get("time_since_abandonment_minutes", 30)
    prior_purchases = raw.get("prior_purchase_count", 0)

    # Recoverability score → weights revenue at risk
    recoverability = _score_recoverability(cart_value, minutes_since, prior_purchases)

    return Signal(
        workflow=WorkflowType.CHECKOUT_ABANDONMENT,
        occurred_at=datetime.fromisoformat(raw["occurred_at"])
        if "occurred_at" in raw
        else datetime.utcnow(),
        source=SignalSource(raw.get("source", "simulation")),
        account_id=raw.get("account_id", raw.get("session_id", "anon")),
        revenue_at_risk=cart_value * recoverability,
        payload={
            "session_id": raw.get("session_id", ""),
            "cart_value": cart_value,
            "currency": raw.get("currency", "USD"),
            "items": raw.get("items", []),
            "abandoned_at": raw.get("abandoned_at", datetime.utcnow().isoformat()),
            "time_since_abandonment_minutes": minutes_since,
            "prior_purchase_count": prior_purchases,
            "all_items_available": raw.get("all_items_available", True),
            "customer_email": raw.get("customer_email", ""),
            "customer_phone": raw.get("customer_phone", ""),
            "recoverability_score": round(recoverability, 3),
        },
    )


def _score_recoverability(
    cart_value: float, minutes_since: int, prior_purchases: int
) -> float:
    """
    Simple recoverability score (0–1).
    Higher cart value, fewer minutes since, and more prior purchases → higher score.
    """
    value_score = min(1.0, cart_value / 500.0)
    time_score = max(0.0, 1.0 - minutes_since / 4320.0)   # decays over 3 days
    loyalty_score = min(1.0, prior_purchases / 5.0)
    return round((value_score * 0.4 + time_score * 0.4 + loyalty_score * 0.2), 3)


def build_payment_event(case: Case, success: bool) -> PaymentEvent:
    return PaymentEvent(
        case_id=case.case_id,
        account_id=case.account_id,
        amount=case.signal.payload.get("cart_value", case.revenue_at_risk),
        currency=case.signal.payload.get("currency", "USD"),
        status="success" if success else "failure",
        processor="checkout",
        occurred_at=datetime.utcnow(),
    )


def run_case(raw_signal: dict, audit_store: AuditStore, holdout_rate: float = 0.15) -> Case:
    engine = build_engine(audit_store, holdout_rate=holdout_rate)
    signal = build_signal(raw_signal)
    case = engine.run(signal)
    # Load consent flags from CRM
    flags = CRMConnector.fetch_account_flags(case.account_id)
    case.consent_flags = {
        "opt_out_email": flags.get("opt_out_email", False),
        "opt_out_sms": flags.get("opt_out_sms", False),
        "opt_out_push": flags.get("opt_out_push", False),
        "opt_out_all": flags.get("opt_out_all", False),
    }
    return case
