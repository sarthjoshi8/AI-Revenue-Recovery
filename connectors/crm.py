"""
connectors/crm.py — CRM connector stub.

Reads and writes account/contact flags used by the pipeline:
  - consent_flags (do-not-contact, opt-outs)
  - account tier and payment history
  - escalation state

To wire in a real CRM (Salesforce, HubSpot, Zoho):
  - Implement _fetch_account() with a real API call
  - The returned dict shape must remain the same
"""

from __future__ import annotations

import random
from datetime import datetime

from connectors.base import BaseConnector
from core.models import ActionResult, Case, Intervention


class CRMConnector(BaseConnector):
    """Stub CRM connector. Returns synthetic account data."""

    @property
    def name(self) -> str:
        return "crm_stub"

    def execute(self, case: Case, intervention: Intervention) -> ActionResult:
        """
        CRM actions are internal — this connector is used for escalation logging.
        """
        now = datetime.utcnow()
        action = intervention.action_type
        action_str = action.value if hasattr(action, "value") else str(action)

        if "escalate" in action_str:
            return self._log_escalation(case, intervention, now)
        return self._log_note(case, intervention, now)

    def _log_escalation(self, case: Case, intervention: Intervention, now: datetime) -> ActionResult:
        return ActionResult(
            success=True,
            connector=self.name,
            response_payload={
                "action": "escalation_logged",
                "case_id": case.case_id,
                "account_id": case.account_id,
                "action_type": intervention.action_type if isinstance(intervention.action_type, str) else intervention.action_type.value,
                "crm_ticket_id": f"CRM-{case.case_id[:8].upper()}",
                "assigned_to": "ar-team@company.com",
                "note": "Stub: real CRM API call would create/update a case record",
                "escalated_at": now.isoformat(),
            },
            executed_at=now,
            is_stub=True,
        )

    def _log_note(self, case: Case, intervention: Intervention, now: datetime) -> ActionResult:
        return ActionResult(
            success=True,
            connector=self.name,
            response_payload={
                "action": "note_logged",
                "case_id": case.case_id,
                "note": f"Recovery action taken: {intervention.action_type}",
            },
            executed_at=now,
            is_stub=True,
        )

    @staticmethod
    def fetch_account_flags(account_id: str) -> dict:
        """
        Stub: returns synthetic consent and account metadata.
        In production, replace with real CRM API call.
        """
        seed = sum(ord(c) for c in account_id)
        rng = random.Random(seed)
        return {
            "account_id": account_id,
            "opt_out_email": rng.random() < 0.05,
            "opt_out_sms": rng.random() < 0.08,
            "opt_out_push": rng.random() < 0.03,
            "opt_out_all": rng.random() < 0.02,
            "do_not_call": rng.random() < 0.04,
            "account_tier": rng.choice(["enterprise", "mid-market", "smb"]),
            "payment_history_score": round(rng.uniform(0.2, 1.0), 2),
            "has_active_dispute": rng.random() < 0.07,
            "account_closed": False,
            "fetched_at": datetime.utcnow().isoformat(),
            "source": "crm_stub",
        }
