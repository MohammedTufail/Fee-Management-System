"""
generate_bulk_seed.py
----------------------
Generates and bulk-inserts synthetic student records into dbo.Students so the
schema/indexes can be exercised at the 5,000+ record scale required by the
assignment's "Scalability" technical requirement.

Usage:
    # 1. Set connection env vars (same ones shared/db.py expects), e.g.:
    export SQL_CONNECTION_STRING="Driver={ODBC Driver 18 for SQL Server};Server=tcp:<server>.database.windows.net,1433;Database=<db>;Uid=<user>;Pwd=<pwd>;Encrypt=yes;"

    # 2. Run (defaults to adding 5000 rows on top of the 20 seed rows):
    python generate_bulk_seed.py --count 5000

This does NOT touch Administrators, FeeTransactions, or ReminderLog — it only
adds Students rows, in batches, using executemany for throughput.
"""

import argparse
import random
import sys
from datetime import date, timedelta

# Allow running this script directly from the sql/ folder
sys.path.append("..")
from shared.db import get_connection  # noqa: E402

FIRST_NAMES = [
    "Aarav", "Diya", "Kabir", "Ananya", "Vivaan", "Ishaan", "Saanvi", "Reyansh",
    "Myra", "Arjun", "Kiara", "Vihaan", "Anika", "Aditya", "Navya", "Aryan",
    "Riya", "Yash", "Prisha", "Dhruv", "Zara", "Kian", "Meera", "Rohan",
    "Tara", "Advik", "Ira", "Vedant", "Aadhya", "Shaurya",
]
LAST_NAMES = [
    "Sharma", "Patel", "Mehta", "Iyer", "Nair", "Verma", "Reddy", "Gupta",
    "Joshi", "Rao", "Das", "Kapoor", "Menon", "Chawla", "Bhatt", "Malhotra",
    "Sen", "Agarwal", "Khanna", "Pillai", "Bose", "Chatterjee", "Pandey",
]
COURSES = [
    ("B.Tech CSE", 120000), ("B.Tech ECE", 110000), ("MBA", 250000),
    ("B.Sc Physics", 80000), ("B.Tech Mech", 115000), ("BCA", 70000),
    ("B.Com", 60000), ("M.Tech CSE", 180000), ("B.Tech Civil", 105000),
    ("BBA", 65000), ("B.Sc Chemistry", 75000), ("B.Tech IT", 112000),
    ("M.Sc Maths", 90000),
]


def _random_student(idx: int):
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    course, total_fee = random.choice(COURSES)
    # Weighted so we get a realistic mix of paid / partial / overdue
    paid_ratio = random.choices(
        [0.0, random.uniform(0.1, 0.9), 1.0],
        weights=[0.2, 0.5, 0.3],
    )[0]
    paid_amount = round(total_fee * paid_ratio, 2)
    due_date = date(2026, 1, 1) + timedelta(days=random.randint(0, 364))
    email = f"{first.lower()}.{last.lower()}{idx}@example.com"
    return (f"{first} {last}", course, total_fee, paid_amount, due_date, email)


def generate_rows(count: int):
    return [_random_student(i) for i in range(count)]


def bulk_insert(rows, batch_size: int = 500):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.fast_executemany = True
    sql = """
        INSERT INTO dbo.Students (Name, Course, TotalFee, PaidAmount, DueDate, Email)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    inserted = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        cursor.executemany(sql, batch)
        conn.commit()
        inserted += len(batch)
        print(f"  inserted {inserted}/{len(rows)}")
    cursor.close()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Bulk-seed dbo.Students for scale testing")
    parser.add_argument("--count", type=int, default=5000, help="Number of rows to insert")
    parser.add_argument("--batch-size", type=int, default=500, help="Rows per executemany batch")
    args = parser.parse_args()

    print(f"Generating {args.count} synthetic student rows...")
    rows = generate_rows(args.count)
    print("Inserting into dbo.Students in batches...")
    bulk_insert(rows, args.batch_size)
    print("Done.")


if __name__ == "__main__":
    main()
