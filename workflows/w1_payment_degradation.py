"""
workflows/w1_payment_degradation.py — Payment degradation recovery workflow.

Detects abnormal decline-rate spikes segmented by processor/issuer/BIN/region/
card-network. Diagnoses likely cause and executes a bounded fix.

Allowed actions (from catalog):
  - retry_with_backoff
  - route_alternate_processor
  - trigger_card_updater
  - fallback_payment_method
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from connectors.base import CompositeConnector
from connectors.payment_processor import PaymentProcessorConnector
from core.audit import AuditStore
from core.diagnosis.llm_engine import get_diagnosis_engine
from core.models import (
    Case,
    CaseStatus,
    Intervention,
    PaymentEvent,
    Signal,
    SignalSource,
    WorkflowType,
)
from core.pipeline import PipelineEngine


# ---------------------------------------------------------------------------
# Workflow factory
# ---------------------------------------------------------------------------


def build_engine(audit_store: AuditStore, holdout_rate: float = 0.15) -> PipelineEngine:
    """Create a configured pipeline engine for W1."""
    connector = PaymentProcessorConnector()
    return PipelineEngine(
        workflow=WorkflowType.PAYMENT_DEGRADATION,
        diagnosis_engine=get_diagnosis_engine(),
        audit_store=audit_store,
        connector=connector,
        holdout_rate=holdout_rate,
    )


# ---------------------------------------------------------------------------
# Signal builder (from raw data dict)
# ---------------------------------------------------------------------------


def build_signal(raw: dict) -> Signal:
    """
    Build a W1 Signal from raw event data.
    raw keys: processor, issuer_bin, card_network, region, decline_rate_current,
              decline_rate_baseline, sample_volume, decline_codes, account_id,
              window_minutes, revenue_at_risk.
    """
    decline_rate = raw.get("decline_rate_current", 0.0)
    baseline = raw.get("decline_rate_baseline", 0.04)
    sample_vol = raw.get("sample_volume", 0)

    # Revenue at risk: estimated from volume × avg transaction value × spike delta
    revenue_at_risk = raw.get(
        "revenue_at_risk",
        sample_vol * (decline_rate - baseline) * raw.get("avg_txn_value", 75.0),
    )

    return Signal(
        workflow=WorkflowType.PAYMENT_DEGRADATION,
        occurred_at=datetime.fromisoformat(raw["occurred_at"])
        if "occurred_at" in raw
        else datetime.utcnow(),
        source=SignalSource(raw.get("source", "simulation")),
        account_id=raw.get("account_id", "processor_global"),
        revenue_at_risk=max(0.0, revenue_at_risk),
        payload={
            "processor": raw.get("processor", "unknown"),
            "issuer_bin": raw.get("issuer_bin", ""),
            "card_network": raw.get("card_network", "visa"),
            "region": raw.get("region", "US"),
            "decline_rate_current": decline_rate,
            "decline_rate_baseline": baseline,
            "sample_volume": sample_vol,
            "decline_codes": raw.get("decline_codes", {}),
            "window_minutes": raw.get("window_minutes", 60),
            "avg_txn_value": raw.get("avg_txn_value", 75.0),
        },
    )


# ---------------------------------------------------------------------------
# Payment event builder (for attribution simulation)
# ---------------------------------------------------------------------------


def build_payment_event(case: Case, success: bool, amount: Optional[float] = None) -> PaymentEvent:
    return PaymentEvent(
        case_id=case.case_id,
        account_id=case.account_id,
        amount=amount or case.revenue_at_risk,
        currency="USD",
        status="success" if success else "failure",
        processor=case.signal.payload.get("processor", "unknown"),
        occurred_at=datetime.utcnow(),
        metadata={"workflow": WorkflowType.PAYMENT_DEGRADATION.value},
    )


# ---------------------------------------------------------------------------
# Run one case
# ---------------------------------------------------------------------------


def run_case(raw_signal: dict, audit_store: AuditStore, holdout_rate: float = 0.15) -> Case:
    """
    Full pipeline run for one payment-degradation signal.
    Returns the resulting Case (check case.status for outcome).
    """
    engine = build_engine(audit_store, holdout_rate=holdout_rate)
    signal = build_signal(raw_signal)
    return engine.run(signal)
