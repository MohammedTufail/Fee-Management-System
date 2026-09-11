"""
email.py
--------
Thin wrapper around the SendGrid Web API for sending fee-due reminder
emails. Kept separate from FeeReminderTimer so the send logic (or the
provider itself — swap for Outlook/Graph later) can be changed or unit
tested without touching the timer's control flow, idempotency checks, or
logging.

Required app settings (see local.settings.json.example):
    SENDGRID_API_KEY    - SendGrid API key
    SENDGRID_FROM_EMAIL - a sender address verified in your SendGrid account
                           (Single Sender Verification or a verified domain)

Add to requirements.txt:  sendgrid
"""
import os

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
SENDGRID_FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL", "")


def send_reminder_email(to_email: str, name: str, course: str, total_fee: float,
                         paid_amount: float, balance: float, due_date) -> None:
    """
    Sends a single fee-due reminder. Raises on any failure — the caller
    (FeeReminderTimer) is responsible for catching that, logging it to
    ReminderLog as Status='Failed', and letting the next scheduled run
    retry it (a Failed row does not block a retry; only Status='Sent' does).
    """
    if not SENDGRID_API_KEY or not SENDGRID_FROM_EMAIL:
        raise RuntimeError("SENDGRID_API_KEY / SENDGRID_FROM_EMAIL not configured.")

    due_str = due_date.isoformat() if hasattr(due_date, "isoformat") else str(due_date)

    subject = f"Fee payment reminder — {course}"
    body = (
        f"Dear {name},\n\n"
        f"This is a reminder that your fee payment for {course} was due on "
        f"{due_str} and has not yet been fully paid.\n\n"
        f"  Total Fee:   {total_fee:.2f}\n"
        f"  Paid so far: {paid_amount:.2f}\n"
        f"  Balance due: {balance:.2f}\n\n"
        f"Please arrange payment at your earliest convenience. If you have "
        f"already paid, please disregard this message.\n\n"
        f"Regards,\nFee Management Office"
    )

    message = Mail(
        from_email=SENDGRID_FROM_EMAIL,
        to_emails=to_email,
        subject=subject,
        plain_text_content=body,
    )

    client = SendGridAPIClient(SENDGRID_API_KEY)
    response = client.send(message)

    if response.status_code >= 300:
        raise RuntimeError(f"SendGrid returned status {response.status_code}: {response.body}")