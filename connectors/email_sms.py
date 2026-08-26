"""
connectors/email_sms.py — Email / SMS / Push notification connector stub.

Enforces:
  - Quiet hours (no messages 21:00–08:00 local time, approximated as UTC)
  - Per-channel opt-out check (reads case.consent_flags)
  - Throttle: max N messages per channel per case (enforced by action catalog max_per_case)

To wire in a real provider:
  - Email: SendGrid, AWS SES, Postmark
  - SMS: Twilio, AWS SNS, Vonage
  - Push: Firebase FCM, AWS SNS
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

from connectors.base import BaseConnector
from core.models import ActionResult, ActionType, Case, Intervention

# Quiet hours: no outbound messaging between 21:00 and 08:00 UTC
_QUIET_START_HOUR = 21
_QUIET_END_HOUR = 8


def _is_quiet_hours(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    h = now.hour
    return h >= _QUIET_START_HOUR or h < _QUIET_END_HOUR


class EmailSMSConnector(BaseConnector):
    """
    Stub connector for email, SMS, and push notifications.
    Checks consent flags and quiet hours before sending.
    """

    @property
    def name(self) -> str:
        return "email_sms_stub"

    def execute(self, case: Case, intervention: Intervention) -> ActionResult:
        now = datetime.utcnow()
        action = (
            ActionType(intervention.action_type)
            if isinstance(intervention.action_type, str)
            else intervention.action_type
        )

        # --- Consent check ---
        channel_map = {
            ActionType.SEND_EMAIL: "email",
            ActionType.SEND_SMS: "sms",
            ActionType.SEND_PUSH: "push",
            ActionType.SEND_DUNNING_MESSAGE: "email",
            ActionType.SEND_FRIENDLY_REMINDER: "email",
            ActionType.SEND_FORMAL_NOTICE: "email",
            ActionType.SEND_GRACE_PERIOD_NOTICE: "email",
            ActionType.SEND_PTP_REMINDER: "email",
        }
        channel = channel_map.get(action, intervention.channel or "email")
        opt_out_key = f"opt_out_{channel}"
        if case.consent_flags.get(opt_out_key) or case.consent_flags.get("opt_out_all"):
            return ActionResult(
                success=False,
                connector=self.name,
                response_payload={
                    "blocked_reason": f"opt_out flag set for channel: {channel}",
                    "channel": channel,
                },
                executed_at=now,
                is_stub=True,
                error_message=f"Blocked: customer has opted out of {channel}",
            )

        # --- Quiet hours check ---
        if channel in ("sms", "push") and _is_quiet_hours(now.replace(tzinfo=timezone.utc)):
            return ActionResult(
                success=False,
                connector=self.name,
                response_payload={
                    "blocked_reason": "quiet_hours",
                    "channel": channel,
                    "retry_after": "08:00 UTC",
                },
                executed_at=now,
                is_stub=True,
                error_message="Blocked: quiet hours (21:00–08:00 UTC) — will retry at 08:00",
            )

        # --- Simulate send ---
        delivery_success = random.random() < 0.94  # 94% delivery rate
        template = intervention.parameters.get("template", f"{self.workflow_name(case)}_{channel}")
        recipient = intervention.parameters.get("to", "customer@example.com")

        payload: dict = {
            "action": action.value if hasattr(action, "value") else str(action),
            "channel": channel,
            "recipient": recipient,
            "template": template,
            "attempt_num": intervention.parameters.get("attempt_num", 1),
            "simulated_outcome": "delivered" if delivery_success else "bounce",
            "message_id": f"msg_{case.case_id[:8]}_{intervention.parameters.get('attempt_num', 1)}",
            "note": f"Stub: real call would go to {self._provider_for(channel)} API",
        }

        return ActionResult(
            success=delivery_success,
            connector=self.name,
            response_payload=payload,
            executed_at=now,
            is_stub=True,
            error_message=None if delivery_success else "Message bounced (simulated)",
        )

    @staticmethod
    def workflow_name(case: Case) -> str:
        w = case.workflow
        return w.value if hasattr(w, "value") else str(w)

    @staticmethod
    def _provider_for(channel: str) -> str:
        return {"email": "SendGrid", "sms": "Twilio", "push": "Firebase FCM"}.get(channel, "unknown")
