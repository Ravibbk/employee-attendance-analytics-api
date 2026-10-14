# Starter Code Review

## 1. Health Check
**Location:** `health()` (line 222)

The original starter always returned `{"status": "ok"}` without checking MongoDB connectivity, meaning it could report readiness while the database was unavailable.

**Fix applied:** The health endpoint now wraps `client.admin.command("ping")` in a try/except and returns HTTP 503 with `{"detail": "Database unavailable"}` when the ping fails. This satisfies the OpenAPI spec requirement that the runner polls `/health` before testing.

---

## 2. Employee Creation
**Location:** `create_employee()` (line 235)

**Bugs found in the starter:**
- The duplicate check followed by insertion was vulnerable to concurrent requests — two simultaneous creates with the same `emp_code` could both pass the `find_one` check and both attempt insertion.
- A unique database index on `emp_code` was missing, so the duplicate check relied entirely on application-level logic.
- `created_at` was stored using a naive `datetime` (no time zone), which could produce incorrect epoch-millisecond conversions depending on the server's locale.
- Employee field validation was incomplete — there was no pattern enforcement on `emp_code`, no email format validation, no string length bounds, and `shift_start == shift_end` was not rejected.
- Duplicate-key errors at the database boundary were not caught.

**Fixes applied:**
- A unique index `uq_employees_emp_code` on `emp_code` is created at startup via `create_indexes()`.
- The `EmployeeIn` Pydantic model enforces: `emp_code` pattern (`^EMP\d{4,6}$`), `email` pattern, `name`/`department` length bounds, `shift_start`/`shift_end` HH:MM pattern, and a model-level validator that rejects `shift_start == shift_end`.
- `created_at` is set with `datetime.now(timezone.utc)` (timezone-aware).
- `DuplicateKeyError` is caught and translated to HTTP 409.
- The response is built without `_id`.

---

## 3. Employee Listing
**Location:** `list_employees()` (line 255)

**Bugs found in the starter:**
- `skip = page * page_size` was skipping the first page of results (page 1 would skip `page_size` records).
- `total` was counting all employees in the collection instead of only those matching the department filter.
- The query did not sort by `emp_code`, so result order was undefined.
- Page and page-size constraints were not enforced (e.g., page=0, page_size=500 would be accepted).

**Fixes applied:**
- `skip = (page - 1) * page_size` is used so page 1 returns the first records.
- The department filter is applied to both `count_documents(query)` and `find(query, ...)`.
- Results are sorted by `emp_code` ascending.
- `page` is constrained with `ge=1` and `page_size` with `ge=1, le=100` via FastAPI `Query()`.
- `_id` is excluded from the projection.
- `created_at` (BSON date) is converted to epoch milliseconds in the response.

---

## 4. Punch-In
**Location:** `punch_in()` (line 291)

**Bugs found in the starter:**
- The employee lookup result was not checked before accessing shift fields — a missing employee caused a `TypeError` instead of a 404.
- The duplicate check and insertion were not concurrency-safe (check-then-insert pattern).
- Timestamp conversion used `datetime.fromtimestamp()` without `tz`, depending on the machine's local time zone.
- `punched_at` was not validated for range (seconds-looking values, out-of-range numbers were accepted).
- Instants were not truncated to whole seconds (R1 requires `microsecond=0`).
- The attendance date calculation did not correctly handle IST and overnight shifts.
- `status` was not restricted to `PRESENT`, `WFH`, and `ON_DUTY` — any string was accepted.
- The response included an `id` field (stringified `_id`), which is not part of the API contract.
- The required unique index on `(emp_code, date)` was missing.

