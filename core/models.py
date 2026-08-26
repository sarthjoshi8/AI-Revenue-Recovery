"""
AI Revenue Recovery — Core Data Models

All Pydantic models used throughout the pipeline. These are the single source
of truth for data shapes; every workflow, connector, and report imports from here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CaseGroup(str, Enum):
    TREATMENT = "treatment"              # Actionable case receiving interventions
    HOLDOUT = "holdout"                  # Control group — no intervention taken for incrementality measurement


class WorkflowType(str, Enum):
    PAYMENT_DEGRADATION = "payment_degradation"
    CHECKOUT_ABANDONMENT = "checkout_abandonment"
    SUBSCRIPTION_RECOVERY = "subscription_recovery"
    B2B_RECEIVABLES = "b2b_receivables"
    MANDATE_RETRY = "mandate_retry"
    HINGLISH_VOICE = "hinglish_voice"
    PROMISE_TO_PAY = "promise_to_pay"


class CaseStatus(str, Enum):
    OPEN = "open"                        # Just detected, not yet acted on
    ACTIVE = "active"                    # Intervention in progress
    RECOVERED = "recovered"             # Payment matched — revenue attributed
    CAPPED = "capped"                   # Max attempts reached — hard stop
    OPTED_OUT = "opted_out"             # Customer opted out — hard stop
    DISPUTED = "disputed"               # Active dispute — hard stop
    PENDING_APPROVAL = "pending_approval"  # Human approval gate triggered
    ESCALATED = "escalated"             # Handed off to human/collections
    HOLDOUT_CONTROL = "holdout_control" # Holdout control group — no intervention
    CLOSED = "closed"                   # Closed without recovery (other reason)


class PipelineStage(str, Enum):
    DETECT = "detect"
    DIAGNOSE = "diagnose"
    DECIDE = "decide"
    ACT = "act"
    MEASURE = "measure"
    AUDIT = "audit"
    STOP = "stop"                        # Stopping rule fired
    SUPPRESSED = "suppressed"            # Guardrail suppressed an action


class SignalSource(str, Enum):
    WEBHOOK = "webhook"
    BATCH_IMPORT = "batch_import"
    SIMULATION = "simulation"
    MANUAL = "manual"


class DeclineCategory(str, Enum):
    SOFT = "soft"                        # Retriable
    HARD = "hard"                        # Not retriable
    FRAUD = "fraud"                      # Flagged by issuer
    NETWORK = "network"                  # Processor/network issue
    EXPIRED_CARD = "expired_card"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    DO_NOT_HONOR = "do_not_honor"
    LOST_STOLEN = "lost_stolen"
    ISSUER_UNAVAILABLE = "issuer_unavailable"
    THREE_DS_FAILURE = "3ds_failure"
    MANDATE_EXPIRED = "mandate_expired"
    UNKNOWN = "unknown"


class ActionType(str, Enum):
    # Payment
    RETRY_WITH_BACKOFF = "retry_with_backoff"
    ROUTE_ALTERNATE_PROCESSOR = "route_alternate_processor"
    TRIGGER_CARD_UPDATER = "trigger_card_updater"
    FALLBACK_PAYMENT_METHOD = "fallback_payment_method"
    # Messaging
    SEND_EMAIL = "send_email"
    SEND_SMS = "send_sms"
    SEND_PUSH = "send_push"
    # Voice
    INITIATE_IVR_SCRIPT = "initiate_ivr_script"
    LOG_HINGLISH_SCRIPT = "log_hinglish_script"
    # Collections / B2B
    SEND_FRIENDLY_REMINDER = "send_friendly_reminder"
    SEND_FORMAL_NOTICE = "send_formal_notice"
    ESCALATE_TO_AR = "escalate_to_ar"          # Requires human approval
    ESCALATE_TO_COLLECTIONS = "escalate_to_collections"  # Requires human approval
    # PTP
    LOG_PROMISE = "log_promise"
    SEND_PTP_REMINDER = "send_ptp_reminder"
    ESCALATE_BROKEN_PTP = "escalate_broken_ptp"
    # Mandate
    RETRY_MANDATE_DEBIT = "retry_mandate_debit"
    REQUEST_MANDATE_REAUTHORIZATION = "request_mandate_reauthorization"
    # Subscription
    SEND_DUNNING_MESSAGE = "send_dunning_message"
    OFFER_PAYMENT_PLAN = "offer_payment_plan"
    SEND_GRACE_PERIOD_NOTICE = "send_grace_period_notice"
    # System & Control
    NO_OP = "no_op"                      # Nothing to do (already resolved)
    HOLDOUT_CONTROL = "holdout_control"  # Intentionally suppressed for incrementality proof
    HUMAN_REVIEW = "human_review"        # Route to human queue


# ---------------------------------------------------------------------------
# Core pipeline models
# ---------------------------------------------------------------------------


class Signal(BaseModel):
    """Raw event that triggers the pipeline for a specific workflow."""

    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workflow: WorkflowType
    occurred_at: datetime
    source: SignalSource = SignalSource.SIMULATION
    account_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    revenue_at_risk: float = 0.0        # Estimated $ at risk from this signal

    class Config:
        use_enum_values = True


class RootCause(BaseModel):
    """Output of the diagnosis step."""

    cause_code: str                      # e.g. "INSUFFICIENT_FUNDS", "ISSUER_OUTAGE"
    decline_category: Optional[DeclineCategory] = None
    description: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    is_retriable: bool = True
    recommended_action_types: list[ActionType] = Field(default_factory=list)
    model_version: str = "rules-v1"

    class Config:
        use_enum_values = True


class Intervention(BaseModel):
    """A single action selected from the bounded action catalog."""

    intervention_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action_type: ActionType
    parameters: dict[str, Any] = Field(default_factory=dict)
    catalog_version: str
    rationale: str
    requires_human_approval: bool = False
    channel: Optional[str] = None       # email, sms, push, ivr, etc.
    estimated_cost: float = 0.0

    class Config:
        use_enum_values = True


class ActionResult(BaseModel):
    """Result from executing an intervention via a connector."""

    success: bool
    connector: str
    response_payload: dict[str, Any] = Field(default_factory=dict)
    executed_at: datetime = Field(default_factory=datetime.utcnow)
    is_stub: bool = True                 # True when running against stubs
    cost: float = 0.0                    # Channel/execution cost incurred
    error_message: Optional[str] = None


class PaymentEvent(BaseModel):
    """A payment success or failure event — used for revenue attribution."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    case_id: Optional[str] = None       # Linked case (if known)
    account_id: str
    amount: float
    currency: str = "USD"
    status: str                          # "success" | "failure"
    processor: str = "unknown"
    decline_code: Optional[str] = None
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanApprovalRecord(BaseModel):
    """Record of a human approving or rejecting an escalation gate."""

    approval_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str
    intervention_id: str
    approver_id: str
    decision: str                        # "approved" | "rejected"
    notes: str = ""
    decided_at: datetime = Field(default_factory=datetime.utcnow)


