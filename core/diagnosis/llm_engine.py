"""
core/diagnosis/llm_engine.py — LLM-assisted diagnosis engine (optional stub).

Set DIAGNOSIS_ENGINE=llm in your environment to activate.
The stub returns a placeholder response with instructions for wiring in a real LLM.

To connect a real LLM:
  1. Install: pip install google-genai  (or openai)
  2. Set: GEMINI_API_KEY=your-key  (or OPENAI_API_KEY=your-key)
  3. Implement _call_llm() below — the interface is already defined.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from core.diagnosis.base import DiagnosisEngine
from core.diagnosis.rules_engine import RulesEngine
from core.models import ActionType, Case, RootCause

_VERSION = "llm-stub-v1.0"


class LLMEngine(DiagnosisEngine):
    """
    LLM-assisted diagnosis. Falls back to RulesEngine if LLM is unavailable.

    The prompt asks the LLM to classify root cause and suggest actions from
    the bounded action catalog — it cannot invent new action types.
    """

    def __init__(self) -> None:
        self._rules_fallback = RulesEngine()
        self._api_key = os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
        self._model = os.getenv("LLM_MODEL", "gemini-2.0-flash")

    @property
    def model_version(self) -> str:
        return f"{_VERSION}/{self._model}"

    def diagnose(self, case: Case) -> RootCause:
        """
        Try LLM diagnosis; fall back to rules on any error.
        """
        if not self._api_key:
            # No key configured — silent fallback
            rc = self._rules_fallback.diagnose(case)
            rc.model_version = f"{_VERSION}/rules-fallback (no API key)"
            return rc

        try:
            return self._llm_diagnose(case)
        except Exception as exc:  # noqa: BLE001
            # Always fall back — never crash the pipeline on LLM failure
            rc = self._rules_fallback.diagnose(case)
            rc.model_version = f"{_VERSION}/rules-fallback ({type(exc).__name__})"
            return rc

    def _llm_diagnose(self, case: Case) -> RootCause:
        """
        Call the LLM with a structured prompt and parse the JSON response.
        STUB: Returns a hardcoded response. Wire in your provider below.
        """
        prompt = self._build_prompt(case)

        # ----------------------------------------------------------------
        # STUB — replace this block with your real LLM call:
        #
        # Example (google-genai):
        #   from google import genai
        #   client = genai.Client(api_key=self._api_key)
        #   response = client.models.generate_content(
        #       model=self._model,
        #       contents=prompt,
        #       config={"response_mime_type": "application/json"},
        #   )
        #   raw = response.text
        #
        # Example (openai):
        #   import openai
        #   client = openai.OpenAI(api_key=self._api_key)
        #   r = client.chat.completions.create(
        #       model=self._model,
        #       messages=[{"role": "user", "content": prompt}],
        #       response_format={"type": "json_object"},
        #   )
        #   raw = r.choices[0].message.content
        # ----------------------------------------------------------------

        raw = json.dumps({
            "cause_code": "LLM_STUB_RESPONSE",
            "decline_category": None,
            "description": (
                "This is a stub LLM response. "
                "Wire in a real provider in core/diagnosis/llm_engine.py."
            ),
            "confidence": 0.5,
            "evidence": ["LLM not yet configured"],
            "is_retriable": True,
            "recommended_action_types": ["human_review"],
        })

        return self._parse_response(raw)

    def _build_prompt(self, case: Case) -> str:
        allowed_actions = [a.value for a in ActionType]
        signal_summary = json.dumps(case.signal.payload, indent=2)
        return f"""You are a revenue recovery diagnosis agent.

Case: {case.case_id}
Workflow: {case.workflow}
Account: {case.account_id}
Revenue at risk: ${case.revenue_at_risk:.2f}
Signal payload:
{signal_summary}

Diagnose the root cause of this revenue loss and recommend recovery actions.
You MUST only recommend actions from this fixed list: {allowed_actions}

Respond with a JSON object matching this schema:
{{
  "cause_code": "string (e.g. INSUFFICIENT_FUNDS, ISSUER_OUTAGE, EXPIRED_CARD, ...)",
  "decline_category": "soft|hard|fraud|network|expired_card|insufficient_funds|...|null",
  "description": "1-2 sentence explanation",
  "confidence": 0.0-1.0,
  "evidence": ["list", "of", "evidence", "strings"],
  "is_retriable": true|false,
  "recommended_action_types": ["action_type_1", "action_type_2"]
}}
"""

    def _parse_response(self, raw: str) -> RootCause:
        data = json.loads(raw)
        action_types = []
        for a in data.get("recommended_action_types", []):
            try:
                action_types.append(ActionType(a))
            except ValueError:
                pass  # Ignore invalid action types from LLM

        return RootCause(
            cause_code=data.get("cause_code", "UNKNOWN"),
            decline_category=data.get("decline_category"),
            description=data.get("description", ""),
            confidence=float(data.get("confidence", 0.5)),
            evidence=data.get("evidence", []),
            is_retriable=data.get("is_retriable", True),
            recommended_action_types=action_types,
            model_version=self.model_version,
        )


def get_diagnosis_engine() -> DiagnosisEngine:
    """
    Factory — reads DIAGNOSIS_ENGINE env var to pick the engine.
    Default: rules. Set DIAGNOSIS_ENGINE=llm for LLM-assisted mode.
    """
    engine_type = os.getenv("DIAGNOSIS_ENGINE", "rules").lower()
    if engine_type == "llm":
        return LLMEngine()
    return RulesEngine()
