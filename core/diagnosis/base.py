"""
core/diagnosis/base.py — Abstract base class for all diagnosis engines.

The pipeline calls `engine.diagnose(case)` — it does not care whether the
implementation is rule-based or LLM-assisted. Swap freely.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.models import Case, RootCause


class DiagnosisEngine(ABC):
    """
    Abstract diagnosis engine.

    Implementations:
      - RulesEngine (core/diagnosis/rules_engine.py) — default, zero LLM cost
      - LLMEngine   (core/diagnosis/llm_engine.py)  — optional, set DIAGNOSIS_ENGINE=llm
    """

    @property
    @abstractmethod
    def model_version(self) -> str:
        """Identifier for the model/rule version used — logged in every audit entry."""

    @abstractmethod
    def diagnose(self, case: Case) -> RootCause:
        """
        Analyse the case signal and produce a root-cause classification.

        Must always return a RootCause (never raise). If uncertain, return
        cause_code='UNKNOWN' with confidence=0.0.
        """
