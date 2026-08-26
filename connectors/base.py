"""
connectors/base.py — Abstract connector interface.

All connectors (payment, email, SMS, IVR, CRM) implement this interface.
The pipeline calls connector(case, intervention) — it does not care
whether the connector is a stub or a live API integration.

To add a real connector:
  1. Subclass BaseConnector
  2. Implement execute()
  3. Register in the workflow's connector factory
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from core.models import ActionResult, Case, Intervention


class BaseConnector(ABC):
    """Abstract base for all connectors."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable connector name logged in ActionResult.connector."""

    @property
    def is_stub(self) -> bool:
        """True when this is a stub (no real API call made)."""
        return True

    @abstractmethod
    def execute(self, case: Case, intervention: Intervention) -> ActionResult:
        """
        Execute the intervention. Must always return an ActionResult.
        Never raise — catch exceptions and return ActionResult(success=False).
        """

    def __call__(self, case: Case, intervention: Intervention) -> ActionResult:
        """Make connectors callable — satisfies ConnectorFn type."""
        return self.execute(case, intervention)


class CompositeConnector(BaseConnector):
    """
    Routes interventions to the correct sub-connector based on action_type.
    Used by workflows that need multiple connector types.
    """

    def __init__(self, routing_table: dict) -> None:
        """
        routing_table: {ActionType -> BaseConnector instance}
        """
        self._table = routing_table
        self._default = NullConnector()

    @property
    def name(self) -> str:
        return "composite"

    def execute(self, case: Case, intervention: Intervention) -> ActionResult:
        action = intervention.action_type
        connector = self._table.get(action, self._default)
        return connector.execute(case, intervention)


class NullConnector(BaseConnector):
    """No-op connector — used when no connector is registered for an action."""

    @property
    def name(self) -> str:
        return "null"

    def execute(self, case: Case, intervention: Intervention) -> ActionResult:
        return ActionResult(
            success=True,
            connector=self.name,
            response_payload={"note": "NullConnector: no action taken"},
            executed_at=datetime.utcnow(),
            is_stub=True,
        )
