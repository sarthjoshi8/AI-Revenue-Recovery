"""
connectors/ivr_voice.py — IVR / voice connector stub.

Does NOT fabricate a live telephony integration.
Instead, it:
  1. Generates a structured Hinglish script from the case context
  2. Logs the script, response-capture fields, and intended outcome
  3. Returns an ActionResult with the full script payload for audit

To wire in a real provider (Twilio Voice, Amazon Connect, Exotel):
  - Replace _initiate_stub_call() with the provider SDK call
  - Pass script_payload as TwiML / Amazon Lex intent
"""

from __future__ import annotations

from datetime import datetime

from connectors.base import BaseConnector
from core.models import ActionResult, ActionType, Case, Intervention


# ---------------------------------------------------------------------------
# Hinglish script templates
# ---------------------------------------------------------------------------

_SCRIPTS: dict[str, str] = {
    "payment_recovery": (
        "Namaste {customer_name} ji! Main {company} ki taraf se bol raha hoon. "
        "Aapka account mein {amount} {currency} ka outstanding balance hai. "
        "Kya aap aaj payment kar sakte hain? "
        "Agar haan, toh '1' dabayein. Agar baad mein karna chahein, toh '2' dabayein. "
        "Kisi bhi help ke liye '0' dabayein. Dhanyavaad!"
    ),
    "promise_reminder": (
        "Namaste {customer_name} ji! Yeh ek yaad dilane wali call hai. "
        "Aapne {promise_date} ko {amount} {currency} dene ka vaada kiya tha. "
        "Kya aap aaj payment complete kar sakte hain? "
        "Payment karne ke liye '1' dabayein. "
        "Help ke liye '0' dabayein. Shukriya!"
    ),
    "subscription_dunning": (
        "Hello {customer_name}! Aapki subscription ka renewal pending hai. "
        "Amount hai {amount} {currency}. "
        "Abhi payment karne ke liye '1' dabayein. "
        "Apna payment method update karne ke liye '2' dabayein. "
        "Hamare agent se baat karne ke liye '0' dabayein."
    ),
    "default": (
        "Namaste {customer_name} ji! Ek zaroori update hai aapke account ke baare mein. "
        "Kripya '1' dabayein agar aap abhi baat karna chahte hain, "
        "ya '2' dabayein callback ke liye. Dhanyavaad."
    ),
}

_RESPONSE_CAPTURE_FIELDS = {
    "dtmf_input": "Customer DTMF key press (1=pay now, 2=later/callback, 0=agent)",
    "call_duration_seconds": "Total call duration",
    "call_outcome": "answered | no_answer | busy | failed",
    "promised_payment_date": "If customer promises a date (captured via IVR prompt)",
    "agent_transfer": "True if transferred to live agent",
    "recording_url": "URL of call recording (if recording enabled)",
}


class IVRVoiceConnector(BaseConnector):
    """
    IVR/Voice connector stub for Hinglish outreach.
    Generates and logs scripts without making real telephony calls.
    """

    @property
    def name(self) -> str:
        return "ivr_voice_stub"

    def execute(self, case: Case, intervention: Intervention) -> ActionResult:
        now = datetime.utcnow()
        action = (
            ActionType(intervention.action_type)
            if isinstance(intervention.action_type, str)
            else intervention.action_type
        )

        script = self._generate_script(case, intervention)
        payload = {
            "action": action.value if hasattr(action, "value") else str(action),
            "channel": "ivr_voice",
            "script": script,
            "response_capture_fields": _RESPONSE_CAPTURE_FIELDS,
            "customer_name": intervention.parameters.get("customer_name", "Customer"),
            "language": intervention.parameters.get("language", "hi-en"),
            "outstanding_amount": intervention.parameters.get("outstanding", case.revenue_at_risk),
            "note": (
                "STUB: Script generated and logged. "
                "No real call made. Wire in Twilio/Exotel/Amazon Connect to activate."
            ),
            "simulated_outcome": "script_logged",
            "call_id": f"ivr_{case.case_id[:10]}_{now.strftime('%Y%m%d%H%M%S')}",
        }

        return ActionResult(
            success=True,
            connector=self.name,
            response_payload=payload,
            executed_at=now,
            is_stub=True,
        )

    def _generate_script(self, case: Case, intervention: Intervention) -> str:
        params = intervention.parameters
        customer_name = params.get("customer_name", "Customer")
        amount = params.get("outstanding", case.revenue_at_risk)
        currency = case.signal.payload.get("currency", "INR")
        promise_date = case.signal.payload.get("promise_date", "jald se jald")
        company = "Company"

        workflow_str = case.workflow.value if hasattr(case.workflow, "value") else str(case.workflow)

        template_key = "default"
        if "promise" in workflow_str.lower():
            template_key = "promise_reminder"
        elif "subscription" in workflow_str.lower():
            template_key = "subscription_dunning"
        elif "voice" in workflow_str.lower() or "mandate" in workflow_str.lower():
            template_key = "payment_recovery"

        template = _SCRIPTS.get(template_key, _SCRIPTS["default"])
        return template.format(
            customer_name=customer_name,
            amount=f"{amount:,.0f}",
            currency=currency,
            company=company,
            promise_date=promise_date,
        )
