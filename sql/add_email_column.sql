-- add_email_column.sql
--
-- The original Task 1 schema (StudentID, Name, Course, TotalFee, PaidAmount,
-- DueDate) has no email address, but FeeReminderTimer (Task 2) needs one to
-- send reminders. Run this once against the same database as
-- sql/schema_and_seed.sql before testing Part 3.

ALTER TABLE dbo.Students
    ADD Email NVARCHAR(256) NULL;
GO

-- Backfill for existing seed/bulk-test rows so Part 3 has something to send
-- to immediately. Replace with real addresses (or a real sign-up flow) once
-- you're past local testing — this is placeholder data only.
UPDATE dbo.Students
SET Email = LOWER(REPLACE(REPLACE(Name, ' ', '.'), '''', '')) + '@example.edu'
WHERE Email IS NULL;
GO

-- Optional but recommended: a lightweight uniqueness guard so the app-level
-- idempotency check in FeeReminderTimer isn't the only thing preventing a
-- duplicate "Sent" row for the same (StudentID, DueDate) if two invocations
-- ever raced each other.
-- IF NOT EXISTS (
--     SELECT 1 FROM sys.indexes
--     WHERE name = 'UX_ReminderLog_Student_DueDate_Sent'
-- )
-- CREATE UNIQUE INDEX UX_ReminderLog_Student_DueDate_Sent
--     ON dbo.ReminderLog (StudentID, DueDate)
--     WHERE Status = 'Sent';
-- GO