**Fixes applied:**
- Employee is fetched first; 404 is raised if not found.
- `punched_at` is validated via the `PunchInIn` Pydantic model: must be a `StrictInt` in the range `[100_000_000_000, 4_102_444_800_000]`. If omitted, defaults to server `now()`.
- The timestamp is converted with explicit `tz=timezone.utc` and truncated with `.replace(microsecond=0)`.
- Attendance date is computed in IST. Overnight shifts (where `shift_end <= shift_start`) and early-morning punch-ins (before `shift_end`) correctly resolve to the previous day.
- `status` is validated by the Pydantic model with a pattern `^(PRESENT|WFH|ON_DUTY)$`.
- The unique index `uq_attendance_emp_date` prevents duplicate records; `DuplicateKeyError` returns 409.
- The response excludes `_id`.

---

## 5. Attendance Listing
**Location:** `list_attendance()` (line 410)

**Bugs found in the starter:**
- The endpoint loaded all matching records into Python memory before sorting and paginating in-app.
- Sorting and pagination were performed in Python (e.g., `sorted()` and list slicing) instead of MongoDB `sort/skip/limit`.
- The sort order did not include the required `emp_code` ascending tie-breaker.
- `_id` was exposed indirectly through an `id` field.
- `date_from > date_to` was silently accepted instead of returning 422.
- Pagination limits were not enforced.

**Fixes applied:**
- MongoDB handles the full query pipeline: `find()` with filter → `sort([("date", -1), ("emp_code", 1)])` → `skip()` → `limit()`.
- `count_documents(q)` uses the same filter for the total count.
- `date_from > date_to` raises HTTP 422.
- `_id` is excluded from the projection (`{"_id": 0}`).
- `punch_in` and `punch_out` BSON dates are converted to epoch milliseconds.
- `history` and `half_day` fields have defaults applied for legacy records.

---

## 6. Derived-Field Calculations
**Locations:** `compute_late_minutes()` (line 76), `compute_work_hours()` (line 95), `compute_overtime()` (line 103)

**Bugs found in the starter:**
- Work-hour rounding used Python's default `round()` (banker's rounding / half-to-even), not the required half-up rounding.
- Overtime was counted whenever `punch_out` was after `shift_end`, without enforcing the 30-minute minimum (R3).
- Overnight shifts were not handled by the overtime calculation — the shift-end datetime was always placed on the same calendar day.
- Late-minutes computation had off-by-one issues with the 10-minute grace period boundary.

**Fixes applied:**
- `compute_work_hours()` uses `Decimal(str(seconds / 3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` for R4 compliance.
- `compute_late_minutes()` uses `elapsed <= 600` (10 minutes = 600 seconds) as the grace boundary, with `int(elapsed // 60)` for floor minutes from shift start (R2).
- `compute_overtime()` detects overnight shifts (`shift_end <= shift_start`), places the shift-end on the next calendar day, and only counts overtime when the excess is ≥ 1800 seconds (30 minutes) per R3.
- All computations convert to IST before comparing with shift times.

---

## 7. Missing Endpoints (Now Implemented)

The starter implementation was missing these endpoints, all of which are now implemented:

### 7.1 `POST /attendance/punch-out` (line 332)
- Finds the employee's most recent open record whose `punch_in ≤ punched_at`.
- Validates: `punch_out > punch_in` and within 24 hours.
- Computes `work_hours` (R4), `overtime_minutes` (R3), `half_day` (R5).
- Uses atomic `update_one` with `{"punch_out": None}` filter to prevent concurrent double punch-outs (409).

### 7.2 `PATCH /attendance/{emp_code}/{date}` (line 465)
- Validates the record exists (404 if not).
- Applies status, punch_in, punch_out changes with full business-rule validation.
- ABSENT/LEAVE clears punch times; presence statuses require punch_in; punch_in must match attendance date (R1).
- Recomputes all derived fields (R2–R5) from the final times.
- Appends exactly one history entry with `{at, by, reason, changes}` — only actually-changed fields appear in `changes`.
- Uses optimistic concurrency: the update filter includes the current field values and history array, returning 409 if the record was modified concurrently.

### 7.3 `GET /analytics/employees/{emp_code}/monthly` (line 693)
- Working days computed from `joined_on` (R7, R9): Mon–Fri only, starting from max(month_start, joined_on).
- MongoDB aggregation computes `present_days` (half-day = 0.5, weekday only), `leave_days`, `late_count`, `total_late_minutes`, `total_overtime_minutes`.
- `attendance_pct = present_days / working_days × 100`, 2 decimals half-up (R8). Null when `working_days = 0`.

