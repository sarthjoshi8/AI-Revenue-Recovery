# Runbook: Adding a New Recovery Workflow

This guide walks through creating and registering a new revenue recovery workflow (e.g. `w8_chargeback_prevention`).

---

## Step 1: Define WorkflowType and ActionTypes

In `core/models.py`:
1. Add your new workflow enum value to `WorkflowType`:
   ```python
   class WorkflowType(str, Enum):
       ...
       CHARGEBACK_PREVENTION = "chargeback_prevention"
   ```
2. If new actions are needed, add them to `ActionType`:
   ```python
   class ActionType(str, Enum):
       ...
       REFUND_PRE_ARBITRATION = "refund_pre_arbitration"
   ```

---

## Step 2: Register Bounded Action Catalog

In `core/action_catalog.py`, add your workflow's allowed actions to `WORKFLOW_CATALOGS`:
```python
WorkflowType.CHARGEBACK_PREVENTION: [
    ActionDefinition(
        ActionType.REFUND_PRE_ARBITRATION,
        "Issue pre-arbitration refund",
        "Issue full refund to avoid chargeback fee and merchant ratio hit",
        channel="payment",
        requires_human_approval=True, # Gate high-dollar refunds!
    ),
],
```

---

## Step 3: Configure Stopping Rules

In `core/stopping_rules.py`, add entry to `DEFAULT_STOPPING_CONFIGS`:
```python
WorkflowType.CHARGEBACK_PREVENTION: StoppingRuleConfig(
    workflow=WorkflowType.CHARGEBACK_PREVENTION,
    max_attempts=1,
    cooldown_hours=24,
    hard_stop_flags=["chargeback_filed", "opt_out", "refund_issued"],
),
```

---

## Step 4: Add Diagnosis Rules

In `core/diagnosis/rules_engine.py`:
1. Add `_diagnose_chargeback_prevention(self, case: Case) -> RootCause` method.
2. Register it in `self.diagnose()` dispatch dictionary.

---

## Step 5: Implement Workflow Module

Create `workflows/w8_chargeback_prevention.py`:
- Define `build_engine(audit_store)`
- Define `build_signal(raw_dict)`
- Define `build_payment_event(case, success)`
- Define `run_case(raw_dict, audit_store)`

---

## Step 6: Register in Batch Runner & Tests

1. In `runner/batch_runner.py`, add `w8` to `WORKFLOW_REGISTRY`.
2. In `data/synthetic/generator.py`, add a generator function `generate_chargebacks()`.
3. Add a unit test in `tests/test_workflows.py`.
