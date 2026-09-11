"""
PaymentStatus Azure Function (Task 3)

    GET /api/fee/status/{studentId}

Access:
    - Admin   : may view any student's fee status.
    - Student : may only view their OWN record — enforced by comparing
                Students.AzureADObjectId for the requested StudentID against
                the caller's `oid` claim from their AAD token. A student
                requesting someone else's StudentID gets 403, not their own
                data with a warning.

Response body:
    {
      "studentId": 1,
      "name": "...",
      "course": "...",
      "totalFee": 50000.0,
      "paidAmount": 20000.0,
      "balance": 30000.0,
      "dueDate": "2026-08-15",
      "status": "Paid" | "Partially Paid" | "Overdue"
    }

Status computation lives in shared/fee_logic.py (compute_status /
balance_due) so this endpoint and ListStudents can never disagree about
what counts as Paid/Partially Paid/Overdue.
"""
import json
import logging
import os
import sys

import azure.functions as func

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.db import get_connection
from shared.auth import require_role
from shared.fee_logic import compute_status, balance_due

bp = func.Blueprint()


@bp.route(route="fee/status/{studentId:int}", methods=["GET"])
@require_role("Student", "Admin")
def PaymentStatus(req: func.HttpRequest) -> func.HttpResponse:
    claims = req._claims

    student_id = req.route_params.get("studentId")
    roles = claims.get("roles", []) or []
    caller_oid = claims.get("oid")

    logging.info("PaymentStatus requested for StudentID=%s by oid=%s", student_id, caller_oid)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT StudentID, Name, Course, TotalFee, PaidAmount, DueDate, AzureADObjectId
            FROM Students
            WHERE StudentID = ?
            """,
            student_id,
        )
        row = cursor.fetchone()

        if row is None:
            return func.HttpResponse(
                json.dumps({"error": f"No student found with StudentID {student_id}"}),
                status_code=404,
                mimetype="application/json",
            )

        (db_student_id, name, course, total_fee, paid_amount, due_date, azure_ad_object_id) = row

        if "Admin" not in roles:
            if not azure_ad_object_id or str(azure_ad_object_id).lower() != str(caller_oid).lower():
                logging.warning(
                    "Student oid=%s attempted to access StudentID=%s belonging to a different identity",
                    caller_oid, student_id,
                )
                return func.HttpResponse(
                    json.dumps({"error": "Forbidden: you may only view your own fee record."}),
                    status_code=403,
                    mimetype="application/json",
                )

        total_fee = float(total_fee)
        paid_amount = float(paid_amount)
        status = compute_status(total_fee, paid_amount, due_date)

        payload = {
            "studentId": db_student_id,
            "name": name,
            "course": course,
            "totalFee": total_fee,
            "paidAmount": paid_amount,
            "balance": balance_due(total_fee, paid_amount),
            "dueDate": due_date.isoformat() if due_date else None,
            "status": status,
        }

        return func.HttpResponse(json.dumps(payload), status_code=200, mimetype="application/json")

    except Exception:
        logging.exception("PaymentStatus failed for StudentID=%s", student_id)
        return func.HttpResponse(
            json.dumps({"error": "Internal server error"}),
            status_code=500,
            mimetype="application/json",
        )
    finally:
        conn.close()
 