class AuditEntry(BaseModel):
    """
    Immutable timestamped record for every pipeline step.
    Once written, this is NEVER mutated — only appended.
    """

    entry_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str
    workflow: str
    stage: PipelineStage
    attempt_num: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    model_version: str = "rules-v1"
    operator_id: str = "system"         # "system" or human approver ID
    ts_utc: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        use_enum_values = True


class Case(BaseModel):
    """
    A single revenue recovery case — state machine from OPEN to terminal state.
    Terminal states: RECOVERED, CAPPED, OPTED_OUT, DISPUTED, ESCALATED, CLOSED, HOLDOUT_CONTROL
    """

    case_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workflow: WorkflowType
    group: CaseGroup = CaseGroup.TREATMENT
    signal: Signal
    status: CaseStatus = CaseStatus.OPEN
    attempt_count: int = 0
    revenue_at_risk: float = 0.0
    revenue_recovered: float = 0.0
    total_intervention_cost: float = 0.0
    net_revenue_recovered: float = 0.0
    account_id: str
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    last_updated_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    close_reason: Optional[str] = None
    recovered_at: Optional[datetime] = None
    pending_approval_since: Optional[datetime] = None
    root_cause: Optional[RootCause] = None
    last_intervention: Optional[Intervention] = None
    consent_flags: dict[str, bool] = Field(default_factory=dict)  # channel -> ok
    metadata: dict[str, Any] = Field(default_factory=dict)

    class Config:
        use_enum_values = True

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            CaseStatus.RECOVERED,
            CaseStatus.CAPPED,
            CaseStatus.OPTED_OUT,
            CaseStatus.DISPUTED,
            CaseStatus.ESCALATED,
            CaseStatus.HOLDOUT_CONTROL,
            CaseStatus.CLOSED,
        }

    @property
    def time_to_recovery_hours(self) -> Optional[float]:
        if self.recovered_at and self.opened_at:
            delta = self.recovered_at - self.opened_at
            return delta.total_seconds() / 3600
        return None


