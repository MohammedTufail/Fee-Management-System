"""
ListStudents Azure Function (admin query endpoint)

    GET /api/fee/students?course=&status=&page=&pageSize=

Admin-only (`@require_role("Admin")`). Lets an administrator search/browse
fee details across all students rather than looking one up at a time.

Query params (all optional):
    course    - exact match on Students.Course
    status    - "Paid" | "Partially Paid" | "Overdue" (computed, same rule
                as PaymentStatus — see shared/fee_logic.py)
    page      - 1-based page number, default 1
    pageSize  - rows per page, default 50, max 200

Response body:
    { "page": 1, "pageSize": 50, "total": 137, "students": [ {...}, ... ] }

Note on scale: filtering by `course` is pushed into SQL. Filtering by the
computed `status` currently happens in Python after the course filter runs,
which is fine at the 5,000-row scale this assignment targets (a single
in-memory pass over at most a few thousand rows). If this ever needs to
scale well past that, move the status logic into a SQL CASE expression in
the WHERE clause instead — see the comment in shared/fee_logic.py.
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


@bp.route(route="fee/students", methods=["GET"])
@require_role("Admin")
def ListStudents(req: func.HttpRequest) -> func.HttpResponse:
    course_filter = req.params.get("course")
    status_filter = req.params.get("status")

    try:
        page = max(1, int(req.params.get("page", 1)))
    except ValueError:
        page = 1
    try:
        page_size = min(200, max(1, int(req.params.get("pageSize", 50))))
    except ValueError:
        page_size = 50

    conn = get_connection()
    try:
        cursor = conn.cursor()

        query = "SELECT StudentID, Name, Course, TotalFee, PaidAmount, DueDate FROM Students"
        params = []
        if course_filter:
            query += " WHERE Course = ?"
            params.append(course_filter)
        query += " ORDER BY StudentID"

        cursor.execute(query, *params)
        rows = cursor.fetchall()

        students = []
        for (student_id, name, course, total_fee, paid_amount, due_date) in rows:
            total_fee = float(total_fee)
            paid_amount = float(paid_amount)
            status = compute_status(total_fee, paid_amount, due_date)
            if status_filter and status.lower() != status_filter.lower():
                continue
            students.append({
                "studentId": student_id,
                "name": name,
                "course": course,
                "totalFee": total_fee,
                "paidAmount": paid_amount,
                "balance": balance_due(total_fee, paid_amount),
                "dueDate": due_date.isoformat() if due_date else None,
                "status": status,
            })

        total = len(students)
        start = (page - 1) * page_size
        page_students = students[start:start + page_size]

        return func.HttpResponse(
            json.dumps({
                "page": page,
                "pageSize": page_size,
                "total": total,
                "students": page_students,
            }),
            status_code=200,
            mimetype="application/json",
        )

    except Exception:
        logging.exception("ListStudents failed")
        return func.HttpResponse(
            json.dumps({"error": "Internal server error"}),
            status_code=500,
            mimetype="application/json",
        )
    finally:
        conn.close()