### 7.4 `GET /analytics/departments/summary` (line 832)
- Headcount from the `employees` collection: employees with `joined_on ≤ month_end` (R9), including those with zero logs.
- MongoDB aggregation with `$lookup` to employees, groups by department.
- `avg_work_hours` = mean of `work_hours` for present-status records with non-null `work_hours`; null if none. 2 decimals half-up (R8).
- Departments sorted alphabetically.

### 7.5 `GET /analytics/leaderboard/late` (line 1009)
- MongoDB aggregation: filter `late_minutes > 0`, group by `emp_code`, `$lookup` to employees.
- Standard competition ranking via `$setWindowFields` with `$rank`: ties share a rank, next rank skipped (1, 2, 2, 4).
- `limit` applied after ranking: all employees with `rank ≤ limit` are returned (may exceed `limit` rows on ties).
- Sorted by `total_late_minutes` desc, `emp_code` asc.

### 7.6 `GET /analytics/departments/{department}/trend` (line 1090)
- `$densify` fills every calendar day from `from` to `to` inclusive (weekends and empty days included).
- `headcount` per day via sub-pipeline lookup: department employees with `joined_on ≤ that day` (R9).
- `attendance_rate = present_count / headcount` on working days with headcount > 0; null otherwise. 4 decimals half-up.
- `moving_avg_7d` = mean of non-null `attendance_rate` in a 7-day window (current + up to 6 preceding rows in range). 4 decimals half-up.
- 404 for unknown department; 422 if `to < from` or range > 92 days.

### 7.7 `GET /admin/explain/{endpoint}` (line 1283)
- Runs `explain` with `executionStats` verbosity for the main query/aggregation of the named endpoint.
- Accepts the same query parameters as the target endpoint.
- Returns raw MongoDB explain JSON. The grader checks for `IXSCAN` (no `COLLSCAN`).

---

## 8. Database Indexes and Performance
**Location:** `create_indexes()` (line 39)

The starter code did not create any indexes. The assignment requires indexed queries and explain plans that avoid collection scans on a ~100k-record dataset.

**Indexes created at startup:**

| Index | Fields | Purpose |
|-------|--------|---------|
| `uq_employees_emp_code` | `emp_code: 1` (unique) | Unique constraint + fast employee lookup |
| `uq_attendance_emp_date` | `emp_code: 1, date: 1` (unique) | One record per employee per day; concurrency safety for punch-in |
| `ix_attendance_date_emp` | `date: -1, emp_code: 1` | Covers attendance listing sort order (date desc, emp_code asc) |
| `ix_attendance_status_date_emp` | `status: 1, date: -1, emp_code: 1` | Status-filtered attendance queries |
| `ix_employees_department_emp` | `department: 1, emp_code: 1` | Department filtering for employee listing and analytics |

These indexes ensure that the explain endpoint returns `IXSCAN` stages and no `COLLSCAN` for the five explainable queries.

---

## Conclusion

The starter code covered only part of the API and had correctness, validation, concurrency, serialization, and performance problems. The implementation has been corrected and completed against `openapi.yaml` and `DATA_MODEL.md`:

- **All 11 endpoints** are implemented and match the OpenAPI contract.
- **Business rules R1–R10** are applied with correct IST handling, half-up rounding, and overnight shift logic.
- **Concurrency** is handled via unique indexes and atomic updates (DuplicateKeyError for punch-in, optimistic locking for regularization, atomic conditional update for punch-out).
- **Derived fields** are computed server-side and stored; analytics read stored values per the specification.
- **Pagination** follows R10 (page ≥ 1, page_size 1–100, default 20).
- **No `_id`** is exposed in any request or response.
- **History** audit trail is append-only with correct `from`/`to` epoch-millisecond conversion for punch times.