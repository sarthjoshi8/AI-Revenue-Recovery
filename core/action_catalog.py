"""
core/action_catalog.py — Bounded, versioned action catalog per workflow.

Each workflow ships with a fixed list of allowed ActionTypes.
The pipeline SELECTS from this list — it never invents new action types at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.models import ActionType, WorkflowType


# ---------------------------------------------------------------------------
# Action definition
# ---------------------------------------------------------------------------


@dataclass
class ActionDefinition:
    """A single entry in the bounded action catalog."""

    action_type: ActionType
    display_name: str
    description: str
    requires_human_approval: bool = False
    channel: Optional[str] = None           # email, sms, push, ivr, payment
    max_per_case: int = 10                  # How many times this action can fire per case
    cooldown_hours: float = 0.0             # Minimum gap between same-action fires
    legal_language: bool = False            # True → always hits approval gate
    unit_cost: float = 0.0                  # Cost in USD to execute this action


# ---------------------------------------------------------------------------
# Catalog registry — one entry per workflow
# ---------------------------------------------------------------------------


WORKFLOW_CATALOGS: dict[WorkflowType, list[ActionDefinition]] = {

    WorkflowType.PAYMENT_DEGRADATION: [
        ActionDefinition(
            ActionType.RETRY_WITH_BACKOFF,
            "Retry with backoff",
            "Retry the failed charge with exponential backoff",
            channel="payment", max_per_case=3, cooldown_hours=1, unit_cost=0.05,
        ),
        ActionDefinition(
            ActionType.ROUTE_ALTERNATE_PROCESSOR,
            "Route to alternate processor",
            "Reroute the transaction to a healthier acquirer/processor",
            channel="payment", max_per_case=2, cooldown_hours=0, unit_cost=0.08,
        ),
        ActionDefinition(
            ActionType.TRIGGER_CARD_UPDATER,
            "Trigger card updater",
            "Request updated card credentials from the network (Account Updater)",
            channel="payment", max_per_case=1, cooldown_hours=0, unit_cost=0.45,
        ),
        ActionDefinition(
            ActionType.FALLBACK_PAYMENT_METHOD,
            "Fall back to alternate payment method",
            "Prompt customer to pay with an alternate saved method",
            channel="payment", max_per_case=1, cooldown_hours=0, unit_cost=0.02,
        ),
    ],

    WorkflowType.CHECKOUT_ABANDONMENT: [
        ActionDefinition(
            ActionType.SEND_EMAIL,
            "Send recovery email",
            "Send a personalized cart-recovery email",
            channel="email", max_per_case=2, cooldown_hours=24, unit_cost=0.005,
        ),
        ActionDefinition(
            ActionType.SEND_SMS,
            "Send recovery SMS",
            "Send a short-link SMS nudge",
            channel="sms", max_per_case=1, cooldown_hours=48, unit_cost=0.035,
        ),
        ActionDefinition(
            ActionType.SEND_PUSH,
            "Send push notification",
            "Send an in-app or web push notification",
            channel="push", max_per_case=1, cooldown_hours=6, unit_cost=0.001,
        ),
    ],

    WorkflowType.SUBSCRIPTION_RECOVERY: [
        ActionDefinition(
            ActionType.SEND_DUNNING_MESSAGE,
            "Send dunning message",
            "Timed email/SMS nudge during the dunning sequence",
            channel="email", max_per_case=4, cooldown_hours=48, unit_cost=0.008,
        ),
        ActionDefinition(
            ActionType.TRIGGER_CARD_UPDATER,
            "Trigger card updater",
            "Request updated card from network before retry",
            channel="payment", max_per_case=1, cooldown_hours=0, unit_cost=0.45,
        ),
        ActionDefinition(
            ActionType.RETRY_WITH_BACKOFF,
            "Retry subscription charge",
            "Retry charge aligned to estimated payday/billing cycle",
            channel="payment", max_per_case=4, cooldown_hours=24, unit_cost=0.05,
        ),
        ActionDefinition(
            ActionType.SEND_GRACE_PERIOD_NOTICE,
            "Send grace-period notice",
            "Notify customer of service suspension grace period",
            channel="email", max_per_case=1, cooldown_hours=0, unit_cost=0.005,
        ),
        ActionDefinition(
            ActionType.OFFER_PAYMENT_PLAN,
            "Offer payment plan",
            "Offer a short-term partial-payment plan to retain subscriber",
            channel="email", max_per_case=1, cooldown_hours=0, unit_cost=0.01,
        ),
    ],

    WorkflowType.B2B_RECEIVABLES: [
        ActionDefinition(
            ActionType.SEND_FRIENDLY_REMINDER,
            "Send friendly reminder",
            "Polite first-touch reminder for overdue invoice",
            channel="email", max_per_case=2, cooldown_hours=72, unit_cost=0.01,
        ),
        ActionDefinition(
            ActionType.SEND_FORMAL_NOTICE,
            "Send formal notice",
            "Formal overdue notice with payment deadline",
            channel="email", max_per_case=1, cooldown_hours=0,
            legal_language=False, unit_cost=0.02,
        ),
        ActionDefinition(
            ActionType.ESCALATE_TO_AR,
            "Escalate to AR team",
            "Hand off to accounts-receivable team for manual outreach",
            requires_human_approval=True, max_per_case=1, cooldown_hours=0, unit_cost=2.50,
        ),
        ActionDefinition(
            ActionType.ESCALATE_TO_COLLECTIONS,
            "Escalate to collections",
            "Initiate external collections referral",
            requires_human_approval=True, legal_language=True,
            max_per_case=1, cooldown_hours=0, unit_cost=12.00,
        ),
    ],

    WorkflowType.MANDATE_RETRY: [
        ActionDefinition(
            ActionType.RETRY_MANDATE_DEBIT,
            "Retry mandate debit",
            "Re-present the mandate debit within permitted retry window",
            channel="payment", max_per_case=3, cooldown_hours=24, unit_cost=0.04,
        ),
        ActionDefinition(
            ActionType.SEND_SMS,
            "Send balance-low SMS",
            "Notify customer to top up account before next debit attempt",
            channel="sms", max_per_case=2, cooldown_hours=24, unit_cost=0.035,
        ),
        ActionDefinition(
            ActionType.REQUEST_MANDATE_REAUTHORIZATION,
            "Request mandate reauthorization",
            "Prompt customer to re-authorize expired/cancelled mandate",
            channel="email", max_per_case=1, cooldown_hours=0, unit_cost=0.01,
        ),
    ],

    WorkflowType.HINGLISH_VOICE: [
        ActionDefinition(
            ActionType.LOG_HINGLISH_SCRIPT,
            "Log Hinglish outreach script",
            "Generate and log a Hinglish voice/IVR recovery script",
            channel="ivr", max_per_case=3, cooldown_hours=48, unit_cost=0.02,
        ),
        ActionDefinition(
            ActionType.INITIATE_IVR_SCRIPT,
            "Initiate IVR call (stub)",
            "Stub: queue an IVR call with the generated script",
            channel="ivr", max_per_case=2, cooldown_hours=48, unit_cost=0.15,
        ),
    ],

    WorkflowType.PROMISE_TO_PAY: [
        ActionDefinition(
            ActionType.LOG_PROMISE,
            "Log PTP commitment",
            "Record a customer promise-to-pay with due date",
            max_per_case=5, cooldown_hours=0, unit_cost=0.01,
        ),
        ActionDefinition(
            ActionType.SEND_PTP_REMINDER,
            "Send PTP reminder",
            "Remind customer of upcoming promised payment",
            channel="email", max_per_case=3, cooldown_hours=24, unit_cost=0.01,
        ),
        ActionDefinition(
            ActionType.ESCALATE_BROKEN_PTP,
            "Escalate broken PTP",
            "Route broken promise to B2B receivables or subscription dunning workflow",
            max_per_case=1, cooldown_hours=0, unit_cost=0.50,
        ),
    ],
}


# ---------------------------------------------------------------------------
# ActionCatalog class
# ---------------------------------------------------------------------------


class ActionCatalog:
    """
    Provides the bounded list of allowed actions for a specific workflow.
    The pipeline calls `select()` which returns only actions from this catalog.
    """

    CATALOG_VERSION = "v1.0"

    def __init__(self, workflow: WorkflowType) -> None:
        self.workflow = workflow
        self._catalog: list[ActionDefinition] = WORKFLOW_CATALOGS.get(workflow, [])

    def get_all(self) -> list[ActionDefinition]:
        return list(self._catalog)

    def get(self, action_type: ActionType) -> Optional[ActionDefinition]:
        for defn in self._catalog:
            if defn.action_type == action_type:
                return defn
        return None

    def is_allowed(self, action_type: ActionType) -> bool:
        return any(d.action_type == action_type for d in self._catalog)

    def requires_approval(self, action_type: ActionType) -> bool:
        defn = self.get(action_type)
        return defn.requires_human_approval if defn else False

    def get_by_channel(self, channel: str) -> list[ActionDefinition]:
        return [d for d in self._catalog if d.channel == channel]

    def version(self) -> str:
        return self.CATALOG_VERSION

    def select_for_cause(
        self,
        recommended_actions: list[ActionType],
        excluded_actions: Optional[list[ActionType]] = None,
    ) -> Optional[ActionDefinition]:
        """
        Given a prioritized list of recommended actions from the diagnosis step,
        return the first one that exists in this workflow's catalog.
        """
        excluded = set(excluded_actions or [])
        for action_type in recommended_actions:
            if action_type in excluded:
                continue
            defn = self.get(action_type)
            if defn:
                return defn
        return None
