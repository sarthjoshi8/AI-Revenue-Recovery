# ⚡ AI Revenue Recovery Agent System

An autonomous, multi-workflow agent system that **detects** slipping revenue, **diagnoses** root causes, **decides** bounded interventions, **executes** actions, **proves incremental recovery** with holdout control groups, and maintains an immutable **audit trail**.

---

## 🌟 Core Differentiators & Product Novelty

Unlike generic "dunning bots" that blindly blast emails or claim credit for organic returns, **AI Revenue Recovery** implements three foundational innovations:

### 1. 🛡️ Causal Incrementality & Holdout Control Groups
- **Stochastic Holdout Splits**: Automatically assigns a random holdout control group (default 15%) for every workflow.
- **Intervention Suppression**: Holds back interventions for control cases while continuing measurement.
- **Causal Lift Attribution**: Computes net causal revenue lift against organic baseline recovery:
  $$\text{Incremental Lift \%} = \max(0, \text{Treatment Recovery Rate \%} - \text{Holdout Recovery Rate \%})$$

### 2. 💎 Net Revenue Recovery & Unit Economics (Cost-Per-Channel)
- **Granular Cost Schedules**: Every action carries an explicit unit cost (e.g., Email `$0.005`, SMS `$0.035`, IVR `$0.15`, Card Updater `$0.45`, AR Escalation `$2.50`, Collections `$12.00`).
- **Net Recovered Revenue**: Reports true CFO-grade recovery:
  $$\text{Net Revenue Recovered} = \text{Gross Revenue Recovered} - \text{Total Intervention Cost}$$

### 3. 🔒 Suppressed-Actions Ledger ("Deliberately Did Not Do")
- **Immutable Guardrail Evidence**: Front-and-center ledger tracking every action blocked by quiet hours, opt-outs, attempt caps, human approval gates, or control group assignment.
- **Auditability**: Complete transparency into *why* an intervention was withheld.

---

## 🎨 Starbucks Visual Identity System (Web UI)

The HTML report dashboard (`output/report.html`) is styled using the **Starbucks Visual Identity System**:
- **Palette**: Deep Green (`#006241`), Dark Roast (`#1E3932`), Warm Gold (`#CBA258`), and Cream Oat (`#F3F1E7`).
- **Features**: Live KPI cards for Net Recovery & Causal Lift, Filter Pills, Case Drawers, Human Approval Queue, and Suppressed-Actions Ledger.

---

## 📦 7 Modular Workflows

1. **W1: Payment Degradation**: Detects decline rate anomalies by processor/BIN/region. Reroutes or triggers card updater.
2. **W2: Checkout Drop-Off**: Recovers abandoned carts using recoverability scoring and sequenced nudges.
3. **W3: Subscription Recovery**: Smart dunning for involuntary churn aligned to billing/payday cycles.
4. **W4: B2B Receivables**: Graduated overdue invoice chaser with human-approval gates for external collections.
5. **W5: Mandate Retries**: NPCI/NACHA compliant retry sequencer respecting network rules.
6. **W6: Hinglish Voice**: Code-mixed Hindi-English voice/IVR script generator and transcript logger.
7. **W7: Promise-to-Pay Tracker**: Monitors customer PTP commitments and auto-escalates broken promises.

---

## 🛡️ Non-Negotiable Safety & Governance

- **Stopping Rules Engine**: Hard caps on attempts, cooldown windows, age limits, and opt-out/dispute hard-stops.
- **Human Approval Gates**: High-consequence actions (external collections, legal notices) require human confirmation before firing.
- **Bounded Action Catalog**: Versioned catalog of pre-approved actions per workflow — zero hallucinated runtime actions.
- **Evidence-Based Attribution**: Revenue is claimed **only** when a matching successful payment event is recorded in the attribution window.
- **Immutable Audit Trail**: SQLite database with DB-level `UPDATE` and `DELETE` triggers preventing audit tampering.

---

## 🚀 Quick Start

### 1. Installation

Requires Python 3.9+.

```bash
git clone https://github.com/your-org/ai-revenue-recovery.git
cd "AI Revenue Recovery"

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Run the Batch Recovery Pipeline

Process signals across all 7 workflows end-to-end:

```bash
python -m runner.batch_runner
```

**Outputs Generated**:
- `output/audit.db`: Append-only SQLite audit database.
- `output/batch_report.json`: Machine-readable JSON execution summary.
- `output/report.html`: Starbucks-themed interactive web dashboard.

### 3. Launch Local Web Dashboard

```bash
python server.py
```
> Open **[http://localhost:8000](http://localhost:8000)** in your browser.

### 4. Rich Terminal Dashboard & Audit CLI

```bash
# Launch Rich Terminal Dashboard
python -m reporting.dashboard

# View Suppressed-Actions Ledger (Deliberately blocked actions)
python -m reporting.audit_viewer --suppressed-actions

# View Causal Incrementality & Unit Economics Analysis
python -m reporting.audit_viewer --holdout-analysis

# View recent cases
python -m reporting.audit_viewer --recent 20

# Inspect full audit trail for a specific case ID
python -m reporting.audit_viewer --case-id <CASE_ID>
```

### 5. Run Test Suite

```bash
pytest
```

---

## 🏛️ Architecture Overview

For in-depth technical details, see [ARCHITECTURE.md](ARCHITECTURE.md).

```
  ┌──────────┐     ┌──────────┐     ┌──────────┐
  │ DETECT   ├────►│ DIAGNOSE ├────►│ DECIDE   │
  └──────────┘     └──────────┘     └────┬─────┘
                                         │ (Check Approval, Holdout & Stopping Rules)
  ┌──────────┐     ┌──────────┐     ┌────▼─────┐
  │  AUDIT   │◄────┤ MEASURE  │◄────┤   ACT    │
  └──────────┘     └──────────┘     └──────────┘
```

---

## 📖 Runbook & Extending System

Refer to [RUNBOOK.md](RUNBOOK.md) for instructions on adding custom workflows, diagnosis rules, action definitions, and connector integrations.

---

## 📄 License

MIT License — see [LICENSE](LICENSE).
