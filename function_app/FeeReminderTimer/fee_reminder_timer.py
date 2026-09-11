"""
FeeReminderTimer Azure Function (Task 2 — Automation)

Timer-triggered, NCRONTAB schedule (default: daily at 08:00 UTC — override
with the REMINDER_CRON_SCHEDULE app setting rather than editing code).

On each run:
  1. Query every student who is Overdue — PaidAmount < TotalFee AND
     DueDate < today (same definition as shared/fee_logic.compute_status,
     re-expressed directly in SQL here since we're already scanning the
     whole table).
  2. For each, check ReminderLog for an existing Status='Sent' row for that
     StudentID sent TODAY. If found, skip — this is what makes re-runs /
     retries idempotent within a day (per the assignment's requirement
     that the job "won't double-send"), while still allowing a fresh
     reminder on each subsequent day the student remains overdue, which is
     what "automate reminders for pending dues" actually implies.
  3. Otherwise, send one reminder via SendGrid (shared/email.py) and log
     the outcome — Status='Sent' or Status='Failed' — to ReminderLog.
     A Failed row does NOT block a retry later the same day; only a Sent
     row does. This means a transient SendGrid outage self-heals on the
     next attempt instead of silently giving up forever.

Each student's send + log is committed individually (not batched into one
transaction for the whole run). With thousands of overdue students and a
real network call to SendGrid per student, a single end-of-run commit would
mean (a) nothing is visible in ReminderLog until the entire run finishes —
which can take minutes — and (b) a crash partway through loses every
row processed so far, including ones that were genuinely sent. Committing
per-student means a crash at student #1,200 leaves the first 1,199
correctly logged, and — combined with the "already sent today" check above
— the next run picks up exactly where this one left off instead of
resending to everyone.

ACTUAL SCHEMA (sql/schema_and_seed.sql):
  - Students.Email already exists — no patch needed.
  - ReminderLog columns: ReminderID, StudentID, SentAt, Status. There is
    no DueDate or Note column, so the idempotency check and the log write
    below are keyed on (StudentID, "sent today") rather than
    (StudentID, DueDate) — Students only ever has one DueDate on file per
    student at a time anyway, so this is equivalent in practice. Any
    send-failure detail goes to the Function's own logs (Application
    Insights), not to the DB.

Requires AzureWebJobsStorage pointed at a real storage account or Azurite —
Timer triggers use it to persist their schedule state. HTTP-only Part 2
could get away without this; Part 3 cannot.
"""
import logging
import os
import sys
import datetime

import azure.functions as func

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.db import get_connection
from shared.email import send_reminder_email

bp = func.Blueprint()

# NCRONTAB: {second} {minute} {hour} {day} {month} {day-of-week}
# Default: every day at 08:00 UTC.
REMINDER_SCHEDULE = os.environ.get("REMINDER_CRON_SCHEDULE", "0 0 8 * * *")


@bp.timer_trigger(schedule=REMINDER_SCHEDULE, arg_name="mytimer",
                   run_on_startup=False, use_monitor=True)
def FeeReminderTimer(mytimer: func.TimerRequest) -> None:
    if mytimer.past_due:
        logging.warning("FeeReminderTimer is running late (past_due=True).")

    run_started = datetime.datetime.utcnow()
    logging.info("FeeReminderTimer run started at %s", run_started.isoformat())

    conn = get_connection()
    sent_count = 0
    skipped_count = 0
    failed_count = 0

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT StudentID, Name, Course, TotalFee, PaidAmount, DueDate, Email
            FROM Students
            WHERE PaidAmount < TotalFee
              AND DueDate < CAST(GETDATE() AS DATE)
            """
        )
        overdue_students = cursor.fetchall()
        logging.info("FeeReminderTimer found %d overdue student(s).", len(overdue_students))

        for (student_id, name, course, total_fee, paid_amount, due_date, email) in overdue_students:
            check_cursor = conn.cursor()
            check_cursor.execute(
                """
                SELECT 1 FROM ReminderLog
                WHERE StudentID = ? AND Status = 'Sent'
                  AND CAST(SentAt AS DATE) = CAST(GETDATE() AS DATE)
                """,
                student_id,
            )
            if check_cursor.fetchone():
                skipped_count += 1
                continue

            if not email:
                logging.warning(
                    "StudentID=%s has no Email on file; logging as Failed and skipping send.",
                    student_id,
                )
                _log_reminder(conn, student_id, "Failed")
                conn.commit()
                failed_count += 1
                continue

            balance = round(float(total_fee) - float(paid_amount), 2)

            try:
                send_reminder_email(
                    to_email=email,
                    name=name,
                    course=course,
                    total_fee=float(total_fee),
                    paid_amount=float(paid_amount),
                    balance=balance,
                    due_date=due_date,
                )
                _log_reminder(conn, student_id, "Sent")
                conn.commit()
                sent_count += 1
                logging.info("Reminder sent to StudentID=%s (%s)", student_id, email)

            except Exception:
                logging.exception("Failed to send reminder to StudentID=%s", student_id)
                _log_reminder(conn, student_id, "Failed")
                conn.commit()
                failed_count += 1

    except Exception:
        conn.rollback()
        logging.exception("FeeReminderTimer run failed")
        # Re-raise so the Functions host records this invocation as failed —
        # that's what makes host.json's retry policy and any Application
        # Insights failure-rate alert actually fire.
        raise

    finally:
        conn.close()

    logging.info(
        "FeeReminderTimer run complete: sent=%d skipped=%d failed=%d",
        sent_count, skipped_count, failed_count,
    )


def _log_reminder(conn, student_id, status):
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO ReminderLog (StudentID, SentAt, Status)
        VALUES (?, ?, ?)
        """,
        student_id, datetime.datetime.utcnow(), status,
    )