"""
core/diagnosis/rules_engine.py — Pure-rules diagnosis engine (default).

All diagnosis logic is deterministic, version-tagged, and requires no LLM.
Each workflow has its own rule set. Confidence is computed from signal strength.
"""

from __future__ import annotations

from core.diagnosis.base import DiagnosisEngine
from core.models import (
    ActionType,
    Case,
    DeclineCategory,
    RootCause,
    WorkflowType,
)


_VERSION = "rules-v1.0"

# Soft decline codes — retriable
SOFT_DECLINE_CODES = {
    "51", "61", "65",               # Insufficient funds / exceeds limit
    "91", "96",                     # Issuer unavailable / system error
    "N7",                           # CVV2 mismatch (sometimes retriable)
    "R01", "R09",                   # ACH insufficient funds, uncollected funds
    "INSUFFICIENT_FUNDS",
    "ISSUER_UNAVAILABLE",
    "DO_NOT_HONOR",                 # Can be soft in some contexts
}

# Hard decline codes — do not retry
HARD_DECLINE_CODES = {
    "04", "07", "41", "43",         # Pick up card, fraud
    "14",                           # Invalid card number
    "54",                           # Expired card
    "57", "62",                     # Transaction not permitted, restricted card
    "R02", "R03", "R04",            # ACH: stop payment, no account, invalid account
    "LOST_STOLEN",
    "EXPIRED_CARD",
    "FRAUD_FLAGGED",
    "HARD_DECLINE",
}

# 3DS failure codes
THREE_DS_CODES = {"3DS_FAIL", "3DS_TIMEOUT", "3DS_ABANDONED", "N3"}

# Issuer outage signals
ISSUER_OUTAGE_SIGNALS = {"ISSUER_UNAVAILABLE", "91", "96"}


