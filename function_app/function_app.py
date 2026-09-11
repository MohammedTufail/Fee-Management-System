"""
function_app.py — Azure Functions Python v2 programming model entry point.

Wires together every blueprint across all 3 parts of this assignment:

    PaymentStatus/      -> Task 3: GET fee status by StudentID
    ListStudents/       -> Task 3 (admin query support): paged/filterable list
    UpdateFeeRecord/    -> Task 4: admin-only secure update + audit trail
    FeeReminderTimer/   -> Task 2: daily overdue-fee email reminders (Part 3)

Only the FeeReminderTimer import/registration is new in Part 3 — the three
HTTP blueprints above are unchanged from Part 2.

http_auth_level is ANONYMOUS at the Functions-host level because
authentication/authorization is handled explicitly in code via
`shared.auth.require_role(...)`, which validates the Azure AD JWT itself.
In production this sits behind APIM (see ../apim/apim-policy.xml), which
adds the subscription-key requirement and rate limiting in front of it.
"""
import azure.functions as func

from PaymentStatus.payment_status import bp as payment_status_bp
from ListStudents.list_students import bp as list_students_bp
from UpdateFeeRecord.update_fee_record import bp as update_fee_record_bp
from FeeReminderTimer.fee_reminder_timer import bp as fee_reminder_timer_bp

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

app.register_functions(payment_status_bp)
app.register_functions(list_students_bp)
app.register_functions(update_fee_record_bp)
app.register_functions(fee_reminder_timer_bp)