# ---------------------------------------------------------------------------
# Reporting models
# ---------------------------------------------------------------------------


class WorkflowReport(BaseModel):
    """Per-workflow aggregated metrics from one batch run."""

    workflow: WorkflowType
    cases_detected: int = 0
    treatment_cases: int = 0
    holdout_cases: int = 0
    revenue_at_risk: float = 0.0
    treatment_revenue_at_risk: float = 0.0
    holdout_revenue_at_risk: float = 0.0
    recovery_attempts: int = 0
    revenue_recovered: float = 0.0
    holdout_revenue_recovered: float = 0.0
    incremental_revenue_recovered: float = 0.0
    treatment_recovery_rate_pct: float = 0.0
    holdout_recovery_rate_pct: float = 0.0
    incremental_lift_pct: float = 0.0
    total_intervention_cost: float = 0.0
    net_revenue_recovered: float = 0.0
    recovery_rate_pct: float = 0.0
    avg_time_to_recovery_hours: Optional[float] = None
    cases_recovered: int = 0
    cases_capped: int = 0
    cases_pending_approval: int = 0
    cases_escalated: int = 0
    cases_opted_out: int = 0
    cases_closed: int = 0
    suppressed_actions_count: int = 0
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        use_enum_values = True


class BatchReport(BaseModel):
    """Aggregated report across all workflows from one batch run."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime
    completed_at: datetime
    workflow_reports: list[WorkflowReport] = Field(default_factory=list)
    total_cases: int = 0
    total_treatment_cases: int = 0
    total_holdout_cases: int = 0
    total_revenue_at_risk: float = 0.0
    total_recovery_attempts: int = 0
    total_revenue_recovered: float = 0.0
    total_holdout_revenue_recovered: float = 0.0
    total_incremental_revenue_recovered: float = 0.0
    total_intervention_cost: float = 0.0
    total_net_revenue_recovered: float = 0.0
    overall_treatment_recovery_rate_pct: float = 0.0
    overall_holdout_recovery_rate_pct: float = 0.0
    overall_incremental_lift_pct: float = 0.0
    overall_recovery_rate_pct: float = 0.0
    avg_time_to_recovery_hours: Optional[float] = None
    cases_hit_cap: int = 0
    cases_pending_human_approval: int = 0
    total_suppressed_actions: int = 0

    def compute_totals(self) -> None:
        """Aggregate workflow reports into totals. Call after all workflows run."""
        self.total_cases = sum(r.cases_detected for r in self.workflow_reports)
        self.total_treatment_cases = sum(r.treatment_cases for r in self.workflow_reports)
        self.total_holdout_cases = sum(r.holdout_cases for r in self.workflow_reports)
        self.total_revenue_at_risk = sum(r.revenue_at_risk for r in self.workflow_reports)
        self.total_recovery_attempts = sum(r.recovery_attempts for r in self.workflow_reports)
        self.total_revenue_recovered = sum(r.revenue_recovered for r in self.workflow_reports)
        self.total_holdout_revenue_recovered = sum(r.holdout_revenue_recovered for r in self.workflow_reports)
        self.total_incremental_revenue_recovered = sum(r.incremental_revenue_recovered for r in self.workflow_reports)
        self.total_intervention_cost = sum(r.total_intervention_cost for r in self.workflow_reports)
        self.total_net_revenue_recovered = sum(r.net_revenue_recovered for r in self.workflow_reports)
        self.cases_hit_cap = sum(r.cases_capped for r in self.workflow_reports)
        self.cases_pending_human_approval = sum(
            r.cases_pending_approval for r in self.workflow_reports
        )
        self.total_suppressed_actions = sum(
            r.suppressed_actions_count for r in self.workflow_reports
        )

        treatment_risk = sum(r.treatment_revenue_at_risk for r in self.workflow_reports)
        holdout_risk = sum(r.holdout_revenue_at_risk for r in self.workflow_reports)

        if treatment_risk > 0:
            self.overall_treatment_recovery_rate_pct = (
                self.total_revenue_recovered / treatment_risk * 100
            )
        if holdout_risk > 0:
            self.overall_holdout_recovery_rate_pct = (
                self.total_holdout_revenue_recovered / holdout_risk * 100
            )

        if self.overall_holdout_recovery_rate_pct > 0:
            self.overall_incremental_lift_pct = (
                self.overall_treatment_recovery_rate_pct - self.overall_holdout_recovery_rate_pct
            )
        else:
            self.overall_incremental_lift_pct = self.overall_treatment_recovery_rate_pct

        if self.total_revenue_at_risk > 0:
            self.overall_recovery_rate_pct = (
                (self.total_revenue_recovered + self.total_holdout_revenue_recovered) / self.total_revenue_at_risk * 100
            )

        recovery_times = [
            r.avg_time_to_recovery_hours
            for r in self.workflow_reports
            if r.avg_time_to_recovery_hours is not None
        ]
        if recovery_times:
            self.avg_time_to_recovery_hours = sum(recovery_times) / len(recovery_times)


# ---------------------------------------------------------------------------
# Workflow-specific payload models
# ---------------------------------------------------------------------------


class PaymentDegradationSignal(BaseModel):
    """Extra payload for W1: Payment degradation signals."""

    processor: str
    issuer_bin: str
    card_network: str
    region: str
    decline_rate_current: float     # e.g. 0.15 = 15%
    decline_rate_baseline: float    # e.g. 0.04 = 4%
    sample_volume: int
    decline_codes: dict[str, int]   # code -> count
    window_minutes: int = 60


class CheckoutAbandonmentSignal(BaseModel):
    """Extra payload for W2: Checkout abandonment."""

    session_id: str
    cart_value: float
    currency: str
    items: list[dict[str, Any]]
    abandoned_at: datetime
    time_since_abandonment_minutes: int
    prior_purchase_count: int
    all_items_available: bool
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None


class SubscriptionFailureSignal(BaseModel):
    """Extra payload for W3: Failed subscription renewal."""

    subscription_id: str
    plan_name: str
    amount: float
    currency: str
    failed_at: datetime
    decline_code: str
    processor: str
    card_last4: str
    card_expiry: str
    is_first_failure: bool
    prior_successful_charges: int


class InvoiceOverdueSignal(BaseModel):
    """Extra payload for W4: B2B overdue invoice."""

    invoice_id: str
    invoice_number: str
    amount: float
    currency: str
    due_date: datetime
    days_overdue: int
    account_tier: str               # "enterprise", "mid-market", "smb"
    payment_history_score: float    # 0-1, higher = better payer
    has_active_dispute: bool
    contract_terms: str             # "net30", "net60", etc.


class MandateFailureSignal(BaseModel):
    """Extra payload for W5: Failed mandate debit."""

    mandate_id: str
    mandate_type: str               # "upi", "ach", "direct_debit"
    network: str                    # "npci", "nacha", "bacs"
    amount: float
    currency: str
    failed_at: datetime
    failure_code: str
    is_mandate_active: bool
    prior_retry_count: int
    max_retries_allowed: int


class VoiceInteractionSignal(BaseModel):
    """Extra payload for W6: Voice/IVR interaction."""

    interaction_id: str
    channel: str                    # "ivr", "agent_call", "chat"
    language_preference: str        # "hi", "en", "hi-en" (Hinglish)
    outstanding_amount: float
    currency: str
    last_contact_date: Optional[datetime] = None
    previous_promise_date: Optional[datetime] = None
    customer_name: str
    preferred_call_time: Optional[str] = None


class PromiseToPaySignal(BaseModel):
    """Extra payload for W7: Promise-to-pay tracking."""

    ptp_id: str
    captured_from: str              # "agent", "chatbot", "ivr"
    promised_amount: float
    currency: str
    promise_date: datetime
    days_until_due: int
    is_broken: bool
    related_invoice_id: Optional[str] = None
    related_subscription_id: Optional[str] = None
    customer_name: str