class RulesEngine(DiagnosisEngine):
    """
    Deterministic rule-based diagnosis engine.
    No network calls, no LLM — runs fully offline.
    """

    @property
    def model_version(self) -> str:
        return _VERSION

    def diagnose(self, case: Case) -> RootCause:
        workflow = (
            WorkflowType(case.workflow)
            if isinstance(case.workflow, str)
            else case.workflow
        )
        dispatch = {
            WorkflowType.PAYMENT_DEGRADATION: self._diagnose_payment_degradation,
            WorkflowType.CHECKOUT_ABANDONMENT: self._diagnose_checkout_abandonment,
            WorkflowType.SUBSCRIPTION_RECOVERY: self._diagnose_subscription,
            WorkflowType.B2B_RECEIVABLES: self._diagnose_b2b,
            WorkflowType.MANDATE_RETRY: self._diagnose_mandate,
            WorkflowType.HINGLISH_VOICE: self._diagnose_voice,
            WorkflowType.PROMISE_TO_PAY: self._diagnose_ptp,
        }
        fn = dispatch.get(workflow, self._diagnose_unknown)
        return fn(case)

    # ------------------------------------------------------------------
    # W1: Payment degradation
    # ------------------------------------------------------------------

    def _diagnose_payment_degradation(self, case: Case) -> RootCause:
        payload = case.signal.payload
        decline_codes: dict = payload.get("decline_codes", {})
        decline_rate = payload.get("decline_rate_current", 0.0)
        baseline = payload.get("decline_rate_baseline", 0.04)
        processor = payload.get("processor", "unknown")
        spike_ratio = (decline_rate / baseline) if baseline > 0 else 1.0

        # Check for issuer outage pattern
        outage_votes = sum(
            count for code, count in decline_codes.items()
            if code in ISSUER_OUTAGE_SIGNALS
        )
        total_declines = sum(decline_codes.values()) or 1

        if outage_votes / total_declines > 0.5:
            return RootCause(
                cause_code="ISSUER_OUTAGE",
                decline_category=DeclineCategory.ISSUER_UNAVAILABLE,
                description=(
                    f"Majority of declines ({outage_votes}/{total_declines}) "
                    "are issuer-unavailable codes, suggesting a transient outage."
                ),
                confidence=min(0.95, 0.6 + (outage_votes / total_declines) * 0.35),
                evidence=[
                    f"Decline rate: {decline_rate:.1%} vs baseline {baseline:.1%}",
                    f"Outage codes: {outage_votes}/{total_declines} declines",
                ],
                is_retriable=True,
                recommended_action_types=[
                    ActionType.RETRY_WITH_BACKOFF,
                    ActionType.ROUTE_ALTERNATE_PROCESSOR,
                ],
                model_version=_VERSION,
            )

        # Check for 3DS friction
        three_ds_votes = sum(
            count for code, count in decline_codes.items()
            if code in THREE_DS_CODES
        )
        if three_ds_votes / total_declines > 0.3:
            return RootCause(
                cause_code="3DS_FRICTION",
                decline_category=DeclineCategory.THREE_DS_FAILURE,
                description="High proportion of 3DS failures — possible friction or challenge failure.",
                confidence=0.8,
                evidence=[f"3DS codes: {three_ds_votes}/{total_declines}"],
                is_retriable=True,
                recommended_action_types=[
                    ActionType.ROUTE_ALTERNATE_PROCESSOR,
                    ActionType.RETRY_WITH_BACKOFF,
                ],
                model_version=_VERSION,
            )

        # Check for expired card bulk
        expired_votes = sum(
            count for code, count in decline_codes.items()
            if code in {"54", "EXPIRED_CARD"}
        )
        if expired_votes / total_declines > 0.4:
            return RootCause(
                cause_code="BULK_CARD_EXPIRY",
                decline_category=DeclineCategory.EXPIRED_CARD,
                description="Large fraction of declines are expired-card codes — card refresh needed.",
                confidence=0.88,
                evidence=[f"Expired codes: {expired_votes}/{total_declines}"],
                is_retriable=False,
                recommended_action_types=[ActionType.TRIGGER_CARD_UPDATER],
                model_version=_VERSION,
            )

        # Processor routing fault (spike without clear code pattern)
        if spike_ratio > 3.0:
            return RootCause(
                cause_code="PROCESSOR_ROUTING_FAULT",
                decline_category=DeclineCategory.NETWORK,
                description=(
                    f"Decline rate spiked {spike_ratio:.1f}x above baseline on processor "
                    f"'{processor}' without a single dominant failure code."
                ),
                confidence=0.65,
                evidence=[f"Spike ratio: {spike_ratio:.1f}x", f"Processor: {processor}"],
                is_retriable=True,
                recommended_action_types=[
                    ActionType.ROUTE_ALTERNATE_PROCESSOR,
                    ActionType.RETRY_WITH_BACKOFF,
                ],
                model_version=_VERSION,
            )

        return self._diagnose_unknown(case)

    # ------------------------------------------------------------------
    # W2: Checkout abandonment
    # ------------------------------------------------------------------

    def _diagnose_checkout_abandonment(self, case: Case) -> RootCause:
        payload = case.signal.payload
        cart_value = payload.get("cart_value", 0.0)
        minutes_since = payload.get("time_since_abandonment_minutes", 0)
        prior_purchases = payload.get("prior_purchase_count", 0)
        items_available = payload.get("all_items_available", True)

        if not items_available:
            return RootCause(
                cause_code="ITEMS_UNAVAILABLE",
                description="One or more cart items are now out of stock.",
                confidence=0.95,
                evidence=["items_available=False"],
                is_retriable=False,
                recommended_action_types=[ActionType.SEND_EMAIL],
                model_version=_VERSION,
            )

        # High-value cart from known buyer — high recoverability
        if cart_value > 200 and prior_purchases > 2 and minutes_since < 60:
            return RootCause(
                cause_code="HIGH_VALUE_ABANDONMENT",
                description="High-value cart from repeat buyer abandoned recently — strong recovery candidate.",
                confidence=0.85,
                evidence=[
                    f"Cart value: ${cart_value:.2f}",
                    f"Prior purchases: {prior_purchases}",
                    f"Minutes since: {minutes_since}",
                ],
                is_retriable=True,
                recommended_action_types=[
                    ActionType.SEND_EMAIL,
                    ActionType.SEND_SMS,
                ],
                model_version=_VERSION,
            )

        if minutes_since > 1440:  # >24 hours — cart is cold
            return RootCause(
                cause_code="COLD_CART",
                description="Cart was abandoned over 24 hours ago — low recovery probability.",
                confidence=0.7,
                evidence=[f"Minutes since: {minutes_since}"],
                is_retriable=True,
                recommended_action_types=[ActionType.SEND_EMAIL],
                model_version=_VERSION,
            )

        return RootCause(
            cause_code="STANDARD_ABANDONMENT",
            description="Standard checkout abandonment — trigger recovery sequence.",
            confidence=0.75,
            evidence=[f"Cart: ${cart_value:.2f}, {minutes_since}min ago"],
            is_retriable=True,
            recommended_action_types=[ActionType.SEND_EMAIL, ActionType.SEND_PUSH],
            model_version=_VERSION,
        )

    # ------------------------------------------------------------------
    # W3: Subscription recovery
    # ------------------------------------------------------------------

    def _diagnose_subscription(self, case: Case) -> RootCause:
        payload = case.signal.payload
        decline_code = payload.get("decline_code", "UNKNOWN").upper()
        is_first = payload.get("is_first_failure", True)
        card_expiry = payload.get("card_expiry", "")

        # Expired card — card updater first
        if decline_code in {"54", "EXPIRED_CARD"} or _card_is_expired(card_expiry):
            return RootCause(
                cause_code="EXPIRED_CARD",
                decline_category=DeclineCategory.EXPIRED_CARD,
                description="Card is expired — trigger Account Updater before retry.",
                confidence=0.93,
                evidence=[f"Decline code: {decline_code}", f"Expiry: {card_expiry}"],
                is_retriable=True,
                recommended_action_types=[
                    ActionType.TRIGGER_CARD_UPDATER,
                    ActionType.SEND_DUNNING_MESSAGE,
                ],
                model_version=_VERSION,
            )

        # Insufficient funds — retry on payday
        if decline_code in {"51", "61", "INSUFFICIENT_FUNDS", "R01"}:
            return RootCause(
                cause_code="INSUFFICIENT_FUNDS",
                decline_category=DeclineCategory.INSUFFICIENT_FUNDS,
                description="Insufficient funds — schedule retry around estimated payday.",
                confidence=0.88,
                evidence=[f"Decline code: {decline_code}"],
                is_retriable=True,
                recommended_action_types=[
                    ActionType.RETRY_WITH_BACKOFF,
                    ActionType.SEND_DUNNING_MESSAGE,
                ],
                model_version=_VERSION,
            )

        # Do not honor — may recover with dunning
        if decline_code in {"05", "DO_NOT_HONOR"}:
            return RootCause(
                cause_code="DO_NOT_HONOR",
                decline_category=DeclineCategory.DO_NOT_HONOR,
                description="Do-not-honor response — send dunning message with grace period.",
                confidence=0.72,
                evidence=[f"Decline code: {decline_code}"],
                is_retriable=True,
                recommended_action_types=[
                    ActionType.SEND_DUNNING_MESSAGE,
                    ActionType.RETRY_WITH_BACKOFF,
                ],
                model_version=_VERSION,
            )

        # Hard decline — do not retry, prompt update
        if decline_code in HARD_DECLINE_CODES:
            return RootCause(
                cause_code="HARD_DECLINE",
                decline_category=DeclineCategory.HARD,
                description="Hard decline — do not retry; prompt customer to update payment method.",
                confidence=0.9,
                evidence=[f"Decline code: {decline_code} (hard)"],
                is_retriable=False,
                recommended_action_types=[ActionType.SEND_DUNNING_MESSAGE],
                model_version=_VERSION,
            )

        return RootCause(
            cause_code="SOFT_DECLINE_GENERIC",
            decline_category=DeclineCategory.SOFT,
            description="Generic soft decline — start standard dunning sequence.",
            confidence=0.65,
            evidence=[f"Decline code: {decline_code}"],
            is_retriable=True,
            recommended_action_types=[
                ActionType.RETRY_WITH_BACKOFF,
                ActionType.SEND_DUNNING_MESSAGE,
            ],
            model_version=_VERSION,
        )

    # ------------------------------------------------------------------
    # W4: B2B receivables
    # ------------------------------------------------------------------

    def _diagnose_b2b(self, case: Case) -> RootCause:
        payload = case.signal.payload
        days_overdue = payload.get("days_overdue", 0)
        has_dispute = payload.get("has_active_dispute", False)
        history_score = payload.get("payment_history_score", 0.5)
        account_tier = payload.get("account_tier", "smb")

        if has_dispute:
            return RootCause(
                cause_code="ACTIVE_DISPUTE",
                description="Invoice has an active dispute — escalation blocked until resolved.",
                confidence=0.99,
                evidence=["has_active_dispute=True"],
                is_retriable=False,
                recommended_action_types=[ActionType.HUMAN_REVIEW],
                model_version=_VERSION,
            )

        if days_overdue <= 7:
            return RootCause(
                cause_code="EARLY_OVERDUE",
                description="Invoice is early overdue — likely oversight; send friendly reminder.",
                confidence=0.85,
                evidence=[f"Days overdue: {days_overdue}"],
                is_retriable=True,
                recommended_action_types=[ActionType.SEND_FRIENDLY_REMINDER],
                model_version=_VERSION,
            )

        if days_overdue <= 30:
            return RootCause(
                cause_code="MID_OVERDUE",
                description="Invoice is 8–30 days overdue — escalate to formal notice.",
                confidence=0.82,
                evidence=[f"Days overdue: {days_overdue}", f"History score: {history_score:.2f}"],
                is_retriable=True,
                recommended_action_types=[
                    ActionType.SEND_FORMAL_NOTICE,
                    ActionType.SEND_FRIENDLY_REMINDER,
                ],
                model_version=_VERSION,
            )

        if days_overdue <= 60:
            return RootCause(
                cause_code="LATE_OVERDUE",
                description="Invoice is 31–60 days overdue — escalate to AR team.",
                confidence=0.88,
                evidence=[
                    f"Days overdue: {days_overdue}",
                    f"Tier: {account_tier}",
                    f"History score: {history_score:.2f}",
                ],
                is_retriable=True,
                recommended_action_types=[
                    ActionType.ESCALATE_TO_AR,
                    ActionType.SEND_FORMAL_NOTICE,
                ],
                model_version=_VERSION,
            )

        # >60 days — collections referral (requires approval)
        return RootCause(
            cause_code="CRITICALLY_OVERDUE",
            description="Invoice >60 days overdue — escalate to collections (requires human approval).",
            confidence=0.92,
            evidence=[f"Days overdue: {days_overdue}"],
            is_retriable=True,
            recommended_action_types=[
                ActionType.ESCALATE_TO_COLLECTIONS,
                ActionType.ESCALATE_TO_AR,
            ],
            model_version=_VERSION,
        )

    # ------------------------------------------------------------------
    # W5: Mandate retry
    # ------------------------------------------------------------------

    def _diagnose_mandate(self, case: Case) -> RootCause:
        payload = case.signal.payload
        failure_code = payload.get("failure_code", "UNKNOWN").upper()
        is_mandate_active = payload.get("is_mandate_active", True)
        mandate_type = payload.get("mandate_type", "unknown")
        prior_retries = payload.get("prior_retry_count", 0)
        max_retries = payload.get("max_retries_allowed", 3)

        if not is_mandate_active:
            return RootCause(
                cause_code="MANDATE_EXPIRED",
                decline_category=DeclineCategory.MANDATE_EXPIRED,
                description="Mandate is no longer active — request re-authorization.",
                confidence=0.97,
                evidence=["is_mandate_active=False"],
                is_retriable=False,
                recommended_action_types=[ActionType.REQUEST_MANDATE_REAUTHORIZATION],
                model_version=_VERSION,
            )

        if prior_retries >= max_retries:
            return RootCause(
                cause_code="MAX_RETRIES_NETWORK",
                description=f"Network-mandated retry limit reached ({prior_retries}/{max_retries}).",
                confidence=0.99,
                evidence=[f"Retries: {prior_retries}/{max_retries}"],
                is_retriable=False,
                recommended_action_types=[ActionType.REQUEST_MANDATE_REAUTHORIZATION],
                model_version=_VERSION,
            )

        if failure_code in {"INSUFFICIENT_FUNDS", "R01", "INSUFF_FUNDS"}:
            return RootCause(
                cause_code="INSUFFICIENT_BALANCE",
                decline_category=DeclineCategory.INSUFFICIENT_FUNDS,
                description="Insufficient balance — notify customer and retry after payday.",
                confidence=0.88,
                evidence=[f"Code: {failure_code}"],
                is_retriable=True,
                recommended_action_types=[
                    ActionType.SEND_SMS,
                    ActionType.RETRY_MANDATE_DEBIT,
                ],
                model_version=_VERSION,
            )

        if failure_code in {"BANK_DOWNTIME", "91", "96", "ISSUER_UNAVAILABLE"}:
            return RootCause(
                cause_code="BANK_DOWNTIME",
                decline_category=DeclineCategory.ISSUER_UNAVAILABLE,
                description="Bank/network downtime — retry after next available window.",
                confidence=0.85,
                evidence=[f"Code: {failure_code}"],
                is_retriable=True,
                recommended_action_types=[ActionType.RETRY_MANDATE_DEBIT],
                model_version=_VERSION,
            )

        return RootCause(
            cause_code="MANDATE_FAILURE_GENERIC",
            description="Generic mandate failure — retry within permitted window.",
            confidence=0.65,
            evidence=[f"Code: {failure_code}, type: {mandate_type}"],
            is_retriable=True,
            recommended_action_types=[ActionType.RETRY_MANDATE_DEBIT],
            model_version=_VERSION,
        )

    # ------------------------------------------------------------------
    # W6: Hinglish voice
    # ------------------------------------------------------------------

    def _diagnose_voice(self, case: Case) -> RootCause:
        payload = case.signal.payload
        outstanding = payload.get("outstanding_amount", 0.0)
        last_contact = payload.get("last_contact_date")
        prev_promise = payload.get("previous_promise_date")

        if prev_promise:
            return RootCause(
                cause_code="BROKEN_PROMISE_VOICE",
                description="Customer made a prior promise-to-pay that was not fulfilled.",
                confidence=0.9,
                evidence=["previous_promise_date is set"],
                is_retriable=True,
                recommended_action_types=[ActionType.LOG_HINGLISH_SCRIPT, ActionType.INITIATE_IVR_SCRIPT],
                model_version=_VERSION,
            )

        if outstanding > 5000:
            return RootCause(
                cause_code="HIGH_VALUE_VOICE_RECOVERY",
                description="High outstanding amount — prioritize voice outreach.",
                confidence=0.8,
                evidence=[f"Outstanding: {outstanding}"],
                is_retriable=True,
                recommended_action_types=[ActionType.INITIATE_IVR_SCRIPT, ActionType.LOG_HINGLISH_SCRIPT],
                model_version=_VERSION,
            )

        return RootCause(
            cause_code="STANDARD_VOICE_RECOVERY",
            description="Standard voice/IVR recovery — generate and log Hinglish script.",
            confidence=0.75,
            evidence=[f"Outstanding: {outstanding}"],
            is_retriable=True,
            recommended_action_types=[ActionType.LOG_HINGLISH_SCRIPT],
            model_version=_VERSION,
        )

    # ------------------------------------------------------------------
    # W7: Promise to pay
    # ------------------------------------------------------------------

    def _diagnose_ptp(self, case: Case) -> RootCause:
        payload = case.signal.payload
        is_broken = payload.get("is_broken", False)
        days_until_due = payload.get("days_until_due", 0)
        captured_from = payload.get("captured_from", "unknown")

        if is_broken:
            return RootCause(
                cause_code="BROKEN_PROMISE",
                description="Promise-to-pay date has passed without payment — escalate.",
                confidence=0.97,
                evidence=[f"is_broken=True, captured from: {captured_from}"],
                is_retriable=True,
                recommended_action_types=[ActionType.ESCALATE_BROKEN_PTP],
                model_version=_VERSION,
            )

        if 0 < days_until_due <= 2:
            return RootCause(
                cause_code="PTP_DUE_SOON",
                description="Promise-to-pay is due within 2 days — send reminder.",
                confidence=0.9,
                evidence=[f"Days until due: {days_until_due}"],
                is_retriable=True,
                recommended_action_types=[ActionType.SEND_PTP_REMINDER],
                model_version=_VERSION,
            )

        return RootCause(
            cause_code="PTP_ACTIVE",
            description="Active promise-to-pay — logged and being monitored.",
            confidence=0.85,
            evidence=[f"Days until due: {days_until_due}"],
            is_retriable=True,
            recommended_action_types=[ActionType.LOG_PROMISE],
            model_version=_VERSION,
        )

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _diagnose_unknown(self, case: Case) -> RootCause:
        return RootCause(
            cause_code="UNKNOWN",
            description="Could not classify root cause with available signal data.",
            confidence=0.0,
            evidence=[f"workflow={case.workflow}"],
            is_retriable=False,
            recommended_action_types=[ActionType.HUMAN_REVIEW],
            model_version=_VERSION,
        )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _card_is_expired(expiry: str) -> bool:
    """Return True if MM/YY expiry string represents a past date."""
    if not expiry or "/" not in expiry:
        return False
    try:
        from datetime import date
        mm, yy = expiry.split("/")
        month, year = int(mm), 2000 + int(yy)
        today = date.today()
        return (year, month) < (today.year, today.month)
    except (ValueError, TypeError):
        return False
