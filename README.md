# Fee Management System — Azure

A cloud-native fee management system for students and administrators,
built entirely on Azure serverless services: Azure SQL Database for
storage, Azure Functions (Python v2 model) for the API and automation
logic, Azure AD (Entra ID) for authentication and RBAC, API Management
for the public-facing API surface, and SendGrid for reminder emails.

Deployed and verified against a live Azure subscription — not just run
locally. See [Deployment notes — real issues hit and fixed](#deployment-notes--real-issues-hit-and-fixed)
for the exact problems encountered along the way.

---

## Contents

- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Database design (Task 1)](#database-design-task-1)
- [How each task maps to the code](#how-each-task-maps-to-the-code)
- [Local setup](#local-setup)
- [Step-by-step deployment guide](#step-by-step-deployment-guide)
- [Configuration reference](#configuration-reference)
- [Testing guide](#testing-guide)
- [Monitoring & alerts (Task 5)](#monitoring--alerts-task-5)
- [Deployment notes — real issues hit and fixed](#deployment-notes--real-issues-hit-and-fixed)
- [Assignment requirement coverage](#assignment-requirement-coverage)

---

## Architecture

```
                    ┌─────────────────────┐
                    │   Azure AD (Entra)   │
                    │  App roles: Student, │
                    │       Admin          │
                    └──────────┬───────────┘
                               │ issues JWTs
              ┌────────────────┴────────────────┐
              │                                  │
     ┌────────▼────────┐               ┌─────────▼────────┐
     │  Student client  │               │   Admin client    │
     └────────┬────────┘               └─────────┬────────┘
              │  Bearer token + subscription key   │
              └────────────────┬────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │   API Management       │
                    │  validate-jwt +        │
                    │  rate-limit + quota +  │
                    │  subscription key      │
                    └───────────┬───────────┘
                                │
                    ┌───────────▼────────────────────────┐
                    │   Azure Function App (Python v2)     │
                    │                                       │
                    │  PaymentStatus   ListStudents         │
                    │  UpdateFeeRecord FeeReminderTimer     │
                    │                                       │
                    │  shared/auth.py   — AAD JWT + RBAC    │
                    │  shared/fee_logic.py — status rules   │
                    │  shared/db.py     — SQL connection    │
                    │  shared/email.py  — SendGrid          │
                    └───────────┬────────────┬─────────────┘
                                │            │
                    ┌───────────▼──┐   ┌─────▼──────┐
                    │  Azure SQL    │   │  SendGrid   │
                    │  Database     │   │  (email)    │
                    └───────────────┘   └─────────────┘
                                │
                    ┌───────────▼───────────┐
                    │  Application Insights   │
                    │  logs, failures, alerts │
                    └────────────────────────┘
```

**Request flow — student checks their own fee status**
1. Student signs in via Azure AD, gets a JWT with `roles: ["Student"]`.
2. Client calls `GET /fee/status/{studentId}`, with `Authorization: Bearer
   <token>` (and `Ocp-Apim-Subscription-Key` if going through APIM).
3. `shared/auth.require_role("Student", "Admin")` validates the token
   (signature, issuer, audience) and checks the role.
4. `PaymentStatus` additionally checks `Students.AzureADObjectId` for the
   requested `StudentID` matches the caller's own `oid` claim — a student
   can only ever see their own record, not anyone else's by guessing an ID.
5. Status is computed by `shared/fee_logic.compute_status` and returned.

**Request flow — admin updates a fee record**
1. Admin's token must carry the `Admin` role.
2. `UpdateFeeRecord` reads the current `PaidAmount`, validates the new
   value (can't be negative, can't exceed `TotalFee`), then in **one SQL
   transaction**: updates `Students.PaidAmount` and inserts an audit row
   into `FeeTransactions` (`PreviousPaid`, `NewPaid`, `ChangedBy`,
   `CreatedAt`). Commit or rollback together — never a partial write.

**Automation flow — daily reminders**
1. `FeeReminderTimer` fires on a schedule (default: daily, 08:00 UTC) —
   no client involved.
2. Queries `Students` for anyone Overdue (`PaidAmount < TotalFee AND
   DueDate < today`).
3. For each, checks `ReminderLog` for a `Status='Sent'` row for that
   student **already sent today** — skips if found, so re-runs/retries
   never double-email the same student on the same day.
4. Sends via SendGrid, logs `Sent` or `Failed` to `ReminderLog` —
   committed **per student**, not batched into one end-of-run transaction,
   so a crash partway through only loses unprocessed students, not
   everything already sent.

---

## Repository layout

```
fee-management-system/
├── README.md
├── requirements.txt
├── sql/
│   ├── schema_and_seed.sql        # Students, Administrators, FeeTransactions,
│   │                               # ReminderLog + 20 seed students, 2 admins
│   └── generate_bulk_seed.py      # bulk-inserts synthetic students (tested at 5,020 rows)
├── shared/
│   ├── db.py                      # Azure SQL connection (SQL auth / Managed Identity)
│   ├── auth.py                    # AAD JWT validation + @require_role RBAC decorator
│   ├── fee_logic.py                # single source of truth for Paid/Partially Paid/Overdue
│   └── email.py                    # SendGrid wrapper
├── function_app/
│   ├── function_app.py             # entry point — registers every blueprint
│   ├── host.json                   # retry policy + Application Insights sampling
│   ├── local.settings.json.example
│   ├── PaymentStatus/
│   │   └── payment_status.py       # Task 3 — GET fee status by StudentID
│   ├── ListStudents/
│   │   └── list_students.py        # Admin search/browse endpoint
│   ├── UpdateFeeRecord/
│   │   └── update_fee_record.py    # Task 4 — admin-only secure update
│   └── FeeReminderTimer/
│       └── fee_reminder_timer.py   # Task 2 — daily reminder automation
├── apim/
│   └── apim-policy.xml             # Task 3 — validate-jwt + rate limiting + quota
└── docs/
    ├── architecture.md
    └── deployment_guide.md
```

---

## Database design (Task 1)

Four tables in Azure SQL Database (`sql/schema_and_seed.sql`):

**`Students`** — `StudentID` (PK, identity), `Name`, `Course`, `TotalFee`,
`PaidAmount`, `DueDate`, `Email`, `AzureADObjectId` (nullable —
populated once a student signs in for real, maps them to their AAD
identity), `CreatedAt`, `UpdatedAt`. Indexed on `DueDate` (covering
`PaidAmount`/`TotalFee` — supports the overdue query used by both
`ListStudents` and `FeeReminderTimer` without a table scan) and on
`AzureADObjectId` (supports the per-student ownership check in
`PaymentStatus`).

**`Administrators`** — `AdminID` (PK), `Name`, `Role`, `Email`,
`AzureADObjectId`.

**`FeeTransactions`** — append-only audit log for every admin update:
`TransactionID` (PK), `StudentID` (FK), `ChangedBy`, `PreviousPaid`,
`NewPaid`, `Note`, `CreatedAt`. Written by `UpdateFeeRecord` in the same
transaction as the `Students` update it's auditing.

**`ReminderLog`** — idempotency log for the daily reminder job:
`ReminderID` (PK), `StudentID` (FK), `SentAt`, `Status` (`Sent`/`Failed`).
Written by `FeeReminderTimer`, one row per student per day it attempts a
send.

Seeded with 20 students (a deliberate mix of fully paid, partially paid,
unpaid, and overdue-by-due-date) and 2 administrators.
`sql/generate_bulk_seed.py` bulk-inserts synthetic rows via
`executemany` batching — **live-tested at 5,020 total students**,
confirming the schema/indexes hold up at the scale the brief requires.

---

## How each task maps to the code

**Task 1 — Data Storage.** `sql/schema_and_seed.sql` (schema + 20 seed
students + 2 admins), `sql/generate_bulk_seed.py` (scale test, 5,020
rows confirmed).

**Task 2 — Automation.** `function_app/FeeReminderTimer/fee_reminder_timer.py`
— a Timer-triggered Azure Function (the brief allows Logic Apps *or*
Durable Functions/Functions automation; a plain Timer Trigger was chosen
to keep the whole system in one Python codebase and reuse `shared/db.py`
and `shared/fee_logic.py` directly instead of re-implementing the overdue
rule as a Logic App expression). Queries overdue students, sends via
`shared/email.py` (SendGrid), and logs every attempt to `ReminderLog` so
re-runs don't double-send. Retry policy for failed invocations is
configured in `function_app/host.json`.

**Task 3 — Payment Status API.** `function_app/PaymentStatus/payment_status.py`
returns exactly the three statuses the brief specifies — Paid, Partially
Paid, Overdue — computed by `shared/fee_logic.compute_status` so this
endpoint and `ListStudents` can never disagree. Secured at two layers:
`apim/apim-policy.xml` (API key + rate limiting + quota, enforced by API
Management) and `@require_role` in code (defense in depth — the Function
is still safe even if called directly, bypassing APIM).
`function_app/ListStudents/list_students.py` is an added admin-only query
endpoint (search/filter/paginate across all students) supporting the
"administrators can query student fee details" requirement beyond a
single lookup.

**Task 4 — Secure Updates for Administrators.**
`function_app/UpdateFeeRecord/update_fee_record.py`, secured by
`@require_role("Admin")` in `shared/auth.py`, which validates the Azure
AD JWT itself (signature against the tenant's JWKS, audience, issuer) and
checks the `roles` claim — this is genuine token validation in code, not
just a platform-level gate. Every update writes a `FeeTransactions` audit
row in the same transaction as the balance change.

**Task 5 — Scalability and Monitoring.** `function_app/host.json` wires
Application Insights (with sampling, to control cost at the "5,000+
records" scale) and a retry policy. All hot-path queries use the indexes
from Task 1 rather than full scans. See
[Monitoring & alerts](#monitoring--alerts-task-5) for the KQL queries and
alert rules used on top of this.

---

## Local setup

```bash
git clone <this-repo>
cd fee-management-system

python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS/Linux

pip install -r requirements.txt

# Storage emulator — Timer triggers need this even locally
npm install -g azurite
azurite &

cd function_app
cp local.settings.json.example local.settings.json
# fill in SQL_CONNECTION_STRING, AAD_TENANT_ID, AAD_API_AUDIENCE,
# SENDGRID_API_KEY, SENDGRID_FROM_EMAIL

func start
```

Run `sql/schema_and_seed.sql` against your database first (sqlcmd, Azure
Data Studio, or the Azure Portal's Query editor).

---

## Step-by-step deployment guide

This is the sequence actually followed for this deployment. CLI shown;
every step has a Portal equivalent if you prefer clicking through.

### 1. Resource group and storage account
```bash
az login
az account set --subscription "<your-subscription>"

az group create --name <resource-group> --location centralindia

az storage account create \
  --name <storage-account-name> \
  --resource-group <resource-group> \
  --location centralindia \
  --sku Standard_LRS
```

### 2. Azure SQL Database
```bash
az sql server create \
  --name <sql-server-name> \
  --resource-group <resource-group> \
  --location centralindia \
  --admin-user sqladmin \
  --admin-password "<strong-password>"

az sql db create \
  --resource-group <resource-group> \
  --server <sql-server-name> \
  --name <database-name> \
  --service-objective S0

az sql server firewall-rule create \
  --resource-group <resource-group> \
  --server <sql-server-name> \
  --name AllowAzureServices \
  --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0
```
Run `sql/schema_and_seed.sql` against it via the Portal's Query editor.

### 3. Function App
```bash
az functionapp create \
  --name <function-app-name> \
  --resource-group <resource-group> \
  --storage-account <storage-account-name> \
  --consumption-plan-location centralindia \
  --runtime python \
  --runtime-version 3.11 \
  --functions-version 4 \
  --os-type Linux
```
This also auto-creates a linked Application Insights resource with the
same name as the Function App.

> **If you're on a free trial subscription:** the Portal's newer "Create
> Function App" wizard defaults to the **Flex Consumption** plan, which
> free-trial subscriptions can't use. The CLI command above (using
> `--consumption-plan-location`, not `--plan`) creates the **classic**
> Consumption plan instead, which works fine on a free trial. See
> [Deployment notes](#deployment-notes--real-issues-hit-and-fixed) below.

### 4. Managed Identity + SQL access
```bash
az functionapp identity assign \
  --name <function-app-name> \
  --resource-group <resource-group>
```
Then, connected to the DB as an Azure AD admin (Portal Query editor):
```sql
CREATE USER [<function-app-name>] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [<function-app-name>];
ALTER ROLE db_datawriter ADD MEMBER [<function-app-name>];
```

### 5. Azure AD App Registration
- Register an app (e.g. `FeeManagementAPI`).
- **Expose an API** → set an Application ID URI (`api://<client-id>`).
- **App roles** → create `Student` and `Admin`.
- **Enterprise Applications** → **Users and groups** → assign real users
  to those roles (a user needs the role assigned here for
  `@require_role` to pass — App Registration alone doesn't do this).

### 6. SendGrid
- Create a SendGrid account, verify a Single Sender (or a domain), and
  generate an API key.

### 7. Application settings
```bash
az functionapp config appsettings set \
  --name <function-app-name> \
  --resource-group <resource-group> \
  --settings \
    USE_MANAGED_IDENTITY=true \
    SQL_CONNECTION_STRING="Driver={ODBC Driver 18 for SQL Server};Server=tcp:<sql-server-name>.database.windows.net,1433;Database=<database-name>;Encrypt=yes;" \
    AAD_TENANT_ID="<tenant-id>" \
    AAD_API_AUDIENCE="api://<client-id>" \
    SENDGRID_API_KEY="<sendgrid-key>" \
    SENDGRID_FROM_EMAIL="<verified-sender>" \
    REMINDER_CRON_SCHEDULE="0 0 8 * * *"
```
`APPLICATIONINSIGHTS_CONNECTION_STRING` is normally already set
automatically since step 3 auto-provisioned App Insights alongside the
Function App — confirm with:
```bash
az functionapp config appsettings list \
  --name <function-app-name> --resource-group <resource-group> \
  --query "[?name=='APPLICATIONINSIGHTS_CONNECTION_STRING']"
```

### 8. Deploy the code
`shared/` lives one level above `function_app/`; only `function_app/`
gets zipped and published, so copy `shared/` in first:
```bash
cd fee-management-system
rm -rf function_app/shared
cp -r shared function_app/shared

cd function_app
func azure functionapp publish <function-app-name>
```

### 9. API Management
```bash
az apim create \
  --name <apim-name> \
  --resource-group <resource-group> \
  --publisher-email "you@example.com" \
  --publisher-name "Fee Management" \
  --sku-name Developer
```
In the Portal: **APIM instance → APIs → Add API → Function App** → select
your Function App (this imports the three HTTP routes; the Timer trigger
has no HTTP surface, so it won't appear here — expected).
- On the **Product**, set **"Requires subscription" = Yes** — this is
  what enforces the `Ocp-Apim-Subscription-Key` header (not part of the
  policy XML).
- Apply `apim/apim-policy.xml` at the API (or Product) scope, replacing
  `{tenant-id}` and `{api-app-client-id}` with your real values.

### 10. Verify
```bash
curl https://<function-app-name>.azurewebsites.net/api/fee/status/1 \
  -H "Authorization: Bearer <token>"
```
See [Testing guide](#testing-guide) below for the full set.

---

## Configuration reference

| Setting | Used by | Purpose |
|---|---|---|
| `SQL_CONNECTION_STRING` | `shared/db.py` | Azure SQL connection (SQL auth string; ignored when `USE_MANAGED_IDENTITY=true`) |
| `USE_MANAGED_IDENTITY` | `shared/db.py` | `true` in production — connects via the Function App's Managed Identity, no password in config |
| `AAD_TENANT_ID` | `shared/auth.py` | Your Azure AD tenant ID — used to build the JWKS URL and validate the token issuer |
| `AAD_API_AUDIENCE` | `shared/auth.py` | The API app registration's Application ID URI — validated as the token audience |
| `SENDGRID_API_KEY` | `shared/email.py` | SendGrid API key |
| `SENDGRID_FROM_EMAIL` | `shared/email.py` | Verified sender address |
| `REMINDER_CRON_SCHEDULE` | `FeeReminderTimer` | NCRONTAB schedule for the daily job; default `0 0 8 * * *` (08:00 UTC) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Functions host | Wires structured logging to Application Insights |

---

## Testing guide

Covers every endpoint, every role, and the automation job — locally and
against the deployed Function App. Get a real token first:
```bash
az login
az account get-access-token --resource api://<client-id> --query accessToken -o tsv
```
The signed-in user needs `Student` or `Admin` assigned under the
Enterprise Application's "Users and groups" (step 5 above) or
`@require_role` will correctly reject them with a 403.

### Task 3 — PaymentStatus

```bash
# Own record — expect 200 with Paid/Partially Paid/Overdue
curl -H "Authorization: Bearer <student-token>" \
  https://<function-app-name>.azurewebsites.net/api/fee/status/1

# Someone else's StudentID as a Student — expect 403
curl -H "Authorization: Bearer <student-token>" \
  https://<function-app-name>.azurewebsites.net/api/fee/status/2

# Any StudentID as an Admin — expect 200
curl -H "Authorization: Bearer <admin-token>" \
  https://<function-app-name>.azurewebsites.net/api/fee/status/2

# Nonexistent StudentID — expect 404
curl -H "Authorization: Bearer <admin-token>" \
  https://<function-app-name>.azurewebsites.net/api/fee/status/999999

# No token at all — expect 401
curl https://<function-app-name>.azurewebsites.net/api/fee/status/1
```

**Confirm the status logic directly**: pick a student with `PaidAmount <
TotalFee` and `DueDate` in the past — response should read `"status":
"Overdue"`; one fully paid — `"Paid"`; one partially paid with a future
`DueDate` — `"Partially Paid"`.

### Task 3 (extension) — ListStudents

```bash
# All students, admin only — expect 200, a paged list
curl -H "Authorization: Bearer <admin-token>" \
  "https://<function-app-name>.azurewebsites.net/api/fee/students"

# Filtered by course and status
curl -H "Authorization: Bearer <admin-token>" \
  "https://<function-app-name>.azurewebsites.net/api/fee/students?course=B.Tech%20CSE&status=Overdue"

# As a Student — expect 403
curl -H "Authorization: Bearer <student-token>" \
  "https://<function-app-name>.azurewebsites.net/api/fee/students"
```
Confirm `total` in the response matches a manual `SELECT COUNT(*)` against
the same filter run directly in SQL.

### Task 4 — UpdateFeeRecord

```bash
# Valid update as Admin — expect 200
curl -X PATCH -H "Authorization: Bearer <admin-token>" -H "Content-Type: application/json" \
  -d '{"paidAmount": 25000, "note": "Bank transfer"}' \
  https://<function-app-name>.azurewebsites.net/api/fee/update/1

# As a Student — expect 403
curl -X PATCH -H "Authorization: Bearer <student-token>" -H "Content-Type: application/json" \
  -d '{"paidAmount": 25000}' \
  https://<function-app-name>.azurewebsites.net/api/fee/update/1

# Overpayment — expect 400
curl -X PATCH -H "Authorization: Bearer <admin-token>" -H "Content-Type: application/json" \
  -d '{"paidAmount": 999999999}' \
  https://<function-app-name>.azurewebsites.net/api/fee/update/1

# Negative amount — expect 400
curl -X PATCH -H "Authorization: Bearer <admin-token>" -H "Content-Type: application/json" \
  -d '{"paidAmount": -100}' \
  https://<function-app-name>.azurewebsites.net/api/fee/update/1

# Missing body field — expect 400
curl -X PATCH -H "Authorization: Bearer <admin-token>" -H "Content-Type: application/json" \
  -d '{}' \
  https://<function-app-name>.azurewebsites.net/api/fee/update/1
```
**Confirm the audit trail**, after a successful update:
```sql
SELECT TOP 5 * FROM FeeTransactions WHERE StudentID = 1 ORDER BY CreatedAt DESC;
```
Should show one new row with the correct `PreviousPaid`/`NewPaid`, and a
matching `GET /fee/status/1` should now reflect the new `PaidAmount`.

### Task 2 — FeeReminderTimer

**Local test with a short schedule** (never use this in production —
revert to the daily default before deploying):
```bash
# in local.settings.json, temporarily:
"REMINDER_CRON_SCHEDULE": "0 */2 * * * *"   # every 2 minutes
```
Watch the terminal for `FeeReminderTimer found N overdue student(s)` and
`Reminder sent to StudentID=...` log lines.

**Trigger it manually against the deployed app** (Portal → Function App →
`FeeReminderTimer` → Code + Test → Test/Run), or wait for the scheduled
time and check Portal → Function App → `FeeReminderTimer` → Monitor for
an invocation.

**Confirm it actually ran and logged correctly:**
```sql
SELECT TOP 20 * FROM ReminderLog ORDER BY SentAt DESC;
```
Each row is committed individually as the function runs — you should see
rows appear within seconds of the run starting, not only after it
finishes.

**Confirm idempotency** — trigger a second run the same day. Expect the
log lines to show `skipped` for students already logged `Sent` today
(check the `sent=/skipped=/failed=` summary line at the end of the run),
and no new `ReminderLog` rows for those students.

**Confirm an actual email arrives** — check the inbox for the address
tied to a test student in the seed data.

### Task 3 — API Management (if configured)

```bash
# Missing subscription key — expect 401
curl https://<apim-name>.azure-api.net/fee/status/1 \
  -H "Authorization: Bearer <token>"

# With subscription key — expect 200
curl https://<apim-name>.azure-api.net/fee/status/1 \
  -H "Authorization: Bearer <token>" \
  -H "Ocp-Apim-Subscription-Key: <subscription-key>"

# Rate limit — fire more than the configured calls/minute in quick succession,
# expect a 429 partway through
for i in $(seq 1 70); do
  curl -s -o /dev/null -w "%{http_code}\n" https://<apim-name>.azure-api.net/fee/status/1 \
    -H "Authorization: Bearer <token>" \
    -H "Ocp-Apim-Subscription-Key: <subscription-key>"
done
```

---

## Monitoring & alerts (Task 5)

Application Insights receives every `logging.info`/`warning`/`exception`
call automatically — no extra instrumentation needed. In the App
Insights resource → **Logs**:

```kusto
// Reminder run outcomes over time
traces
| where message has "FeeReminderTimer run complete"
| project timestamp, message
| order by timestamp desc
```

```kusto
// Failed HTTP invocations, last 24h
requests
| where success == false and timestamp > ago(24h)
| summarize count() by name, resultCode
```

```kusto
// Individual failed reminder sends
traces
| where message has "Failed to send reminder"
| project timestamp, message
| order by timestamp desc
```

**Alerts** (App Insights → Alerts → Create alert rule):

| Alert | Signal | Threshold |
|---|---|---|
| API failure rate | `requests/failed` | > 5% over 15 min |
| API latency | `requests/duration` (avg) | > 2s over 15 min |
| Reminder job didn't run | Log query on `FeeReminderTimer run started`, 0 results | over a 26h window |
| Reminder failures | Log query on `Failed to send reminder`, > 0 results | over 24h |

---

## Deployment notes — real issues hit and fixed

**1. Flex Consumption not available on a free-trial subscription.** The
Portal's newer "Create Function App" wizard defaults to the Flex
Consumption plan (SKU `FC1`), which is explicitly blocked on free trial
subscriptions ("Free trial subscription is not supported for Flex
Consumption"). Fix: use `az functionapp create` with
`--consumption-plan-location` (not `--plan`), which provisions the
classic Consumption plan (`Y1`, Linux) instead — fully supported on a
free trial, and what this deployment actually runs on. (Azure does warn
that Linux Consumption reaches end-of-life in September 2028 — fine for
now, worth migrating to Flex before then on a paid subscription.)

**2. `Microsoft.Web` resource provider not registered.** The very first
`az functionapp create` attempt failed with
`MissingSubscriptionRegistration` — a fresh subscription that has never
created an App Service/Functions resource before needs its
`Microsoft.Web` provider registered first. Fixed with:
```bash
az provider register --namespace Microsoft.Web
az provider show --namespace Microsoft.Web --query registrationState -o tsv
# wait for "Registered", then re-run the functionapp create command
```

**3. `ReminderLog` schema mismatch caused an `Invalid column name
'DueDate'` error.** An earlier version of `fee_reminder_timer.py` assumed
`ReminderLog` had `DueDate` and `Note` columns; the actual table only has
`ReminderID, StudentID, SentAt, Status`. Fixed by keying the idempotency
check on `(StudentID, "already sent today")` instead of
`(StudentID, DueDate)` — functionally equivalent here since a student
only ever has one `DueDate` on file at a time, and it has the added
benefit of matching "daily reminder while still overdue" rather than
"one reminder ever per due date."

**4. Reminder run appeared to do nothing — `ReminderLog` was empty
mid-run.** The original code held the entire run (thousands of students,
each with a real SendGrid network call) as one transaction, committing
only once at the very end. Checking the table while the run was still in
progress correctly showed zero rows — it just hadn't committed anything
yet, and would have taken 15+ minutes to. Fixed by committing after each
student's send-and-log instead of once at the end of the run: rows now
appear within seconds of the run starting, and a crash partway through
only loses unprocessed students rather than the entire run's work.

**5. JWT "Signature verification failed" despite a valid token.**
Azure AD can hand back either a v1.0 token (`iss:
https://sts.windows.net/{tenant}/`) or a v2.0 token (`iss:
https://login.microsoftonline.com/{tenant}/v2.0`) for the same API,
depending on how it's requested (`--resource` vs `--scope` in
`az account get-access-token`) — both are valid, both are signed with the
same underlying keys. The original `shared/auth.py` hardcoded a single
expected issuer, which rejected whichever token type it didn't expect.
Fixed by fetching JWKS from the tenant's v2.0 discovery endpoint (which
Microsoft confirms serves valid signing keys for both token versions) and
checking the issuer against a small set of accepted formats instead of
one hardcoded string. Full detail is in the docstring at the top of
`shared/auth.py`.

**6. Resource group ended up different than planned.** An early
`az functionapp create` command reused the storage account's resource
group name for `--resource-group` instead of the originally intended one
— both the storage account and the Function App ended up in that group,
leaving the originally created (empty) resource group unused. Not
broken, just worth checking `az functionapp show --query resourceGroup`
if a later command can't find the app — it's whichever group the create
command actually used, not necessarily the one from an earlier step.

---

## Assignment requirement coverage

| # | Requirement | Where it's satisfied |
|---|---|---|
| 1 | Data Storage — Azure SQL, Students/Administrators, 20+ seed rows, scales to 5,000+ | `sql/schema_and_seed.sql`, `sql/generate_bulk_seed.py` — live-tested at 5,020 total rows |
| 2 | Automation — reminders via Logic Apps/Durable Functions | `function_app/FeeReminderTimer/fee_reminder_timer.py`, idempotent via `ReminderLog`, SendGrid via `shared/email.py` |
| 3 | Payment Status API — fetch by StudentID, Paid/Partially Paid/Overdue; secured via APIM with rate limiting + API key | `function_app/PaymentStatus/payment_status.py`, `apim/apim-policy.xml` |
| 4 | Secure Updates — admin endpoint, AAD + RBAC | `function_app/UpdateFeeRecord/update_fee_record.py`, `shared/auth.py` |
| 5 | Scalability & Monitoring — Application Insights, retry policies | `function_app/host.json` (App Insights wiring + retry policy), KQL queries and alert rules above |

Live-verified: `GET /api/fee/status/1` against the deployed Function App
(`https://fee-management-functions.azurewebsites.net`) returns a correct,
fully-formed response reflecting real data from Azure SQL.