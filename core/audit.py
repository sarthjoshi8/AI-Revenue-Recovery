"""
core/audit.py — Append-only SQLite audit store.

GUARANTEES:
  1. No UPDATE or DELETE is ever issued against the audit table.
  2. A DB-level trigger enforces this — any UPDATE/DELETE raises an error.
  3. entries are deduplicated by (case_id, stage, attempt_num) — a duplicate
     write is silently ignored (INSERT OR IGNORE), preserving the first record.
  4. The audit store is the single source of truth for reconstructing a case.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.models import AuditEntry, PipelineStage


_DEFAULT_DB_PATH = Path("output") / "audit.db"


class AuditStore:
    """
    Thread-safe, append-only SQLite audit log.

    Usage:
        store = AuditStore()
        store.write(entry)
        entries = store.get_case(case_id)
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ------------------------------------------------------------------
    # Internal setup
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                entry_id       TEXT PRIMARY KEY,
                case_id        TEXT NOT NULL,
                workflow       TEXT NOT NULL,
                stage          TEXT NOT NULL,
                attempt_num    INTEGER NOT NULL DEFAULT 0,
                payload_json   TEXT NOT NULL,
                model_version  TEXT NOT NULL DEFAULT 'rules-v1',
                operator_id    TEXT NOT NULL DEFAULT 'system',
                ts_utc         TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_audit_case_id
                ON audit_log (case_id);

            CREATE INDEX IF NOT EXISTS idx_audit_workflow
                ON audit_log (workflow);

            CREATE INDEX IF NOT EXISTS idx_audit_ts
                ON audit_log (ts_utc);

            -- Immutability trigger: prevent UPDATE on audit_log
            CREATE TRIGGER IF NOT EXISTS trg_no_update_audit
                BEFORE UPDATE ON audit_log
            BEGIN
                SELECT RAISE(ABORT, 'audit_log is append-only — UPDATE is forbidden');
            END;

            -- Immutability trigger: prevent DELETE on audit_log
            CREATE TRIGGER IF NOT EXISTS trg_no_delete_audit
                BEFORE DELETE ON audit_log
            BEGIN
                SELECT RAISE(ABORT, 'audit_log is append-only — DELETE is forbidden');
            END;

            -- Cases table (mutable — tracks current case state)
            CREATE TABLE IF NOT EXISTS cases (
                case_id              TEXT PRIMARY KEY,
                workflow             TEXT NOT NULL,
                account_id           TEXT NOT NULL,
                status               TEXT NOT NULL,
                group_name           TEXT NOT NULL DEFAULT 'treatment',
                attempt_count        INTEGER NOT NULL DEFAULT 0,
                revenue_at_risk      REAL NOT NULL DEFAULT 0.0,
                revenue_recovered    REAL NOT NULL DEFAULT 0.0,
                total_intervention_cost REAL NOT NULL DEFAULT 0.0,
                net_revenue_recovered REAL NOT NULL DEFAULT 0.0,
                opened_at            TEXT NOT NULL,
                last_updated_at      TEXT NOT NULL,
                closed_at            TEXT,
                close_reason         TEXT,
                recovered_at         TEXT,
                signal_json          TEXT NOT NULL,
                root_cause_json      TEXT,
                last_intervention_json TEXT,
                metadata_json        TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_cases_workflow
                ON cases (workflow);

            CREATE INDEX IF NOT EXISTS idx_cases_status
                ON cases (status);

            -- Payment events table (for attribution)
            CREATE TABLE IF NOT EXISTS payment_events (
                event_id     TEXT PRIMARY KEY,
                case_id      TEXT,
                account_id   TEXT NOT NULL,
                amount       REAL NOT NULL,
                currency     TEXT NOT NULL DEFAULT 'USD',
                status       TEXT NOT NULL,
                processor    TEXT NOT NULL DEFAULT 'unknown',
                decline_code TEXT,
                occurred_at  TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_pe_case_id
                ON payment_events (case_id);

            CREATE INDEX IF NOT EXISTS idx_pe_account_id
                ON payment_events (account_id);

            -- Human approval queue
            CREATE TABLE IF NOT EXISTS approval_requests (
                approval_id      TEXT PRIMARY KEY,
                case_id          TEXT NOT NULL,
                intervention_json TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'pending',
                approver_id      TEXT,
                decision         TEXT,
                notes            TEXT DEFAULT '',
                requested_at     TEXT NOT NULL,
                decided_at       TEXT
            );
            """
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Audit entry operations (append-only)
    # ------------------------------------------------------------------

    def write(self, entry: AuditEntry) -> None:
        """Append an audit entry. Duplicate (case_id, stage, attempt_num) is ignored."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR IGNORE INTO audit_log
                (entry_id, case_id, workflow, stage, attempt_num,
                 payload_json, model_version, operator_id, ts_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.entry_id,
                entry.case_id,
                entry.workflow,
                entry.stage.value if hasattr(entry.stage, "value") else entry.stage,
                entry.attempt_num,
                json.dumps(entry.payload),
                entry.model_version,
                entry.operator_id,
                entry.ts_utc.isoformat(),
            ),
        )
        conn.commit()

    def get_case_audit(self, case_id: str) -> list[dict]:
        """Return all audit entries for a case, ordered by timestamp."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE case_id = ? ORDER BY ts_utc ASC",
            (case_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_entries(self, limit: int = 100) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY ts_utc DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_workflow_entries(self, workflow: str) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE workflow = ? ORDER BY ts_utc ASC",
            (workflow,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Case persistence
    # ------------------------------------------------------------------

    def upsert_case(self, case) -> None:  # noqa: ANN001
        """Write or update a case record (mutable — only cases, never audit_log)."""
        from core.models import Case
        assert isinstance(case, Case)
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO cases
                (case_id, workflow, account_id, status, group_name, attempt_count,
                 revenue_at_risk, revenue_recovered, total_intervention_cost, net_revenue_recovered,
                 opened_at, last_updated_at, closed_at, close_reason, recovered_at, signal_json,
                 root_cause_json, last_intervention_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id) DO UPDATE SET
                status = excluded.status,
                group_name = excluded.group_name,
                attempt_count = excluded.attempt_count,
                revenue_recovered = excluded.revenue_recovered,
                total_intervention_cost = excluded.total_intervention_cost,
                net_revenue_recovered = excluded.net_revenue_recovered,
                last_updated_at = excluded.last_updated_at,
                closed_at = excluded.closed_at,
                close_reason = excluded.close_reason,
                recovered_at = excluded.recovered_at,
                root_cause_json = excluded.root_cause_json,
                last_intervention_json = excluded.last_intervention_json,
                metadata_json = excluded.metadata_json
            """,
            (
                case.case_id,
                case.workflow.value if hasattr(case.workflow, "value") else case.workflow,
                case.account_id,
                case.status.value if hasattr(case.status, "value") else case.status,
                case.group.value if hasattr(case.group, "value") else str(case.group),
                case.attempt_count,
                case.revenue_at_risk,
                case.revenue_recovered,
                case.total_intervention_cost,
                case.net_revenue_recovered,
                case.opened_at.isoformat(),
                case.last_updated_at.isoformat(),
                case.closed_at.isoformat() if case.closed_at else None,
                case.close_reason,
                case.recovered_at.isoformat() if case.recovered_at else None,
                case.signal.model_dump_json(),
                case.root_cause.model_dump_json() if case.root_cause else None,
                case.last_intervention.model_dump_json() if case.last_intervention else None,
                json.dumps(case.metadata),
            ),
        )
        conn.commit()

    def get_case(self, case_id: str) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_cases_by_workflow(self, workflow: str) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM cases WHERE workflow = ? ORDER BY opened_at ASC",
            (workflow,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_cases(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM cases ORDER BY opened_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Payment events (for attribution)
    # ------------------------------------------------------------------

    def write_payment_event(self, event) -> None:  # noqa: ANN001
        from core.models import PaymentEvent
        assert isinstance(event, PaymentEvent)
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR IGNORE INTO payment_events
                (event_id, case_id, account_id, amount, currency,
                 status, processor, decline_code, occurred_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.case_id,
                event.account_id,
                event.amount,
                event.currency,
                event.status,
                event.processor,
                event.decline_code,
                event.occurred_at.isoformat(),
                json.dumps(event.metadata),
            ),
        )
        conn.commit()

    def get_payment_events_for_case(self, case_id: str) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM payment_events WHERE case_id = ? ORDER BY occurred_at ASC",
            (case_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_payment_events_for_account(
        self,
        account_id: str,
        since: Optional[datetime] = None,
    ) -> list[dict]:
        conn = self._get_conn()
        if since:
            rows = conn.execute(
                """SELECT * FROM payment_events
                   WHERE account_id = ? AND occurred_at >= ?
                   ORDER BY occurred_at ASC""",
                (account_id, since.isoformat()),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM payment_events WHERE account_id = ? ORDER BY occurred_at ASC",
                (account_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Human approval queue
    # ------------------------------------------------------------------

    def create_approval_request(
        self, case_id: str, intervention_json: str
    ) -> str:
        """Create a pending approval request. Returns approval_id."""
        import uuid as _uuid
        approval_id = str(_uuid.uuid4())
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO approval_requests
                (approval_id, case_id, intervention_json, status, requested_at)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (approval_id, case_id, intervention_json, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return approval_id

    def resolve_approval(
        self,
        approval_id: str,
        approver_id: str,
        decision: str,
        notes: str = "",
    ) -> None:
        """Mark an approval as approved or rejected."""
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE approval_requests
            SET status = ?, approver_id = ?, decision = ?, notes = ?, decided_at = ?
            WHERE approval_id = ?
            """,
            (decision, approver_id, decision, notes, datetime.utcnow().isoformat(), approval_id),
        )
        conn.commit()

    def get_pending_approvals(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM approval_requests WHERE status = 'pending' ORDER BY requested_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_approval(self, approval_id: str) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM approval_requests WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
