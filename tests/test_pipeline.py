"""
tests/test_pipeline.py — End-to-end pipeline test and integration checks.

Verifies:
  - Full DETECT -> DIAGNOSE -> DECIDE -> ACT -> MEASURE -> AUDIT flow
  - Audit log entries created at every stage
  - Human approval gate behavior and resolution
  - Immutability trigger on audit log table (DB-level error on UPDATE/DELETE)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from core.audit import AuditStore
from core.diagnosis.rules_engine import RulesEngine
from core.models import (
    ActionResult,
    ActionType,
    Case,
    CaseStatus,
    Intervention,
    PipelineStage,
    Signal,
    SignalSource,
    WorkflowType,
)
from core.pipeline import PipelineEngine
import workflows.w4_b2b_receivables as w4


def dummy_connector(case: Case, intervention: Intervention) -> ActionResult:
    return ActionResult(
        success=True,
        connector="dummy_connector",
        response_payload={"action_taken": intervention.action_type},
        is_stub=True,
    )


class TestPipelineEngine:

    def test_full_pipeline_flow(self, tmp_path: Path):
        db_path = tmp_path / "test_audit.db"
        audit_store = AuditStore(db_path)
        engine = PipelineEngine(
            workflow=WorkflowType.CHECKOUT_ABANDONMENT,
            diagnosis_engine=RulesEngine(),
            audit_store=audit_store,
            connector=dummy_connector,
            holdout_rate=0.0,
        )

        signal = Signal(
            workflow=WorkflowType.CHECKOUT_ABANDONMENT,
            occurred_at=w4.datetime.utcnow(),
            source=SignalSource.SIMULATION,
            account_id="acc_pipe_01",
            revenue_at_risk=250.0,
            payload={
                "session_id": "sess_123",
                "cart_value": 250.0,
                "time_since_abandonment_minutes": 20,
                "prior_purchase_count": 2,
                "all_items_available": True,
                "customer_email": "test@example.com",
            },
        )

        case = engine.run(signal)

        assert case.status in (CaseStatus.ACTIVE, CaseStatus.RECOVERED)
        assert case.attempt_count == 1
        assert case.root_cause is not None

        entries = audit_store.get_case_audit(case.case_id)
        stages = [e["stage"] for e in entries]
        assert "detect" in stages
        assert "diagnose" in stages
        assert "decide" in stages
        assert "act" in stages
        assert "measure" in stages

        audit_store.close()

    def test_human_approval_gate(self, tmp_path: Path):
        db_path = tmp_path / "test_audit_gate.db"
        audit_store = AuditStore(db_path)

        # W4 critically overdue triggers collections (requires approval)
        raw_signal = {
            "account_id": "acc_b2b_gate",
            "invoice_id": "inv_gate_001",
            "invoice_number": "INV-GATE",
            "amount": 75000.0,
            "days_overdue": 90,
            "account_tier": "smb",
            "payment_history_score": 0.2,
            "has_active_dispute": False,
        }

        case = w4.run_case(raw_signal, audit_store, holdout_rate=0.0)

        assert case.status == CaseStatus.PENDING_APPROVAL
        assert case.pending_approval_since is not None

        pending = audit_store.get_pending_approvals()
        assert len(pending) == 1
        approval_id = pending[0]["approval_id"]

        # Now approve and continue
        engine = w4.build_engine(audit_store)
        case_approved = engine.approve_and_continue(
            case, approval_id, approver_id="manager_john", notes="Approved for collections referral"
        )

        assert case_approved.status == CaseStatus.ACTIVE
        assert case_approved.attempt_count == 1

        entries = audit_store.get_case_audit(case.case_id)
        operators = [e["operator_id"] for e in entries]
        assert "manager_john" in operators

        audit_store.close()

    def test_audit_store_immutability(self, tmp_path: Path):
        db_path = tmp_path / "test_immutability.db"
        audit_store = AuditStore(db_path)

        signal = Signal(
            workflow=WorkflowType.MANDATE_RETRY,
            occurred_at=w4.datetime.utcnow(),
            source=SignalSource.SIMULATION,
            account_id="acc_immut",
            revenue_at_risk=500.0,
        )
        engine = PipelineEngine(
            workflow=WorkflowType.MANDATE_RETRY,
            diagnosis_engine=RulesEngine(),
            audit_store=audit_store,
            connector=dummy_connector,
        )
        case = engine.run(signal)

        entries = audit_store.get_case_audit(case.case_id)
        assert len(entries) > 0
        entry_id = entries[0]["entry_id"]

        # Attempt raw SQL update — DB trigger must block it
        conn = sqlite3.connect(str(db_path))
        with pytest.raises(sqlite3.DatabaseError, match="UPDATE is forbidden"):
            conn.execute("UPDATE audit_log SET operator_id = 'hacker' WHERE entry_id = ?", (entry_id,))

        with pytest.raises(sqlite3.DatabaseError, match="DELETE is forbidden"):
            conn.execute("DELETE FROM audit_log WHERE entry_id = ?", (entry_id,))

        conn.close()
        audit_store.close()
