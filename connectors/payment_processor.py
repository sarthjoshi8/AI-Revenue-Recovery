"""
connectors/payment_processor.py — Payment processor connector stub.

Supports: retry_with_backoff, route_alternate_processor,
          trigger_card_updater, fallback_payment_method.

To wire in a real processor (Stripe, Adyen, Braintree, etc.):
  1. Set PAYMENT_PROCESSOR_API_KEY in env
  2. Replace the stub body in each _stub_* method with a real API call
  3. The interface (ActionResult) stays identical
"""

from __future__ import annotations

import random
from datetime import datetime

from connectors.base import BaseConnector
from core.models import ActionResult, ActionType, Case, Intervention


class PaymentProcessorConnector(BaseConnector):
    """
    Stub payment processor connector.

    Simulates realistic outcomes:
      - 70% of retries succeed (on first/second attempt)
      - Card updater succeeds 80% of the time
      - Processor routing succeeds 85% of the time
    """

    @property
    def name(self) -> str:
        return "payment_processor_stub"

    def execute(self, case: Case, intervention: Intervention) -> ActionResult:
        action = (
            ActionType(intervention.action_type)
            if isinstance(intervention.action_type, str)
            else intervention.action_type
        )
        dispatch = {
            ActionType.RETRY_WITH_BACKOFF: self._retry_with_backoff,
            ActionType.ROUTE_ALTERNATE_PROCESSOR: self._route_alternate,
            ActionType.TRIGGER_CARD_UPDATER: self._trigger_card_updater,
            ActionType.FALLBACK_PAYMENT_METHOD: self._fallback_method,
            ActionType.RETRY_MANDATE_DEBIT: self._retry_mandate,
        }
        fn = dispatch.get(action, self._unsupported)
        return fn(case, intervention)

    def _retry_with_backoff(self, case: Case, intervention: Intervention) -> ActionResult:
        attempt = intervention.parameters.get("attempt_num", 1)
        # Success probability decreases with attempts (realistic)
        success_prob = max(0.3, 0.75 - (attempt - 1) * 0.15)
        success = random.random() < success_prob

        payload: dict = {
            "action": "retry_with_backoff",
            "processor": intervention.parameters.get("processor", "default"),
            "attempt_num": attempt,
            "backoff_seconds": intervention.parameters.get("backoff_seconds", 60),
            "simulated_outcome": "success" if success else "declined",
        }
        if success:
            payload["transaction_id"] = f"txn_{case.case_id[:8]}_{attempt}"
            payload["amount"] = case.revenue_at_risk
        else:
            payload["decline_code"] = random.choice(["51", "91", "DO_NOT_HONOR"])

        return ActionResult(
            success=success,
            connector=self.name,
            response_payload=payload,
            executed_at=datetime.utcnow(),
            is_stub=True,
            error_message=None if success else f"Declined: {payload.get('decline_code')}",
        )

    def _route_alternate(self, case: Case, intervention: Intervention) -> ActionResult:
        success = random.random() < 0.82
        return ActionResult(
            success=success,
            connector=self.name,
            response_payload={
                "action": "route_alternate_processor",
                "from_processor": intervention.parameters.get("from_processor", "primary"),
                "to_processor": intervention.parameters.get("to_processor", "backup"),
                "simulated_outcome": "routed_successfully" if success else "routing_failed",
                "transaction_id": f"txn_alt_{case.case_id[:8]}" if success else None,
            },
            executed_at=datetime.utcnow(),
            is_stub=True,
            error_message=None if success else "Alternate processor also declined",
        )

    def _trigger_card_updater(self, case: Case, intervention: Intervention) -> ActionResult:
        updated = random.random() < 0.78
        return ActionResult(
            success=True,   # The request itself always succeeds; card update is async
            connector=self.name,
            response_payload={
                "action": "card_updater_triggered",
                "card_last4": intervention.parameters.get("card_last4", "****"),
                "network": intervention.parameters.get("network", "visa"),
                "update_status": "updated" if updated else "no_update_available",
                "note": "Real card details would come from network Account Updater API",
            },
            executed_at=datetime.utcnow(),
            is_stub=True,
        )

    def _fallback_method(self, case: Case, intervention: Intervention) -> ActionResult:
        success = random.random() < 0.6
        return ActionResult(
            success=success,
            connector=self.name,
            response_payload={
                "action": "fallback_payment_method",
                "simulated_outcome": "paid_with_alternate" if success else "no_alternate_available",
                "note": "Stub: would call payment vault to retrieve alternate saved method",
            },
            executed_at=datetime.utcnow(),
            is_stub=True,
            error_message=None if success else "No alternate payment method on file",
        )

    def _retry_mandate(self, case: Case, intervention: Intervention) -> ActionResult:
        attempt = intervention.parameters.get("attempt_num", 1)
        success = random.random() < max(0.35, 0.7 - (attempt - 1) * 0.15)
        return ActionResult(
            success=success,
            connector=self.name,
            response_payload={
                "action": "retry_mandate_debit",
                "mandate_id": case.signal.payload.get("mandate_id", "mandate_unknown"),
                "attempt_num": attempt,
                "simulated_outcome": "debit_success" if success else "debit_failed",
                "note": "Stub: real call would go to NPCI/NACHA/BACS mandate API",
            },
            executed_at=datetime.utcnow(),
            is_stub=True,
            error_message=None if success else "Mandate debit failed (simulated)",
        )

    def _unsupported(self, case: Case, intervention: Intervention) -> ActionResult:
        return ActionResult(
            success=False,
            connector=self.name,
            response_payload={"error": f"Unsupported action: {intervention.action_type}"},
            executed_at=datetime.utcnow(),
            is_stub=True,
            error_message=f"PaymentProcessorConnector does not support {intervention.action_type}",
        )
