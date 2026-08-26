"""
data/synthetic/generator.py — Master synthetic data generator.

Generates realistic sample data for all 7 workflow signal types.
Each generator seeds from a fixed seed for reproducibility.
Run: python -m data.synthetic.generator
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from faker import Faker

fake = Faker()
Faker.seed(42)
random.seed(42)

OUTPUT_DIR = Path("data/sample")


def _rand_dt(days_ago_max: int = 7, days_ago_min: int = 0) -> str:
    delta = random.uniform(days_ago_min, days_ago_max)
    dt = datetime.utcnow() - timedelta(days=delta)
    return dt.isoformat()


def _account_id() -> str:
    return f"acc_{fake.uuid4()[:8]}"


# ---------------------------------------------------------------------------
# W1: Payment degradation signals
# ---------------------------------------------------------------------------


def generate_payment_degradation(n: int = 60) -> list[dict]:
    PROCESSORS = ["stripe", "adyen", "braintree", "square", "checkout_com"]
    NETWORKS = ["visa", "mastercard", "amex", "discover"]
    REGIONS = ["US", "EU", "IN", "GB", "AU", "SG"]
    BINS = [f"{random.randint(400000, 499999)}" for _ in range(20)]

    DECLINE_CODE_POOLS = {
        "issuer_outage": {"91": 40, "96": 30, "ISSUER_UNAVAILABLE": 20, "51": 10},
        "3ds": {"3DS_FAIL": 50, "N3": 30, "3DS_TIMEOUT": 20},
        "expired": {"54": 60, "EXPIRED_CARD": 30, "14": 10},
        "routing": {"05": 25, "51": 25, "91": 25, "DO_NOT_HONOR": 25},
        "random": {"51": 30, "05": 20, "54": 15, "91": 20, "14": 15},
    }

    records = []
    for i in range(n):
        scenario = random.choice(list(DECLINE_CODE_POOLS.keys()))
        baseline = round(random.uniform(0.02, 0.05), 3)
        current = round(baseline * random.uniform(2.5, 8.0), 3)
        volume = random.randint(500, 10000)
        avg_txn = round(random.uniform(25, 250), 2)

        raw_codes = DECLINE_CODE_POOLS[scenario]
        total_declines = int(volume * current)
        decline_codes = {
            code: int(total_declines * pct / 100)
            for code, pct in raw_codes.items()
        }

        records.append({
            "account_id": "processor_global",
            "processor": random.choice(PROCESSORS),
            "issuer_bin": random.choice(BINS),
            "card_network": random.choice(NETWORKS),
            "region": random.choice(REGIONS),
            "decline_rate_current": current,
            "decline_rate_baseline": baseline,
            "sample_volume": volume,
            "avg_txn_value": avg_txn,
            "revenue_at_risk": round(volume * (current - baseline) * avg_txn, 2),
            "decline_codes": decline_codes,
            "window_minutes": random.choice([30, 60, 120]),
            "occurred_at": _rand_dt(days_ago_max=2),
            "source": "simulation",
            "_scenario": scenario,
        })
    return records


# ---------------------------------------------------------------------------
# W2: Checkout abandonment signals
# ---------------------------------------------------------------------------


def generate_checkout_abandonment(n: int = 80) -> list[dict]:
    records = []
    for _ in range(n):
        cart_value = round(random.uniform(15, 800), 2)
        minutes_since = random.randint(5, 4320)  # up to 3 days
        prior_purchases = random.randint(0, 20)
        items_available = random.random() > 0.08

        records.append({
            "account_id": _account_id(),
            "session_id": f"sess_{fake.uuid4()[:12]}",
            "cart_value": cart_value,
            "currency": random.choice(["USD", "EUR", "GBP"]),
            "items": [
                {"sku": fake.ean8(), "name": fake.word(), "price": round(random.uniform(5, 200), 2)}
                for _ in range(random.randint(1, 5))
            ],
            "abandoned_at": _rand_dt(days_ago_max=3),
            "time_since_abandonment_minutes": minutes_since,
            "prior_purchase_count": prior_purchases,
            "all_items_available": items_available,
            "customer_email": fake.email() if random.random() > 0.15 else "",
            "customer_phone": fake.phone_number() if random.random() > 0.4 else "",
            "occurred_at": _rand_dt(days_ago_max=3),
            "source": "simulation",
        })
    return records


# ---------------------------------------------------------------------------
# W3: Subscription failure signals
# ---------------------------------------------------------------------------


def generate_subscription_failures(n: int = 70) -> list[dict]:
    DECLINE_CODES = [
        "51", "54", "05", "91", "61",
        "INSUFFICIENT_FUNDS", "EXPIRED_CARD", "DO_NOT_HONOR",
    ]
    PROCESSORS = ["stripe", "braintree", "adyen"]
    PLANS = ["starter_monthly", "pro_monthly", "enterprise_annual", "basic_monthly"]

    records = []
    for _ in range(n):
        amount = round(random.choice([9.99, 19.99, 29.99, 49.99, 99.99, 299.99, 499.99]), 2)
        records.append({
            "account_id": _account_id(),
            "subscription_id": f"sub_{fake.uuid4()[:10]}",
            "plan_name": random.choice(PLANS),
            "amount": amount,
            "currency": "USD",
            "failed_at": _rand_dt(days_ago_max=5),
            "decline_code": random.choice(DECLINE_CODES),
            "processor": random.choice(PROCESSORS),
            "card_last4": str(random.randint(1000, 9999)),
            "card_expiry": _random_expiry(),
            "is_first_failure": random.random() > 0.35,
            "prior_successful_charges": random.randint(0, 36),
            "customer_email": fake.email(),
            "occurred_at": _rand_dt(days_ago_max=5),
            "source": "simulation",
        })
    return records


def _random_expiry() -> str:
    """Return an MM/YY expiry — some expired, most valid."""
    if random.random() < 0.20:  # 20% expired cards
        yr = random.randint(20, 24)
        mo = random.randint(1, 12)
    else:
        yr = random.randint(26, 30)
        mo = random.randint(1, 12)
    return f"{mo:02d}/{yr:02d}"


# ---------------------------------------------------------------------------
# W4: B2B overdue invoice signals
# ---------------------------------------------------------------------------


def generate_b2b_invoices(n: int = 50) -> list[dict]:
    TIERS = ["enterprise", "mid-market", "smb"]
    TERMS = ["net15", "net30", "net60", "net90"]

    records = []
    for _ in range(n):
        amount = round(random.uniform(500, 150000), 2)
        days_overdue = random.choice(
            [random.randint(1, 7)] * 3 +
            [random.randint(8, 30)] * 4 +
            [random.randint(31, 60)] * 2 +
            [random.randint(61, 120)]
        )
        tier = random.choice(TIERS)
        # Enterprise has better payment history
        history_score = round(
            random.uniform(0.6, 1.0) if tier == "enterprise"
            else random.uniform(0.3, 0.9), 2
        )

        records.append({
            "account_id": _account_id(),
            "invoice_id": f"inv_{fake.uuid4()[:10]}",
            "invoice_number": f"INV-{random.randint(10000, 99999)}",
            "amount": amount,
            "currency": random.choice(["USD", "EUR", "GBP"]),
            "due_date": (datetime.utcnow() - timedelta(days=days_overdue)).isoformat(),
            "days_overdue": days_overdue,
            "account_tier": tier,
            "payment_history_score": history_score,
            "has_active_dispute": random.random() < 0.08,
            "contract_terms": random.choice(TERMS),
            "customer_email": fake.company_email(),
            "contact_name": fake.name(),
            "occurred_at": _rand_dt(days_ago_max=1),
            "source": "simulation",
        })
    return records


# ---------------------------------------------------------------------------
# W5: Mandate failure signals
# ---------------------------------------------------------------------------


def generate_mandate_failures(n: int = 60) -> list[dict]:
    MANDATE_TYPES = ["upi", "ach", "direct_debit"]
    NETWORKS = {"upi": "npci", "ach": "nacha", "direct_debit": "bacs"}
    FAILURE_CODES = {
        "upi": ["INSUFFICIENT_FUNDS", "BANK_DOWNTIME", "MANDATE_EXPIRED", "INSUFF_FUNDS"],
        "ach": ["R01", "R02", "R03", "INSUFFICIENT_FUNDS", "BANK_DOWNTIME"],
        "direct_debit": ["INSUFFICIENT_FUNDS", "AUPAY", "BANK_DOWNTIME"],
    }

    records = []
    for _ in range(n):
        mandate_type = random.choice(MANDATE_TYPES)
        currency = "INR" if mandate_type == "upi" else ("USD" if mandate_type == "ach" else "GBP")
        amount = round(random.uniform(100, 5000) if mandate_type == "upi" else random.uniform(50, 2000), 2)
        prior_retries = random.randint(0, 3)
        is_active = prior_retries < 3 and random.random() > 0.15

        records.append({
            "account_id": _account_id(),
            "mandate_id": f"mnd_{fake.uuid4()[:10]}",
            "mandate_type": mandate_type,
            "network": NETWORKS[mandate_type],
            "amount": amount,
            "currency": currency,
            "failed_at": _rand_dt(days_ago_max=4),
            "failure_code": random.choice(FAILURE_CODES[mandate_type]),
            "is_mandate_active": is_active,
            "prior_retry_count": prior_retries,
            "max_retries_allowed": 3,
            "customer_phone": fake.phone_number(),
            "occurred_at": _rand_dt(days_ago_max=4),
            "source": "simulation",
        })
    return records


# ---------------------------------------------------------------------------
# W6: Voice interaction signals
# ---------------------------------------------------------------------------


def generate_voice_interactions(n: int = 40) -> list[dict]:
    CHANNELS = ["ivr", "agent_call", "chat"]
    LANG_PREFS = ["hi-en", "hi-en", "hi-en", "en", "hi"]  # Hinglish dominant

    records = []
    for _ in range(n):
        outstanding = round(random.uniform(500, 50000), 2)
        has_prior_promise = random.random() < 0.35

        records.append({
            "account_id": _account_id(),
            "interaction_id": f"ivr_{fake.uuid4()[:10]}",
            "channel": random.choice(CHANNELS),
            "language_preference": random.choice(LANG_PREFS),
            "outstanding_amount": outstanding,
            "currency": "INR",
            "last_contact_date": _rand_dt(days_ago_max=30) if random.random() > 0.3 else None,
            "previous_promise_date": _rand_dt(days_ago_max=10) if has_prior_promise else None,
            "customer_name": fake.name(),
            "preferred_call_time": random.choice(["morning", "afternoon", "evening", None]),
            "occurred_at": _rand_dt(days_ago_max=2),
            "source": "simulation",
        })
    return records


# ---------------------------------------------------------------------------
# W7: Promise-to-pay signals
# ---------------------------------------------------------------------------


def generate_promises(n: int = 50) -> list[dict]:
    SOURCES = ["agent", "chatbot", "ivr"]

    records = []
    for _ in range(n):
        promised_amount = round(random.uniform(100, 50000), 2)
        days_until_due = random.randint(-30, 14)  # Negative = already broken
        is_broken = days_until_due < 0

        records.append({
            "account_id": _account_id(),
            "ptp_id": f"ptp_{fake.uuid4()[:10]}",
            "captured_from": random.choice(SOURCES),
            "promised_amount": promised_amount,
            "currency": random.choice(["USD", "INR", "GBP"]),
            "promise_date": (datetime.utcnow() + timedelta(days=days_until_due)).isoformat(),
            "days_until_due": days_until_due,
            "is_broken": is_broken,
            "related_invoice_id": f"inv_{fake.uuid4()[:8]}" if random.random() > 0.5 else None,
            "related_subscription_id": f"sub_{fake.uuid4()[:8]}" if random.random() > 0.7 else None,
            "customer_name": fake.name(),
            "customer_email": fake.email(),
            "occurred_at": _rand_dt(days_ago_max=7),
            "source": "simulation",
        })
    return records


# ---------------------------------------------------------------------------
# Payment events for attribution simulation
# ---------------------------------------------------------------------------


def generate_payment_events_for_cases(cases: list[dict], recovery_rate: float = 0.45) -> list[dict]:
    """
    Generate payment events for a set of cases to simulate recovery.
    ~recovery_rate of cases get a successful payment event within attribution window.
    """
    from datetime import timedelta as td
    events = []
    for case in cases:
        if random.random() < recovery_rate:
            offset_hours = random.uniform(0.5, 48)
            events.append({
                "event_id": f"evt_{fake.uuid4()[:10]}",
                "case_id": case.get("case_id"),
                "account_id": case.get("account_id"),
                "amount": case.get("revenue_at_risk", 0.0),
                "currency": "USD",
                "status": "success",
                "processor": "recovery",
                "occurred_at": (
                    datetime.fromisoformat(case.get("opened_at", datetime.utcnow().isoformat()))
                    + td(hours=offset_hours)
                ).isoformat(),
            })
    return events


# ---------------------------------------------------------------------------
# Main: write all sample files
# ---------------------------------------------------------------------------


def generate_all(seed: int = 42) -> None:
    random.seed(seed)
    fake.seed_instance(seed)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    datasets = {
        "payment_degradation": generate_payment_degradation(60),
        "checkout_abandonment": generate_checkout_abandonment(80),
        "subscription_failures": generate_subscription_failures(70),
        "b2b_invoices": generate_b2b_invoices(50),
        "mandate_failures": generate_mandate_failures(60),
        "voice_interactions": generate_voice_interactions(40),
        "promises": generate_promises(50),
    }

    for name, records in datasets.items():
        path = OUTPUT_DIR / f"{name}.jsonl"
        with open(path, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
        print(f"  ✓ {path} — {len(records)} records")

    total = sum(len(v) for v in datasets.values())
    print(f"\nGenerated {total} synthetic records across {len(datasets)} datasets.")


if __name__ == "__main__":
    print("Generating synthetic sample data...")
    generate_all()
