# Starter Code Review

I went through the starter code my colleague left behind and found quite a few issues — some minor, some pretty serious. Here's everything I spotted and how I fixed it.

---

## 1. Health Check

The original `/health` endpoint just returned `{"status": "ok"}` no matter what. It never actually checked if MongoDB was reachable, so the app could say "I'm fine" while the database was completely down.

I added a `client.admin.command("ping")` call wrapped in a try/except. Now it returns 200 only when MongoDB actually responds, and 503 otherwise. The grader polls this before running tests, so getting it right matters.

---

## 2. Employee Creation

This one had several problems:

- There was a find-then-insert pattern for checking duplicate `emp_code`. Two requests hitting at the same time could both pass the check and both insert — classic race condition.
- No unique index on `emp_code` at all, so the DB couldn't catch it either.
- `created_at` was using `datetime.now()` without a timezone, which is a silent bug waiting to happen depending on where the server runs.
- Basically no input validation — any string could be an `emp_code`, email wasn't checked, `shift_start` could equal `shift_end`, etc.

I fixed all of this. There's now a unique index on `emp_code` created at startup. The Pydantic model validates the `emp_code` format (`EMP` + 4–6 digits), email pattern, string lengths, and rejects `shift_start == shift_end`. `DuplicateKeyError` from MongoDB gets caught and turned into a 409. `created_at` uses `datetime.now(timezone.utc)`.

---

## 3. Employee Listing

The pagination was broken from the start — `skip = page * page_size` means page 1 already skips the first batch of records. Should be `(page - 1) * page_size`.

Also, the total count was counting *all* employees even when a department filter was applied, so the pagination metadata was wrong. And results weren't sorted by `emp_code`, so the order was basically random. No limits on `page_size` either — someone could request page_size=10000.

I fixed the skip formula, applied the department filter to both the count and the query, added `sort("emp_code", 1)`, and put proper bounds on the query params (page ≥ 1, page_size 1–100).

---

## 4. Punch-In

This was probably the messiest endpoint. Issues I found:

- If the employee didn't exist, the code tried to access `emp["shift_start"]` on `None` — instant crash instead of a clean 404.
- The timestamp conversion used `datetime.fromtimestamp()` without specifying a timezone, so it depended on whatever the server's local TZ was.
- No validation on `punched_at` — you could pass in a value that looks like seconds instead of milliseconds and it would silently do the wrong thing.
- Microseconds weren't being stripped (R1 says truncate to whole seconds).
- The attendance date logic for overnight shifts wasn't right.
- Any string was accepted as `status` — not limited to PRESENT/WFH/ON_DUTY.
- The response was leaking MongoDB's `_id` as an `id` field.
- No unique index on `(emp_code, date)`, so concurrent punch-ins could create duplicates.

I rewrote it properly. The Pydantic model validates `punched_at` range and restricts `status`. Timestamps are converted with explicit UTC and truncated with `.replace(microsecond=0)`. The attendance date calculation handles IST and overnight shifts correctly. And the unique index catches any race condition — `DuplicateKeyError` → 409.

---

## 5. Attendance Listing

The original code loaded *every* matching record into memory, then sorted and paginated in Python. On a 100k-record collection, that's a disaster.

Other issues: no `emp_code` tie-breaker in the sort, `_id` leaking through, `date_from > date_to` being silently accepted, and no pagination limits.

Now everything happens in MongoDB — filter, sort by `(date desc, emp_code asc)`, skip, limit. The count query uses the same filter. Dates are validated, `_id` is excluded, and BSON dates are properly converted to epoch milliseconds in the response.

---

## 6. Derived-Field Calculations

The helper functions had subtle bugs:

- `compute_work_hours` used Python's built-in `round()`, which does banker's rounding (round half to even). The spec requires half-up rounding. Might not seem like a big deal, but it fails on exact boundary cases like 4.545 hours.
- `compute_overtime` didn't enforce the 30-minute minimum — any amount of time past shift_end counted as overtime. R3 says it should be zero unless the excess is at least 30 minutes.
- Overnight shifts weren't handled in overtime — the shift-end time was always placed on the same calendar day as the shift start, which is wrong for a 22:00–06:00 shift.
- The grace period boundary in `compute_late_minutes` had an edge case issue.

I fixed all three functions. Work hours use `Decimal` with `ROUND_HALF_UP`. Overtime checks `elapsed >= 1800` seconds. Overnight detection uses `shift_end <= shift_start` and adds a day to the end time. Late minutes uses `<= 600` seconds as the grace boundary.

---

## 7. Missing Endpoints

The starter code was missing more than half the API. I implemented everything:

**Punch-out** — finds the most recent open record for the employee, validates the timing (must be after punch-in, within 24 hours), computes work_hours/overtime/half_day, and uses an atomic update with `{"punch_out": None}` in the filter to prevent double punch-outs.

**Regularization (PATCH)** — this was the trickiest one. It handles status changes, punch time corrections, all the validation rules (ABSENT/LEAVE can't have punch times, presence statuses need a punch_in, punch_in must match the attendance date, etc.). After applying changes, it recomputes all derived fields and appends a history entry with only the fields that actually changed. I used optimistic concurrency — the update filter includes the current values, so if someone else modified the record in between, it fails with 409.

**Employee monthly analytics** — counts working days from `joined_on` to month end (Mon–Fri only), runs a MongoDB aggregation for present_days (half-day = 0.5, weekday only), leave_days, late counts, and overtime totals. Attendance percentage uses half-up rounding.

**Department summary** — headcount comes from the employees collection directly (not from logs), so people with zero attendance records are still counted. The aggregation uses `$lookup` to join logs with employees, then groups by department. `avg_work_hours` is the mean over present-status records that actually have a work_hours value.

**Late leaderboard** — aggregates total late minutes per employee, uses `$setWindowFields` with `$rank` for standard competition ranking. The `limit` filter is on rank, not array position, so ties at the boundary are all included.

**Department trend** — uses `$densify` to fill in every calendar day (including weekends and days with no records). Headcount per day comes from a sub-pipeline lookup on employees. Attendance rate is only computed on working days with headcount > 0. The 7-day moving average uses a window of the current row plus up to 6 preceding rows within the requested range.

**Explain endpoint** — runs MongoDB's `explain` command with `executionStats` for whichever endpoint is specified. It builds the same query/pipeline that the actual endpoint would use.

---

## 8. Indexes

The starter had zero indexes beyond the default `_id`. I created five:

- **`uq_employees_emp_code`** — unique on `emp_code`. Needed for the uniqueness constraint and fast lookups.
- **`uq_attendance_emp_date`** — unique on `(emp_code, date)`. Prevents duplicate attendance records and makes concurrent punch-in safe.
- **`ix_attendance_date_emp`** — `date` descending, `emp_code` ascending. Matches the attendance listing sort order exactly.
- **`ix_attendance_status_date_emp`** — adds `status` as a prefix for when the attendance list is filtered by status.
- **`ix_employees_department_emp`** — `department` + `emp_code` for department-filtered queries.

These cover all five endpoints that the explain grader checks. No more collection scans.

---

## Bottom Line

The starter code was a rough draft that only handled the happy path for a few endpoints. It had race conditions, broken pagination, wrong rounding, missing validation, and no indexes. I fixed all the bugs, implemented the missing endpoints, and made sure everything follows the OpenAPI spec and business rules R1–R10.