"""
core/stopping_rules.py — Stopping rule evaluator.

NON-NEGOTIABLE GUARDRAILS:
  - Every workflow has a max-attempts cap.
  - Every workflow has a cooldown period.
  - Hard stops fire immediately on: payment_success, opt_out, dispute,
    account_closed, voluntary_cancel.
  - A stopped case is NEVER re-activated by this engine.
  - The evaluator runs BEFORE every Act step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from core.models import Case, CaseStatus, WorkflowType


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class StoppingRuleConfig:
    """Per-workflow stopping rule parameters."""

    workflow: WorkflowType
    max_attempts: int                       # Hard cap on total Act steps
    cooldown_hours: float                   # Min hours between attempts
    max_case_age_days: int = 90             # Auto-close if case older than this
    hard_stop_flags: list[str] = field(default_factory=list)  # metadata flags that kill the case


# Default configs per workflow
DEFAULT_STOPPING_CONFIGS: dict[WorkflowType, StoppingRuleConfig] = {
    WorkflowType.PAYMENT_DEGRADATION: StoppingRuleConfig(
        workflow=WorkflowType.PAYMENT_DEGRADATION,
        max_attempts=3,
        cooldown_hours=1,
        hard_stop_flags=["payment_success", "opt_out", "dispute", "account_closed"],
    ),
    WorkflowType.CHECKOUT_ABANDONMENT: StoppingRuleConfig(
        workflow=WorkflowType.CHECKOUT_ABANDONMENT,
        max_attempts=3,
        cooldown_hours=6,
        max_case_age_days=3,            # Carts go cold fast
        hard_stop_flags=["payment_success", "opt_out", "items_unavailable"],
    ),
    WorkflowType.SUBSCRIPTION_RECOVERY: StoppingRuleConfig(
        workflow=WorkflowType.SUBSCRIPTION_RECOVERY,
        max_attempts=5,
        cooldown_hours=48,
        max_case_age_days=30,
        hard_stop_flags=["payment_success", "voluntary_cancel", "opt_out", "dispute"],
    ),
    WorkflowType.B2B_RECEIVABLES: StoppingRuleConfig(
        workflow=WorkflowType.B2B_RECEIVABLES,
        max_attempts=4,
        cooldown_hours=72,
        max_case_age_days=90,
        hard_stop_flags=["payment_received", "dispute_filed", "account_closed", "opted_out"],
    ),
    WorkflowType.MANDATE_RETRY: StoppingRuleConfig(
        workflow=WorkflowType.MANDATE_RETRY,
        max_attempts=3,
        cooldown_hours=24,
        max_case_age_days=30,
        hard_stop_flags=["payment_success", "mandate_cancelled", "opt_out"],
    ),
    WorkflowType.HINGLISH_VOICE: StoppingRuleConfig(
        workflow=WorkflowType.HINGLISH_VOICE,
        max_attempts=3,
        cooldown_hours=48,
        max_case_age_days=14,
        hard_stop_flags=["payment_received", "do_not_call", "opt_out"],
    ),
    WorkflowType.PROMISE_TO_PAY: StoppingRuleConfig(
        workflow=WorkflowType.PROMISE_TO_PAY,
        max_attempts=5,
        cooldown_hours=24,
        max_case_age_days=60,
        hard_stop_flags=["payment_received", "dispute_filed", "opt_out"],
    ),
}


# ---------------------------------------------------------------------------
# Evaluation result
# ---------------------------------------------------------------------------


@dataclass
class StopEvaluation:
    should_stop: bool
    new_status: Optional[CaseStatus] = None
    reason: str = ""
    stop_type: str = ""     # "hard_stop" | "cap_reached" | "cooldown" | "age_limit" | "terminal"


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class StoppingRuleEvaluator:
    """
    Evaluates stopping rules for a case before every Act step.

    Hierarchy (first match wins):
      1. Already in terminal state → stop immediately.
      2. Hard-stop flag in case metadata → stop immediately.
      3. Max-attempts cap reached → CAPPED.
      4. Case age limit exceeded → CLOSED.
      5. Cooldown not elapsed → stop (delay, not close).
    """

    def __init__(self, config: Optional[StoppingRuleConfig] = None) -> None:
        self._configs = DEFAULT_STOPPING_CONFIGS
        self._override = config

    def get_config(self, workflow: WorkflowType) -> StoppingRuleConfig:
        if self._override and self._override.workflow == workflow:
            return self._override
        return self._configs.get(workflow, StoppingRuleConfig(
            workflow=workflow, max_attempts=5, cooldown_hours=24
        ))

    def evaluate(self, case: Case, now: Optional[datetime] = None) -> StopEvaluation:
        """
        Run all stopping rules against the case.
        Returns immediately on first triggered rule.
        """
        now = now or datetime.utcnow()
        cfg = self.get_config(
            WorkflowType(case.workflow) if isinstance(case.workflow, str) else case.workflow
        )

        # Rule 1: Already terminal — never re-evaluate
        if case.is_terminal:
            return StopEvaluation(
                should_stop=True,
                new_status=CaseStatus(case.status) if isinstance(case.status, str) else case.status,
                reason="Case is already in a terminal state",
                stop_type="terminal",
            )

        # Rule 2: Hard-stop flags in metadata
        for flag in cfg.hard_stop_flags:
            if case.metadata.get(flag) is True:
                status_map = {
                    "payment_success": CaseStatus.RECOVERED,
                    "payment_received": CaseStatus.RECOVERED,
                    "opt_out": CaseStatus.OPTED_OUT,
                    "do_not_call": CaseStatus.OPTED_OUT,
                    "dispute": CaseStatus.DISPUTED,
                    "dispute_filed": CaseStatus.DISPUTED,
                    "account_closed": CaseStatus.CLOSED,
                    "voluntary_cancel": CaseStatus.CLOSED,
                    "mandate_cancelled": CaseStatus.CLOSED,
                    "opted_out": CaseStatus.OPTED_OUT,
                    "items_unavailable": CaseStatus.CLOSED,
                }
                new_status = status_map.get(flag, CaseStatus.CLOSED)
                return StopEvaluation(
                    should_stop=True,
                    new_status=new_status,
                    reason=f"Hard stop: flag '{flag}' is set on case",
                    stop_type="hard_stop",
                )

        # Rule 3: Max attempts cap
        if case.attempt_count >= cfg.max_attempts:
            return StopEvaluation(
                should_stop=True,
                new_status=CaseStatus.CAPPED,
                reason=(
                    f"Max attempts reached: {case.attempt_count}/{cfg.max_attempts}"
                ),
                stop_type="cap_reached",
            )

        # Rule 4: Case age limit
        age_days = (now - case.opened_at).days
        if age_days > cfg.max_case_age_days:
            return StopEvaluation(
                should_stop=True,
                new_status=CaseStatus.CLOSED,
                reason=f"Case age {age_days}d exceeds limit {cfg.max_case_age_days}d",
                stop_type="age_limit",
            )

        # Rule 5: Cooldown — the case is ACTIVE but not yet time for next attempt
        if case.status == CaseStatus.ACTIVE and cfg.cooldown_hours > 0:
            if case.last_updated_at:
                next_allowed = case.last_updated_at + timedelta(hours=cfg.cooldown_hours)
                if now < next_allowed:
                    return StopEvaluation(
                        should_stop=True,
                        new_status=None,    # Don't change status — just skip this run
                        reason=(
                            f"Cooldown active: next attempt allowed at "
                            f"{next_allowed.isoformat()} (cooldown={cfg.cooldown_hours}h)"
                        ),
                        stop_type="cooldown",
                    )

        return StopEvaluation(should_stop=False)

    def check_hard_stop_only(self, case: Case) -> StopEvaluation:
        """
        Check ONLY hard-stop flags — used after a payment event arrives
        to immediately close cases that are already recovered.
        """
        if case.is_terminal:
            return StopEvaluation(
                should_stop=True,
                new_status=CaseStatus(case.status) if isinstance(case.status, str) else case.status,
                reason="Already terminal",
                stop_type="terminal",
            )
        cfg = self.get_config(
            WorkflowType(case.workflow) if isinstance(case.workflow, str) else case.workflow
        )
        for flag in cfg.hard_stop_flags:
            if case.metadata.get(flag) is True:
                return StopEvaluation(
                    should_stop=True,
                    new_status=CaseStatus.CLOSED,
                    reason=f"Hard stop flag: {flag}",
                    stop_type="hard_stop",
                )
        return StopEvaluation(should_stop=False)
