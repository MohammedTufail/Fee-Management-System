/* ============================================================
   Fee Management System - Azure SQL Database Schema + Seed Data
   ============================================================ */

-- ------------------------------------------------------------
-- 1. STUDENTS TABLE
-- ------------------------------------------------------------
IF OBJECT_ID('dbo.Students', 'U') IS NOT NULL DROP TABLE dbo.Students;
CREATE TABLE dbo.Students (
    StudentID        INT             IDENTITY(1,1) PRIMARY KEY,
    Name             NVARCHAR(100)   NOT NULL,
    Course           NVARCHAR(100)   NOT NULL,
    TotalFee         DECIMAL(10,2)   NOT NULL,
    PaidAmount       DECIMAL(10,2)   NOT NULL DEFAULT 0,
    DueDate          DATE            NOT NULL,
    Email            NVARCHAR(200)   NOT NULL,
    AzureADObjectId  UNIQUEIDENTIFIER NULL,      -- maps student to their Azure AD identity (oid claim)
    CreatedAt        DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
    UpdatedAt        DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME()
);

-- Helpful for the 5,000+ record scalability requirement
CREATE INDEX IX_Students_DueDate ON dbo.Students (DueDate) INCLUDE (PaidAmount, TotalFee);
CREATE INDEX IX_Students_AzureADObjectId ON dbo.Students (AzureADObjectId);

-- ------------------------------------------------------------
-- 2. ADMINISTRATORS TABLE
-- ------------------------------------------------------------
IF OBJECT_ID('dbo.Administrators', 'U') IS NOT NULL DROP TABLE dbo.Administrators;
CREATE TABLE dbo.Administrators (
    AdminID          INT             IDENTITY(1,1) PRIMARY KEY,
    Name             NVARCHAR(100)   NOT NULL,
    Role             NVARCHAR(50)    NOT NULL,     -- e.g. 'Admin', 'FeeManager'
    Email            NVARCHAR(200)   NOT NULL,
    AzureADObjectId  UNIQUEIDENTIFIER NULL,
    CreatedAt        DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME()
);

-- ------------------------------------------------------------
-- 3. FEE TRANSACTIONS TABLE (audit trail for every update / payment)
-- ------------------------------------------------------------
IF OBJECT_ID('dbo.FeeTransactions', 'U') IS NOT NULL DROP TABLE dbo.FeeTransactions;
CREATE TABLE dbo.FeeTransactions (
    TransactionID    INT             IDENTITY(1,1) PRIMARY KEY,
    StudentID        INT             NOT NULL FOREIGN KEY REFERENCES dbo.Students(StudentID),
    ChangedBy        NVARCHAR(200)   NOT NULL,     -- Admin email / oid from the JWT
    PreviousPaid     DECIMAL(10,2)   NOT NULL,
    NewPaid          DECIMAL(10,2)   NOT NULL,
    Note             NVARCHAR(400)   NULL,
    CreatedAt        DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME()
);

-- ------------------------------------------------------------
-- 4. REMINDER LOG TABLE (so the daily job never double-sends)
-- ------------------------------------------------------------
IF OBJECT_ID('dbo.ReminderLog', 'U') IS NOT NULL DROP TABLE dbo.ReminderLog;
CREATE TABLE dbo.ReminderLog (
    ReminderID       INT             IDENTITY(1,1) PRIMARY KEY,
    StudentID        INT             NOT NULL FOREIGN KEY REFERENCES dbo.Students(StudentID),
    SentAt           DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
    Status           NVARCHAR(20)    NOT NULL      -- 'Sent' / 'Failed'
);

-- ------------------------------------------------------------
-- 5. SEED DATA - 20 sample students
-- ------------------------------------------------------------
INSERT INTO dbo.Students (Name, Course, TotalFee, PaidAmount, DueDate, Email) VALUES
('Aarav Sharma',      'B.Tech CSE',        120000, 120000, '2026-01-15', 'aarav.sharma@example.com'),
('Diya Patel',        'B.Tech ECE',        110000,  55000, '2026-08-20', 'diya.patel@example.com'),
('Kabir Mehta',       'MBA',               250000,  50000, '2026-06-10', 'kabir.mehta@example.com'),
('Ananya Iyer',       'B.Sc Physics',       80000,  80000, '2026-02-01', 'ananya.iyer@example.com'),
('Vivaan Nair',       'B.Tech Mech',       115000,      0, '2026-05-05', 'vivaan.nair@example.com'),
('Ishaan Verma',      'BCA',                70000,  70000, '2026-03-12', 'ishaan.verma@example.com'),
('Saanvi Reddy',      'B.Com',              60000,  30000, '2026-07-01', 'saanvi.reddy@example.com'),
('Reyansh Gupta',     'M.Tech CSE',        180000, 180000, '2026-01-30', 'reyansh.gupta@example.com'),
('Myra Joshi',        'B.Tech Civil',      105000,  40000, '2026-04-18', 'myra.joshi@example.com'),
('Arjun Rao',         'BBA',                65000,      0, '2026-09-01', 'arjun.rao@example.com'),
('Kiara Das',         'B.Sc Chemistry',     75000,  75000, '2026-02-22', 'kiara.das@example.com'),
('Vihaan Kapoor',     'MBA',               250000, 125000, '2026-08-05', 'vihaan.kapoor@example.com'),
('Anika Menon',       'B.Tech IT',         112000,  56000, '2026-06-25', 'anika.menon@example.com'),
('Aditya Chawla',     'BCA',                70000,  70000, '2026-01-10', 'aditya.chawla@example.com'),
('Navya Bhatt',       'B.Com',              60000,  20000, '2026-03-30', 'navya.bhatt@example.com'),
('Aryan Malhotra',    'B.Tech Mech',       115000, 115000, '2026-02-14', 'aryan.malhotra@example.com'),
('Riya Sen',          'M.Sc Maths',         90000,  45000, '2026-07-19', 'riya.sen@example.com'),
('Yash Agarwal',      'B.Tech ECE',        110000,      0, '2026-04-02', 'yash.agarwal@example.com'),
('Prisha Khanna',     'B.Sc Physics',       80000,  40000, '2026-05-28', 'prisha.khanna@example.com'),
('Dhruv Pillai',      'B.Tech CSE',        120000,  90000, '2026-03-05', 'dhruv.pillai@example.com');

-- ------------------------------------------------------------
-- 6. SEED DATA - Administrators
-- ------------------------------------------------------------
INSERT INTO dbo.Administrators (Name, Role, Email) VALUES
('Priya Desai',   'Admin',       'priya.desai@college.edu'),
('Rohan Kulkarni','FeeManager',  'rohan.kulkarni@college.edu');

/* NOTE: Once real users sign in through Azure AD, run an UPDATE
   statement to populate AzureADObjectId with each user's `oid`
   claim, e.g.:

   UPDATE dbo.Students
   SET AzureADObjectId = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
   WHERE Email = 'aarav.sharma@example.com';
*/
