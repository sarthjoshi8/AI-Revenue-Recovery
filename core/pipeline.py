"""
core/pipeline.py — The 6-stage pipeline engine (workflow-agnostic).

Every workflow runs through:
  DETECT → DIAGNOSE → DECIDE → ACT → MEASURE → AUDIT

Guardrails enforced here:
  • Stopping rules checked BEFORE every Act step
  • Human approval gate checked BEFORE any escalation action
  • Revenue attribution ONLY on matched payment event (never on intent)
  • Every stage writes an immutable AuditEntry
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Callable, Optional

from core.action_catalog import ActionCatalog
from core.attribution import AttributionEngine
from core.audit import AuditStore
from core.diagnosis.base import DiagnosisEngine
from core.models import (
    ActionResult,
    ActionType,
    AuditEntry,
    Case,
    CaseGroup,
    CaseStatus,
    Intervention,
    PipelineStage,
    RootCause,
    WorkflowType,
)
from core.stopping_rules import StoppingRuleEvaluator


# ---------------------------------------------------------------------------
# Connector interface (defined here to avoid circular imports)
# ---------------------------------------------------------------------------

# A connector is any callable that takes (case, intervention) and returns ActionResult
ConnectorFn = Callable[[Case, Intervention], ActionResult]


# ---------------------------------------------------------------------------
# Pipeline engine
# ---------------------------------------------------------------------------


class PipelineEngine:
    """
    Shared, workflow-agnostic pipeline engine.

    Usage:
        engine = PipelineEngine(
            workflow=WorkflowType.SUBSCRIPTION_RECOVERY,
            diagnosis_engine=RulesEngine(),
            audit_store=AuditStore(),
            connector=my_connector_fn,
            holdout_rate=0.15,
        )
        case = engine.run(signal)
    """

    def __init__(
        self,
        workflow: WorkflowType,
        diagnosis_engine: DiagnosisEngine,
        audit_store: AuditStore,
        connector: ConnectorFn,
        operator_id: str = "system",
        holdout_rate: float = 0.15,
    ) -> None:
        self.workflow = workflow
        self.diagnosis = diagnosis_engine
        self.audit_store = audit_store
        self.connector = connector
        self.operator_id = operator_id
        self.holdout_rate = holdout_rate

        self.catalog = ActionCatalog(workflow)
        self.stopping = StoppingRuleEvaluator()
        self.attribution = AttributionEngine()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, signal, existing_case: Optional[Case] = None) -> Case:
        """
        Process one signal through the full pipeline.
        If `existing_case` is provided, continue an in-progress case.
        Returns the (possibly mutated) Case.
        """
        from core.models import Signal
        assert isinstance(signal, Signal)

        now = datetime.utcnow()

        # ── STAGE 1: DETECT ──────────────────────────────────────────
        if existing_case is None:
            case = self._detect(signal, now)
        else:
            case = existing_case
            case.last_updated_at = now

        self._write_audit(case, PipelineStage.DETECT, {
            "signal_id": signal.signal_id,
            "revenue_at_risk": signal.revenue_at_risk,
            "source": signal.source,
            "group": case.group,
        })
        self.audit_store.upsert_case(case)

        # If case is in HOLDOUT control group, log diagnosis but suppress intervention
        if case.group == CaseGroup.HOLDOUT or case.group == "holdout":
            root_cause = self._diagnose(case)
            case.root_cause = root_cause
            case.status = CaseStatus.HOLDOUT_CONTROL
            case.close_reason = "Holdout control group — intervention suppressed for causality proof"
            case.closed_at = now

            self._write_audit(case, PipelineStage.DIAGNOSE, {
                "cause_code": root_cause.cause_code,
                "confidence": root_cause.confidence,
                "is_retriable": root_cause.is_retriable,
                "model_version": root_cause.model_version,
            }, model_version=root_cause.model_version)

            self._write_audit(case, PipelineStage.SUPPRESSED, {
                "suppression_reason": "holdout_control_group",
                "detail": "Case assigned to holdout control group (no intervention taken)",
                "action_type": ActionType.HOLDOUT_CONTROL,
            })
            self.audit_store.upsert_case(case)

            # Still run measurement on holdout cases to measure baseline natural recovery!
            self._measure(case, now)
            return case

        # ── STOPPING RULES (pre-diagnose) ─────────────────────────────
        stop = self.stopping.evaluate(case, now)
        if stop.should_stop and stop.stop_type != "cooldown":
            self._apply_stop(case, stop, now)
            return case
        if stop.should_stop and stop.stop_type == "cooldown":
            # Log suppressed action ledger entry for cooldown
            self._write_audit(case, PipelineStage.SUPPRESSED, {
                "suppression_reason": "cooldown_active",
                "detail": stop.reason,
            })
            self.audit_store.upsert_case(case)
            return case

        # ── STAGE 2: DIAGNOSE ─────────────────────────────────────────
        root_cause = self._diagnose(case)
        case.root_cause = root_cause
        self._write_audit(case, PipelineStage.DIAGNOSE, {
            "cause_code": root_cause.cause_code,
            "confidence": root_cause.confidence,
            "is_retriable": root_cause.is_retriable,
            "recommended_actions": [a for a in root_cause.recommended_action_types],
            "model_version": root_cause.model_version,
        }, model_version=root_cause.model_version)

        # ── STAGE 3: DECIDE ───────────────────────────────────────────
        intervention = self._decide(case, root_cause)
        if intervention is None:
            # Nothing actionable — close as no-op
            case.status = CaseStatus.CLOSED
            case.close_reason = "No actionable intervention in catalog for this root cause"
            case.closed_at = now
            self._write_audit(case, PipelineStage.DECIDE, {
                "decision": "no_op",
                "reason": case.close_reason,
            })
            self.audit_store.upsert_case(case)
            return case

        case.last_intervention = intervention
        self._write_audit(case, PipelineStage.DECIDE, {
            "intervention_id": intervention.intervention_id,
            "action_type": intervention.action_type,
            "requires_approval": intervention.requires_human_approval,
            "rationale": intervention.rationale,
            "catalog_version": intervention.catalog_version,
            "estimated_cost": intervention.estimated_cost,
        })

        # ── HUMAN APPROVAL GATE ───────────────────────────────────────
        if intervention.requires_human_approval:
            return self._trigger_approval_gate(case, intervention, now)

        # ── STOPPING RULES (pre-act) ──────────────────────────────────
        stop = self.stopping.evaluate(case, now)
        if stop.should_stop:
            self._apply_stop(case, stop, now)
            return case

        # ── STAGE 4: ACT ─────────────────────────────────────────────
        result = self._act(case, intervention, now)

        # ── STAGE 5: MEASURE ─────────────────────────────────────────
        self._measure(case, now)

        return case

    def process_payment_event(self, case: Case, payment_event_dict: dict) -> Case:
        """
        Called when a new payment event arrives for an account.
        Checks attribution and closes the case if recovered.
        """
        now = datetime.utcnow()
        events = self.audit_store.get_payment_events_for_account(
            case.account_id, since=case.opened_at
        )
        attr = self.attribution.measure(case, events, now)
        if attr.recovered:
            case = self.attribution.apply(case, attr)
            case.net_revenue_recovered = max(0.0, case.revenue_recovered - case.total_intervention_cost)
            self._write_audit(case, PipelineStage.MEASURE, {
                "recovered": True,
                "amount": attr.amount,
                "net_amount": case.net_revenue_recovered,
                "total_cost": case.total_intervention_cost,
                "matched_event_id": attr.matched_event_id,
                "matched_at": attr.matched_at.isoformat() if attr.matched_at else None,
                "reason": attr.reason,
            })
            self.audit_store.upsert_case(case)
        return case

    # ------------------------------------------------------------------
    # Stage implementations
    # ------------------------------------------------------------------

    def _detect(self, signal, now: datetime) -> Case:
        import random
        from core.models import Case, CaseGroup, Signal
        # Assign holdout group based on holdout_rate
        group = CaseGroup.HOLDOUT if (self.holdout_rate > 0 and random.random() < self.holdout_rate) else CaseGroup.TREATMENT
        return Case(
            case_id=str(uuid.uuid4()),
            workflow=self.workflow,
            group=group,
            signal=signal,
            status=CaseStatus.OPEN,
            account_id=signal.account_id,
            revenue_at_risk=signal.revenue_at_risk,
            opened_at=now,
            last_updated_at=now,
        )

    def _diagnose(self, case: Case) -> RootCause:
        return self.diagnosis.diagnose(case)

    def _decide(self, case: Case, root_cause: RootCause) -> Optional[Intervention]:
        """
        Select the first recommended action that exists in this workflow's catalog.
        Falls back to the first catalog entry if no recommendation matches.
        """
        defn = self.catalog.select_for_cause(
            root_cause.recommended_action_types,
        )
        if defn is None:
            # Last resort: pick first catalog entry
            all_actions = self.catalog.get_all()
            if not all_actions:
                return None
            defn = all_actions[0]

        return Intervention(
            action_type=defn.action_type,
            parameters=self._build_action_params(defn.action_type, case, root_cause),
            catalog_version=self.catalog.version(),
            rationale=(
                f"Root cause: {root_cause.cause_code} (confidence={root_cause.confidence:.2f}). "
                f"Action '{defn.display_name}' selected from {self.workflow.value} catalog v{self.catalog.version()}."
            ),
            requires_human_approval=defn.requires_human_approval,
            channel=defn.channel,
            estimated_cost=defn.unit_cost,
        )

    def _build_action_params(
        self,
        action_type: ActionType,
        case: Case,
        root_cause: RootCause,
    ) -> dict:
        base = {
            "case_id": case.case_id,
            "account_id": case.account_id,
            "revenue_at_risk": case.revenue_at_risk,
            "cause_code": root_cause.cause_code,
            "attempt_num": case.attempt_count + 1,
        }
        payload = case.signal.payload
        if action_type in (ActionType.SEND_EMAIL, ActionType.SEND_DUNNING_MESSAGE):
            base["template"] = f"{self.workflow.value}_{root_cause.cause_code.lower()}"
            base["to"] = payload.get("customer_email") or payload.get("email", "customer@example.com")
        elif action_type == ActionType.SEND_SMS:
            base["to"] = payload.get("customer_phone") or payload.get("phone", "+10000000000")
            base["template"] = f"{self.workflow.value}_sms"
        elif action_type == ActionType.RETRY_WITH_BACKOFF:
            base["backoff_seconds"] = [60, 300, 900][min(case.attempt_count, 2)]
            base["processor"] = payload.get("processor", "default")
        elif action_type == ActionType.ROUTE_ALTERNATE_PROCESSOR:
            base["from_processor"] = payload.get("processor", "primary")
            base["to_processor"] = "backup_processor"
        elif action_type == ActionType.TRIGGER_CARD_UPDATER:
            base["card_last4"] = payload.get("card_last4", "****")
            base["network"] = payload.get("card_network", "visa")
        elif action_type in (ActionType.LOG_HINGLISH_SCRIPT, ActionType.INITIATE_IVR_SCRIPT):
            base["language"] = payload.get("language_preference", "hi-en")
            base["customer_name"] = payload.get("customer_name", "Customer")
            base["outstanding"] = payload.get("outstanding_amount", case.revenue_at_risk)
        return base

    def _act(self, case: Case, intervention: Intervention, now: datetime) -> ActionResult:
        case.status = CaseStatus.ACTIVE
        case.attempt_count += 1
        case.last_updated_at = now

        result = self.connector(case, intervention)

        # Set cost from catalog definition if connector didn't override
        defn = self.catalog.get(intervention.action_type)
        if result.cost == 0.0 and defn:
            result.cost = defn.unit_cost

        case.total_intervention_cost += result.cost

        # Log suppressed action if connector returned blocked/suppressed status
        if not result.success and result.response_payload.get("blocked_reason"):
            self._write_audit(case, PipelineStage.SUPPRESSED, {
                "action_type": intervention.action_type,
                "suppression_reason": result.response_payload.get("blocked_reason"),
                "channel": result.response_payload.get("channel"),
                "error": result.error_message,
            })

        self._write_audit(case, PipelineStage.ACT, {
            "action_type": intervention.action_type,
            "connector": result.connector,
            "success": result.success,
            "is_stub": result.is_stub,
            "attempt_num": case.attempt_count,
            "cost": result.cost,
            "total_case_cost": case.total_intervention_cost,
            "response": result.response_payload,
            "error": result.error_message,
        })
        self.audit_store.upsert_case(case)
        return result

    def _measure(self, case: Case, now: datetime) -> None:
        """
        Check attribution after every Act step or holdout check.
        Only marks RECOVERED if a matching payment event exists.
        """
        events = self.audit_store.get_payment_events_for_account(
            case.account_id, since=case.opened_at
        )
        attr = self.attribution.measure(case, events, now)

        self._write_audit(case, PipelineStage.MEASURE, {
            "recovered": attr.recovered,
            "amount": attr.amount if attr.recovered else 0.0,
            "net_amount": max(0.0, attr.amount - case.total_intervention_cost) if attr.recovered else 0.0,
            "matched_event_id": attr.matched_event_id,
            "reason": attr.reason,
            "group": case.group,
        })

        if attr.recovered:
            case = self.attribution.apply(case, attr)
            case.net_revenue_recovered = max(0.0, case.revenue_recovered - case.total_intervention_cost)

        self.audit_store.upsert_case(case)

    def _trigger_approval_gate(
        self, case: Case, intervention: Intervention, now: datetime
    ) -> Case:
        """
        Block the case pending human approval.
        Writes an audit entry and creates an approval request.
        """
        case.status = CaseStatus.PENDING_APPROVAL
        case.pending_approval_since = now
        case.last_updated_at = now

        approval_id = self.audit_store.create_approval_request(
            case.case_id, intervention.model_dump_json()
        )

        self._write_audit(case, PipelineStage.DECIDE, {
            "gate": "human_approval_required",
            "action_type": intervention.action_type,
            "approval_id": approval_id,
            "reason": (
                "Action requires explicit human approval before execution. "
                f"Approval request created: {approval_id}"
            ),
        })
        self._write_audit(case, PipelineStage.SUPPRESSED, {
            "suppression_reason": "pending_human_approval",
            "action_type": intervention.action_type,
            "approval_id": approval_id,
            "detail": "Action suppressed until human operator authorizes execution",
        })
        self.audit_store.upsert_case(case)
        return case

    def approve_and_continue(
        self, case: Case, approval_id: str, approver_id: str, notes: str = ""
    ) -> Case:
        """
        Called when a human approves a pending escalation.
        Resumes the pipeline from the Act step.
        """
        now = datetime.utcnow()
        self.audit_store.resolve_approval(approval_id, approver_id, "approved", notes)
        self._write_audit(case, PipelineStage.ACT, {
            "gate_resolved": True,
            "approval_id": approval_id,
            "approver_id": approver_id,
            "decision": "approved",
            "notes": notes,
        }, operator_id=approver_id)

        if case.last_intervention is None:
            return case

        result = self._act(case, case.last_intervention, now)
        self._measure(case, now)
        return case

    def _apply_stop(self, case: Case, stop, now: datetime) -> None:
        from core.stopping_rules import StopEvaluation
        if stop.new_status:
            case.status = stop.new_status
        case.close_reason = stop.reason
        case.closed_at = now
        case.last_updated_at = now
        
        self._write_audit(case, PipelineStage.STOP, {
            "stop_type": stop.stop_type,
            "reason": stop.reason,
            "new_status": stop.new_status.value if stop.new_status else None,
            "attempt_count": case.attempt_count,
        })
        
        self._write_audit(case, PipelineStage.SUPPRESSED, {
            "suppression_reason": f"guardrail_stop_{stop.stop_type}",
            "detail": stop.reason,
            "action_type": case.last_intervention.action_type if case.last_intervention else None,
        })
        self.audit_store.upsert_case(case)

    # ------------------------------------------------------------------
    # Audit helper
    # ------------------------------------------------------------------

    def _write_audit(
        self,
        case: Case,
        stage: PipelineStage,
        payload: dict,
        model_version: Optional[str] = None,
        operator_id: Optional[str] = None,
    ) -> None:
        entry = AuditEntry(
            case_id=case.case_id,
            workflow=self.workflow.value if hasattr(self.workflow, "value") else str(self.workflow),
            stage=stage,
            attempt_num=case.attempt_count,
            payload=payload,
            model_version=model_version or self.diagnosis.model_version,
            operator_id=operator_id or self.operator_id,
            ts_utc=datetime.utcnow(),
        )
        self.audit_store.write(entry)
