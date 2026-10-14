# Starter Code Review

## 1. Health Check
**Location:** `health()`

The endpoint always returns `{"status": "ok"}` without checking MongoDB connectivity. It can report readiness while the database is unavailable.

**Fix:** Run a MongoDB ping and return HTTP 503 when the ping fails.

## 2. Employee Creation
**Location:** `create_employee()`

- The duplicate check followed by insertion is vulnerable to concurrent requests.
- A unique database index on `emp_code` is missing.
- `created_at` is stored using a naive datetime.
- Employee field validation is incomplete.
- Duplicate-key errors are not handled at the database boundary.

**Fix:** Create the required indexes at startup, validate request fields, use a UTC timestamp, and translate duplicate-key errors into HTTP 409.

## 3. Employee Listing
**Location:** `list_employees()`

- `skip = page * page_size` skips the first page of records.
- `total` counts all employees instead of only employees matching the department filter.
- The query does not sort by `emp_code`.
- Page and page-size constraints are not enforced.

**Fix:** Apply the filter to both the count and data queries, sort by `emp_code`, and use `(page - 1) * page_size`.

## 4. Punch-In
**Location:** `punch_in()`

- The employee lookup result is not checked before shift fields are accessed.
- The duplicate check and insertion are not concurrency-safe.
- Timestamp conversion depends on the machine's local time zone.
- Timestamp validation and whole-second truncation are missing.
- The attendance date does not correctly handle IST and overnight shifts.
- Status values are not restricted to `PRESENT`, `WFH`, and `ON_DUTY`.
- The response includes an `id` field, which is not part of the API contract.
- The required attendance unique index is missing.

**Fix:** Validate the employee and request, apply the time rules, create the appropriate indexes, and handle duplicate-key errors.

## 5. Attendance Listing
**Location:** `list_attendance()`

- The endpoint loads all matching records into Python memory.
- Sorting and pagination are performed in Python instead of MongoDB.
- The sort order does not include the required `emp_code` tie-breaker.
- `_id` is exposed indirectly through an `id` field.
- Date-range ordering and query-parameter validation are missing.
- Pagination limits are not enforced.

**Fix:** Build the MongoDB filter, count the filtered records, sort and paginate in MongoDB, and serialize only the documented response fields.

## 6. Derived-Field Calculations
**Locations:** `compute_late_minutes()`, `compute_work_hours()`, `compute_overtime()`

- Work-hour rounding does not explicitly implement half-up rounding.
- Overtime is not restricted to cases where the excess is at least 30 minutes.
- Overnight shifts are not handled correctly by the overtime calculation.
- Time calculations do not consistently apply the contract's truncation and time-zone rules.

**Fix:** Centralize the attendance calculations and test the exact boundary cases in rules R1–R5.

## 7. Missing Endpoints
The starter implementation does not implement:

- `POST /attendance/punch-out`
- `PATCH /attendance/{emp_code}/{date}`
- `GET /analytics/employees/{emp_code}/monthly`
- `GET /analytics/departments/summary`
- `GET /analytics/leaderboard/late`
- `GET /analytics/departments/{department}/trend`
- `GET /admin/explain/{endpoint}`

These endpoints must follow the request schemas, response schemas, status codes, aggregation requirements, and business rules in `openapi.yaml`.

## 8. Database Indexes and Performance
The starter code does not create the required indexes. The assignment requires indexed queries and explain plans that avoid collection scans on the large seeded dataset.

**Fix:** Design indexes around the actual filters, sort order, and aggregation pipelines. Verify them using the explain endpoint.

## Conclusion
The starter code covers only part of the API and has correctness, validation, concurrency, serialization, and performance problems. The implementation should be corrected against `openapi.yaml` and `DATA_MODEL.md`, with targeted tests for business-rule boundaries and concurrent requests.