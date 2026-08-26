"""
workflows/w6_hinglish_voice.py — Hinglish voice recovery workflow.

Generates and logs Hinglish (Hindi-English code-mixed) outreach scripts for
payment recovery or promise-to-pay confirmation.

STUB: Does not make real telephony calls. Generates structured script payload,
logs it with response-capture fields and intended outcome to the audit log.
"""

from __future__ import annotations

from datetime import datetime

from connectors.base import CompositeConnector
from connectors.ivr_voice import IVRVoiceConnector
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
    ivr = IVRVoiceConnector()
    connector = CompositeConnector({
        ActionType.LOG_HINGLISH_SCRIPT: ivr,
        ActionType.INITIATE_IVR_SCRIPT: ivr,
    })
    return PipelineEngine(
        workflow=WorkflowType.HINGLISH_VOICE,
        diagnosis_engine=get_diagnosis_engine(),
        audit_store=audit_store,
        connector=connector,
        holdout_rate=holdout_rate,
    )


def build_signal(raw: dict) -> Signal:
    outstanding = raw.get("outstanding_amount", 0.0)
    return Signal(
        workflow=WorkflowType.HINGLISH_VOICE,
        occurred_at=datetime.fromisoformat(raw["occurred_at"])
        if "occurred_at" in raw
        else datetime.utcnow(),
        source=SignalSource(raw.get("source", "simulation")),
        account_id=raw.get("account_id", ""),
        revenue_at_risk=outstanding,
        payload={
            "interaction_id": raw.get("interaction_id", ""),
            "channel": raw.get("channel", "ivr"),
            "language_preference": raw.get("language_preference", "hi-en"),
            "outstanding_amount": outstanding,
            "currency": raw.get("currency", "INR"),
            "last_contact_date": raw.get("last_contact_date"),
            "previous_promise_date": raw.get("previous_promise_date"),
            "customer_name": raw.get("customer_name", "Customer"),
            "preferred_call_time": raw.get("preferred_call_time"),
        },
    )


def build_payment_event(case: Case, success: bool) -> PaymentEvent:
    return PaymentEvent(
        case_id=case.case_id,
        account_id=case.account_id,
        amount=case.signal.payload.get("outstanding_amount", case.revenue_at_risk),
        currency=case.signal.payload.get("currency", "INR"),
        status="success" if success else "failure",
        processor="ivr_payment",
        occurred_at=datetime.utcnow(),
    )


def run_case(raw_signal: dict, audit_store: AuditStore, holdout_rate: float = 0.15) -> Case:
    engine = build_engine(audit_store, holdout_rate=holdout_rate)
    signal = build_signal(raw_signal)
    return engine.run(signal)
