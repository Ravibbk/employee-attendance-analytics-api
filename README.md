# Employee Attendance & Analytics API

A production-grade RESTful API built with **FastAPI** and **MongoDB** for managing employee attendance records and generating workforce analytics. Designed for HROne's engineering assignment.

**Live API:** https://employee-attendance-analytics-api-cnab.onrender.com/docs

---

## Tech Stack

| Layer        | Technology                    |
|-------------|-------------------------------|
| Framework   | FastAPI 0.110+                |
| Server      | Uvicorn (ASGI)                |
| Database    | MongoDB 7.0 (Atlas free tier) |
| Driver      | PyMongo 4.7+ (with SRV)      |
| Validation  | Pydantic v2                   |
| Config      | python-dotenv                 |
| Deployment  | Render                        |

---

## Project Structure

```
employee-attendance-analytics-api/
├── app/
│   ├── __init__.py
│   └── main.py            # All application code (single-file as required)
├── sample_data/
│   ├── employees.json      # 6 sample employees (Extended JSON)
│   └── attendance_logs.json # 9 sample attendance records
├── .github/
│   └── workflows/          # CI workflow
├── openapi.yaml            # Full API contract & business rules R1–R10
├── DATA_MODEL.md           # MongoDB collection schemas & samples
├── PROBLEM_STATEMENT.docx  # Assignment brief
├── REVIEW.md               # Starter code review (bugs found & fixes)
├── DECISIONS.md            # Design decisions & trade-offs
├── requirements.txt        # Python dependencies
├── sample_seed.py          # Loads sample data into MongoDB
└── .gitignore
```

---

## Local Setup

### Prerequisites
- Python 3.10+
- MongoDB 6.0+ (local Docker or Atlas M0 free tier)

### Steps

```bash
# 1. Clone
git clone https://github.com/Ravibbk/employee-attendance-analytics-api.git
cd employee-attendance-analytics-api

# 2. Virtual environment
python -m venv .venv
# Linux/Mac:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Environment variables
# Create a .env file with:
#   MONGO_URI=mongodb+srv://<user>:<pass>@<cluster>.mongodb.net/
#   MONGO_DB=attendance_db
# Or for local Docker:
#   docker run -p 27017:27017 mongo:7
#   MONGO_URI=mongodb://localhost:27017
#   MONGO_DB=attendance_db

# 5. Seed sample data
python sample_seed.py

# 6. Start the server
uvicorn app.main:app --port 8000 --reload
```

Visit `http://localhost:8000/docs` for the interactive Swagger UI.

---

## API Endpoints

### System
| Method | Path       | Description        |
|--------|------------|--------------------|
| GET    | `/health`  | Readiness probe (pings MongoDB, returns 503 on failure) |

### Employees
| Method | Path          | Description                        |
|--------|---------------|------------------------------------|
| POST   | `/employees`  | Create an employee (409 on duplicate `emp_code`) |
| GET    | `/employees`  | List employees (paginated, filterable by department) |

### Attendance
| Method | Path                              | Description                         |
|--------|-----------------------------------|-------------------------------------|
| POST   | `/attendance/punch-in`            | Record a punch-in (creates day record) |
| POST   | `/attendance/punch-out`           | Record a punch-out (computes derived fields) |
| GET    | `/attendance`                     | List attendance records (paginated, filterable) |
| PATCH  | `/attendance/{emp_code}/{date}`   | Regularize/correct a record (audit trail) |

### Analytics
| Method | Path                                          | Description                              |
|--------|-----------------------------------------------|------------------------------------------|
| GET    | `/analytics/employees/{emp_code}/monthly`     | Monthly summary for one employee         |
| GET    | `/analytics/departments/summary`              | Department summary for a month           |
| GET    | `/analytics/leaderboard/late`                 | Late-comers leaderboard (competition ranking) |
| GET    | `/analytics/departments/{department}/trend`   | Daily attendance trend with 7-day moving average |

### Admin
| Method | Path                          | Description                              |
|--------|-------------------------------|------------------------------------------|
| GET    | `/admin/explain/{endpoint}`   | MongoDB explain output (IXSCAN verification) |

---

## Business Rules (R1–R10)

| Rule | Summary |
|------|---------|
| R1   | All times are IST (UTC+05:30). Overnight shifts: punch-in before `shift_end` belongs to previous day. Instants truncated to whole seconds. |
| R2   | Late = punch-in strictly > 10 min after `shift_start`. `late_minutes` = floor(minutes since shift_start). |
| R3   | Overtime counted only when ≥ 30 min past `shift_end`. |
| R4   | `work_hours` = (punch_out − punch_in) / 3600, rounded 2 decimals half-up. |
| R5   | `half_day` = true when rounded `work_hours` < 4.50. Counts as 0.5 present day. |
| R6   | PRESENT, WFH, ON_DUTY = present. ABSENT, LEAVE = not present. |
| R7   | Working days = Mon–Fri. Mid-month joiners: count from `joined_on`. Weekend records still count for late/overtime. |
| R8   | Reported numbers: 2 decimals half-up. Rates: 4 decimals half-up. |
| R9   | Headcount = employees with `joined_on` ≤ end of period, regardless of logs. |
| R10  | Pagination: page starts at 1, page_size max 100 (default 20). |

---

## Database Indexes

Created at startup (idempotent):

| Index Name                        | Fields                              | Purpose |
|-----------------------------------|-------------------------------------|---------|
| `uq_employees_emp_code`           | `emp_code: 1` (unique)              | Enforces unique employee codes; fast lookup |
| `uq_attendance_emp_date`          | `emp_code: 1, date: 1` (unique)     | One record per employee per day; concurrency safety |
| `ix_attendance_date_emp`          | `date: -1, emp_code: 1`             | Attendance listing sort order |
| `ix_attendance_status_date_emp`   | `status: 1, date: -1, emp_code: 1`  | Status-filtered attendance queries |
| `ix_employees_department_emp`     | `department: 1, emp_code: 1`        | Department filtering and sorting |

---

## Key Design Decisions

- **Single-file architecture** (`app/main.py`): as required by the assignment.
- **Concurrency via unique indexes**: `DuplicateKeyError` catches handle race conditions for punch-in and employee creation — no check-then-insert pattern.
- **MongoDB aggregation pipelines**: analytics use server-side aggregation with `$lookup`, `$setWindowFields`, `$densify` — no Python-side looping.
- **Epoch milliseconds at the API boundary**: BSON datetimes in storage, integer epoch ms in all request/response bodies.
- **Half-up rounding**: `Decimal.quantize(ROUND_HALF_UP)` used consistently for R8 compliance.
- **IST timezone handling**: `timezone(timedelta(hours=5, minutes=30))` applied to all shift/attendance date calculations.

---

## Deployment

The API is deployed on **Render** as a web service with:
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Environment variables: `MONGO_URI`, `MONGO_DB` set in Render dashboard

---

## License

This project is an engineering assignment submission.
