# Architecture & System Design

## Core Pipeline Overview

The **AI Revenue Recovery** system operates on a unified 6-stage lifecycle for all recovery workflows:

```
[ DETECT ] ──► [ DIAGNOSE ] ──► [ DECIDE ] ──► [ ACT ] ──► [ MEASURE ] ──► [ AUDIT ]
```

1. **Detect**: Ingests signals from webhooks, batch files, or simulated event feeds. Creates a `Case` in state `OPEN`.
2. **Diagnose**: Classifies root cause using deterministic rules or LLM assistance. Assigns confidence, evidence, and retriability.
3. **Decide**: Selects exactly **ONE** intervention from the workflow's versioned, bounded action catalog. Checks human approval requirements.
4. **Act**: Evaluates stopping rules (caps, cooldowns, opt-outs), then executes the action via connector stubs or live APIs.
5. **Measure**: Attributes recovered revenue **only** when a matching payment success event arrives within the attribution window.
6. **Audit**: Writes an immutable, timestamped record for every step to SQLite.

---

## 🛡 Guardrail Subsystems

### 1. Stopping Rules Engine (`core/stopping_rules.py`)
Checked **before** every `Act` step. Hard stops terminate execution immediately on:
- Hard-stop metadata flags (`opt_out`, `payment_success`, `dispute`, `account_closed`, `voluntary_cancel`)
- Reaching max-attempt caps (e.g. 3 attempts for checkout recovery, 5 for subscriptions)
- Exceeding maximum case age (e.g. 3 days for checkout, 90 days for B2B)
- Active cooldown periods (prevents spamming customer or processor)

### 2. Bounded Action Catalog (`core/action_catalog.py`)
Each workflow ships with a fixed, versioned list of allowed `ActionType`s. The agent selects from this catalog; it cannot generate or execute unapproved action types at runtime.

### 3. Human Approval Gates (`core/pipeline.py`)
Actions touching collections referrals, legal notices, or AR escalation trigger a `PENDING_APPROVAL` status:
- The case halts before execution.
- An approval record is created in `approval_requests`.
- Only when an authorized operator approves via `engine.approve_and_continue(...)` does the pipeline resume.

### 4. Revenue Attribution Engine (`core/attribution.py`)
- Revenue is **never** claimed on intent or action delivery alone.
- Attribution requires: `PaymentEvent(status="success", account_id=X)` occurring **after** case opening and **within** the workflow's attribution window (e.g., 24h for checkout, 7d for subscription, 30d for B2B).

### 5. Append-Only Audit Trail (`core/audit.py`)
- SQLite database enforced via DB-level `BEFORE UPDATE` and `BEFORE DELETE` triggers that raise operational errors on any mutation attempt.
- Every case can be reconstructed step-by-step.

---

## Workflow Modules

1. **Payment Degradation (`w1_payment_degradation.py`)**: Detects decline spikes across processor/BIN/region. Reroutes or triggers card updater.
2. **Checkout Abandonment (`w2_checkout_abandonment.py`)**: Scores cart recoverability and triggers sequenced email/SMS/push.
3. **Subscription Recovery (`w3_subscription_recovery.py`)**: Handles involuntary churn from card failures with smart dunning and card updater.
4. **B2B Receivables (`w4_b2b_receivables.py`)**: Graduated overdue invoice chaser with human-approval gates for legal/collections.
5. **Mandate Retry (`w5_mandate_retry.py`)**: NPCI/NACHA compliant retry sequencer respecting network retry limits.
6. **Hinglish Voice (`w6_hinglish_voice.py`)**: Generates and logs code-mixed Hindi-English outreach scripts with response fields.
7. **Promise-to-Pay (`w7_promise_to_pay.py`)**: Tracks PTP due dates and automatically escalates broken promises.
