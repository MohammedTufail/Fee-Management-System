"""
UpdateFeeRecord Azure Function (Task 4)

    POST/PATCH /api/fee/update/{studentId}
    Body: { "paidAmount": 25000.0, "note": "optional free-text reason" }

FeeTransactions columns (see sql/schema_and_seed.sql):
    StudentID, ChangedBy, PreviousPaid, NewPaid, Note, CreatedAt (default)

Response body (200):
    {
      "studentId": 1,
      "PreviousPaid": 20000.0,
      "NewPaid": 25000.0,
      "changedBy": "admin@tenant.onmicrosoft.com"
    }
"""
import json
import logging
import os
import sys

import azure.functions as func

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.db import get_connection
from shared.auth import require_role

bp = func.Blueprint()


@bp.route(route="fee/update/{studentId:int}", methods=["POST", "PATCH"])
@require_role("Admin")
def UpdateFeeRecord(req: func.HttpRequest) -> func.HttpResponse:
    claims = req._claims

    student_id = req.route_params.get("studentId")

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Request body must be valid JSON."}),
            status_code=400,
            mimetype="application/json",
        )

    if not body or "paidAmount" not in body:
        return func.HttpResponse(
            json.dumps({"error": "Body must include 'paidAmount'."}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        new_paid = round(float(body["paidAmount"]), 2)
    except (TypeError, ValueError):
        return func.HttpResponse(
            json.dumps({"error": "'paidAmount' must be numeric."}),
            status_code=400,
            mimetype="application/json",
        )

    if new_paid < 0:
        return func.HttpResponse(
            json.dumps({"error": "'paidAmount' cannot be negative."}),
            status_code=400,
            mimetype="application/json",
        )

    note = body.get("note")
    changed_by = claims.get("preferred_username") or claims.get("upn") or claims.get("oid") or "unknown-admin"

    conn = get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT PaidAmount, TotalFee FROM Students WHERE StudentID = ?",
            student_id,
        )
        row = cursor.fetchone()
        if row is None:
            return func.HttpResponse(
                json.dumps({"error": f"No student found with StudentID {student_id}"}),
                status_code=404,
                mimetype="application/json",
            )

        previous_paid, total_fee = float(row[0]), float(row[1])

        if new_paid > total_fee:
            return func.HttpResponse(
                json.dumps({
                    "error": f"paidAmount ({new_paid}) cannot exceed TotalFee ({total_fee})."
                }),
                status_code=400,
                mimetype="application/json",
            )

        cursor.execute(
            """
            UPDATE Students
            SET PaidAmount = ?,
                UpdatedAt = SYSUTCDATETIME()
            WHERE StudentID = ?
            """,
            new_paid,
            student_id,
        )

        cursor.execute(
            """
            INSERT INTO FeeTransactions
                (StudentID, ChangedBy, PreviousPaid, NewPaid, Note)
            VALUES (?, ?, ?, ?, ?)
            """,
            student_id,
            changed_by,
            previous_paid,
            new_paid,
            note,
        )

        conn.commit()

        logging.info(
            "UpdateFeeRecord: StudentID=%s %.2f -> %.2f by %s",
            student_id, previous_paid, new_paid, changed_by,
        )

        return func.HttpResponse(
            json.dumps({
                "studentId": int(student_id),
                "PreviousPaid": previous_paid,
                "NewPaid": new_paid,
                "changedBy": changed_by,
            }),
            status_code=200,
            mimetype="application/json",
        )

    except Exception:
        conn.rollback()
        logging.exception("UpdateFeeRecord failed for StudentID=%s", student_id)
        return func.HttpResponse(
            json.dumps({"error": "Internal server error"}),
            status_code=500,
            mimetype="application/json",
        )
    finally:
        conn.close()
