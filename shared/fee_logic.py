"""
fee_logic.py
------------
Pure business logic for turning a student's TotalFee / PaidAmount / DueDate
into one of the three statuses the assignment requires: "Paid",
"Partially Paid", "Overdue". Kept separate from the Functions themselves so
it can be unit tested without a DB connection or the Functions runtime.

IMPORTANT: admin_query.py re-implements this SAME logic as a SQL CASE
expression (STATUS_EXPR) so status filtering can happen server-side for
pagination at scale. If you ever change the rules here, update that SQL
expression too — see the comment there.
"""

from datetime import date, datetime


def compute_status(total_fee, paid_amount, due_date, as_of=None) -> str:
    """
    Rules (per assignment spec — only these 3 values are valid):
      - "Paid":            paid_amount >= total_fee
      - "Overdue":         due date has passed and not fully paid
      - "Partially Paid":  not fully paid, due date not yet passed
                            (this also covers PaidAmount == 0 pre-due-date,
                            since the assignment only defines 3 statuses)
    """
    as_of = as_of or date.today()
    if isinstance(due_date, datetime):
        due_date = due_date.date()

    if float(paid_amount) >= float(total_fee):
        return "Paid"
    if due_date < as_of:
        return "Overdue"
    return "Partially Paid"


def balance_due(total_fee, paid_amount) -> float:
    return round(float(total_fee) - float(paid_amount), 2)
