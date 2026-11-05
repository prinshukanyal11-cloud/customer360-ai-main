"""Generate realistic synthetic customer transaction data for local dev/demo.

Output: data/sample/customers.csv and data/sample/transactions.csv.

IMPORTANT: outputs are synthetic/demo data and must never be presented as
real business results. Set DEMO_MODE=true + ENABLE_SAMPLE_DATA=true to load.

Usage: python scripts/generate_sample_data.py [--customers 500] [--seed 42]
"""
from __future__ import annotations

import argparse
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

CITIES = [
    ("Mumbai", "Maharashtra"), ("Delhi", "Delhi"), ("Bengaluru", "Karnataka"),
    ("Hyderabad", "Telangana"), ("Ahmedabad", "Gujarat"), ("Chennai", "Tamil Nadu"),
    ("Kolkata", "West Bengal"), ("Pune", "Maharashtra"), ("Jaipur", "Rajasthan"),
    ("Lucknow", "Uttar Pradesh"), ("Chandigarh", "Punjab"), ("Gurugram", "Haryana"),
    ("Indore", "Madhya Pradesh"), ("Kochi", "Kerala"), ("Bhubaneswar", "Odisha"),
    ("Guwahati", "Assam"),
]
CITIES = list(dict.fromkeys(CITIES))

GENDERS = ("Male", "Female", "Other")
CHANNELS = ("web", "app", "store", "phone")
CATEGORIES = {
    "Electronics": ("Mobile", "Laptop", "Headphones", "Smartwatch", "Camera"),
    "Fashion": ("T-Shirt", "Jeans", "Sneakers", "Jacket", "Dress"),
    "Home & Kitchen": ("Blender", "Cookware", "Bedding", "Lamp", "Cutlery"),
    "Beauty": ("Skincare", "Fragrance", "Cosmetics", "Haircare"),
    "Groceries": ("Snacks", "Beverages", "Pantry", "Dairy", "Produce"),
}
CHANNEL_WEIGHTS = [0.5, 0.3, 0.12, 0.08]


def weighted_category() -> tuple[str, str]:
    cat = random.choice(list(CATEGORIES.keys()))
    return cat, random.choice(CATEGORIES[cat])


def make_customer_archetype(rng: random.Random) -> dict:
    """Give customers different behaviors so segments are meaningful (synthetic)."""
    archetype = rng.choices(["loyal", "active", "at_risk", "new", "dormant"], weights=[0.18, 0.22, 0.22, 0.15, 0.23])[0]
    if archetype == "loyal":
        frequency = rng.randint(8, 25)
        last_days = rng.randint(0, 30)
        signup_months = rng.randint(10, 36)
    elif archetype == "active":
        frequency = rng.randint(3, 8)
        last_days = rng.randint(0, 45)
        signup_months = rng.randint(4, 18)
    elif archetype == "at_risk":
        frequency = rng.randint(1, 3)
        last_days = rng.randint(50, 140)
        signup_months = rng.randint(3, 14)
    elif archetype == "new":
        frequency = rng.randint(1, 2)
        last_days = rng.randint(0, 25)
        signup_months = rng.randint(0, 2)
    else:  # dormant
        frequency = rng.randint(1, 3)
        last_days = rng.randint(150, 365)
        signup_months = rng.randint(6, 30)
    avg_order = rng.randint(800, 2600)
    return {
        "archetype": archetype,
        "frequency": frequency,
        "last_days": last_days,
        "signup_months": signup_months,
        "avg_order": avg_order,
    }


def generate(customers: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = random.Random(seed)
    today = datetime.now(UTC).date()

    customer_rows: list[dict] = []
    transaction_rows: list[dict] = []
    first_names = ["Aarav", "Vivaan", "Diya", "Aditya", "Ananya", "Rohan", "Ishita", "Kabir", "Meera", "Arjun",
                   "Saanvi", "Rahul", "Priya", "Nikhil", "Kavya", "Advait", "Sara", "Aryan", "Anjali", "Vikram"]
    last_names = ["Sharma", "Verma", "Gupta", "Reddy", "Nair", "Iyer", "Patel", "Singh", "Kumar", "Rao",
                  "Kapoor", "Bhat", "Menon", "Das", "Chopra", "Malhotra", "Joshi", "Mehta", "Agrawal", "Kaur"]

    for i in range(1, customers + 1):
        profile = make_customer_archetype(rng)
        city, state = rng.choice(CITIES)
        name = f"{rng.choice(first_names)} {rng.choice(last_names)}"
        gender = rng.choices(GENDERS, weights=[0.5, 0.47, 0.03])[0]
        email = f"{name.lower().replace(' ', '.')}{i}@example.com"
        signup_date = today - timedelta(days=rng.randint(25, 60) + profile["signup_months"] * 30)
        channel_spread = rng.choices(CHANNELS, weights=CHANNEL_WEIGHTS)[0]
        cid = f"CUST{i:05d}"

        customer_rows.append({
            "customer_id": cid,
            "name": name,
            "email": email,
            "city": city,
            "state": state,
            "age": rng.randint(18, 72),
            "gender": gender,
            "signup_date": signup_date.isoformat(),
            "acquisition_channel": channel_spread,
        })

        for order_no in range(1, profile["frequency"] + 1):
            gap = rng.randint(12, 45)
            order_date = signup_date + timedelta(days=(profile["signup_months"] * 30) // profile["frequency"] * (order_no - 1) + rng.randint(0, gap))
            order_date = min(order_date, today - timedelta(days=profile["last_days"]))
            amount = max(199, int(rng.gauss(profile["avg_order"], profile["avg_order"] * 0.35)))
            discount = round(rng.choice([0.0, 0.0, 0.05, 0.1, 0.15]), 2)
            discount = round(amount * discount, 2)
            category, product = weighted_category()
            transaction_rows.append({
                "order_id": f"ORD{len(transaction_rows) + 1:06d}",
                "customer_id": cid,
                "order_date": order_date.isoformat(),
                "product_name": product,
                "category": category,
                "quantity": rng.randint(1, 3),
                "amount": amount,
                "discount": discount,
                "channel": rng.choices(CHANNELS, weights=CHANNEL_WEIGHTS)[0],
            })

    customers_df = pd.DataFrame(customer_rows)
    transactions_df = pd.DataFrame(transaction_rows)
    return customers_df, transactions_df


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic demo data.")
    parser.add_argument("--customers", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    out = Path(__file__).resolve().parent.parent / "data" / "sample"
    out.mkdir(parents=True, exist_ok=True)

    customers_df, transactions_df = generate(args.customers, args.seed)
    customers_df.to_csv(out / "customers.csv", index=False)
    transactions_df.to_csv(out / "transactions.csv", index=False)

    print(f"wrote {out / 'customers.csv'} ({len(customers_df)} rows)")
    print(f"wrote {out / 'transactions.csv'} ({len(transactions_df)} rows)")


if __name__ == "__main__":
    main()