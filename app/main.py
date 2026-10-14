"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import json
from fastapi.responses import JSONResponse
from bson.json_util import dumps
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError



load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


def create_indexes():
    """Create indexes needed for uniqueness and common queries."""
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uq_employees_emp_code",
    )

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uq_attendance_emp_date",
    )

    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)],
        name="ix_attendance_date_emp",
    )

    db.attendance_logs.create_index(
        [("status", 1), ("date", -1), ("emp_code", 1)],
        name="ix_attendance_status_date_emp",
    )

    db.employees.create_index(
        [("department", 1), ("emp_code", 1)],
        name="ix_employees_department_emp",
    )
@app.on_event("startup")
def startup():
    create_indexes()

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    attendance_day: str,
) -> int:
    local = punch_in.astimezone(IST)
    h, m = map(int, shift_start.split(":"))
    day = date.fromisoformat(attendance_day)

    start = datetime(day.year, day.month, day.day, h, m, tzinfo=IST)

    elapsed = (local - start).total_seconds()
    if elapsed <= 600:
        return 0

    return int(elapsed // 60)



def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    from decimal import Decimal, ROUND_HALF_UP

    seconds = (punch_out - punch_in).total_seconds()
    hours = Decimal(str(seconds / 3600))
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    date_str: str,
) -> int:
    day = date.fromisoformat(date_str)
    h, m = map(int, shift_end.split(":"))
    overnight = shift_end <= shift_start
    end_day = day + timedelta(days=1) if overnight else day
    shift_end_dt = datetime(
        end_day.year, end_day.month, end_day.day, h, m, tzinfo=IST
    )
    local_out = punch_out.astimezone(IST)
    elapsed = (local_out - shift_end_dt).total_seconds()
    return int(elapsed // 60) if elapsed >= 1800 else 0



# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(
        default="09:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    shift_end: str = Field(
        default="18:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    joined_on: date

    @field_validator("name", "department")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("Shift start and end must differ")
        return self



class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: str = Field(
        default="PRESENT",
        pattern=r"^(PRESENT|WFH|ON_DUTY)$",
    )
   
    @field_validator("punched_at")
    @classmethod
    def validate_punched_at(cls, value):
        if value is None:
            return value
        if value < 100_000_000_000 or value > 4_102_444_800_000:
            raise ValueError("Invalid epoch-millisecond timestamp")
        return value
 

class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emp_code: str
    punched_at: Optional[StrictInt] = None
    @field_validator("punched_at")
    @classmethod
    def validate_punched_at(cls, value):
        if value is None:
            return value
        if value < 100_000_000_000 or value > 4_102_444_800_000:
            raise ValueError("Invalid epoch-millisecond timestamp")
        return value  


class AttendancePatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Optional[str] = Field(
        default=None,
        pattern=r"^(PRESENT|WFH|ON_DUTY|ABSENT|LEAVE)$",
    )
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
    
    @field_validator("punch_in", "punch_out")
    @classmethod
    def validate_punch_timestamps(cls, value):
        if value is None:
            return value
        if value < 100_000_000_000 or value > 4_102_444_800_000:
            raise ValueError("Invalid epoch-millisecond timestamp")
        return value

    
# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    response = body.model_dump(mode="json")

    doc = body.model_dump(mode="json")
    doc["created_at"] = datetime.now(timezone.utc)
    response["created_at"] = int(doc["created_at"].timestamp() * 1000)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists"
        )

    return response



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {}
    if department is not None:
        query["department"] = department

    total = db.employees.count_documents(query)
    skip = (page - 1) * page_size

    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

   
    for item in items:
        created_at = item.get("created_at")
        if created_at is not None:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            item["created_at"] = int(created_at.timestamp() * 1000)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    if body.punched_at is not None:
        ts = datetime.fromtimestamp(body.punched_at / 1000,    tz=timezone.utc).replace(microsecond=0)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    local_ts = ts.astimezone(IST)
    local_time = local_ts.strftime("%H:%M")
    d = local_ts.date().isoformat()

    if emp["shift_end"] <= emp["shift_start"] and local_time < emp["shift_end"]:
        d = (local_ts.date() - timedelta(days=1)).isoformat()
    # if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
    #     raise HTTPException(409, "already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], d),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        res = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="already punched in for this date",
        )
    return {key: value for key, value in doc.items() if key != "_id"}




@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is not None:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000, tz=timezone.utc
        ).replace(microsecond=0)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$ne": None, "$lte": ts},
            "punch_out": None,
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        previous = db.attendance_logs.find_one(
            {"emp_code": body.emp_code, "punch_out": {"$ne": None}},
            sort=[("punch_in", -1)],
        )
        if previous:
            raise HTTPException(status_code=409, detail="Already punched out")
        raise HTTPException(status_code=404, detail="No open punch-in found")

    punch_in_time = record["punch_in"]
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    elapsed = (ts - punch_in_time).total_seconds()
    if elapsed <= 0 or elapsed > 24 * 60 * 60:
        raise HTTPException(status_code=422, detail="Invalid punch-out time")

    work_hours = compute_work_hours(punch_in_time, ts)
    overtime = compute_overtime(
        ts,
        emp["shift_start"],
        emp["shift_end"],
        record["date"],
    )
    half_day = work_hours < 4.50

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(status_code=409, detail="Already punched out")

    record.update({
        "punch_out": ts,
        "work_hours": work_hours,
        "overtime_minutes": overtime,
        "half_day": half_day,
    })
    record.pop("_id", None)
    record["punch_in"] = int(punch_in_time.timestamp() * 1000)
    record["punch_out"] = int(ts.timestamp() * 1000)
    record.setdefault("history", [])
    return record

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=422, detail="Invalid date range")

    if status:
        q["status"] = status

    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size

    docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    for item in docs:
        for field in ("punch_in", "punch_out"):
            value = item.get(field)
            if value is not None:
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                item[field] = int(value.timestamp() * 1000)

        item.setdefault("history", [])
        item.setdefault("half_day", False)

    return {
        "items": docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: AttendancePatchIn,
):
    from fastapi import HTTPException
    from datetime import date as Date

    try:
        Date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date")

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date,
    })
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    supplied = body.model_fields_set
    changes = {}

    current_status = record.get("status", "PRESENT")
    new_status = body.status if "status" in supplied else current_status

    current_in = record.get("punch_in")
    current_out = record.get("punch_out")

    def to_utc(value):
        if value is None:
            return None
        return datetime.fromtimestamp(
            value / 1000, tz=timezone.utc
        ).replace(microsecond=0)

    new_in = to_utc(body.punch_in) if "punch_in" in supplied else current_in
    new_out = to_utc(body.punch_out) if "punch_out" in supplied else current_out

    if new_status in ("ABSENT", "LEAVE"):
        if ("punch_in" in supplied and body.punch_in is not None) or (
            "punch_out" in supplied and body.punch_out is not None
        ):
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot have punch times",
            )
        new_in = None
        new_out = None
    elif new_in is None:
        raise HTTPException(
            status_code=422,
            detail="PRESENT, WFH, and ON_DUTY require punch_in",
        )

    if new_in is not None:
        if new_in.tzinfo is None:
            new_in = new_in.replace(tzinfo=timezone.utc)
        local_in = new_in.astimezone(IST)
        expected_date = local_in.date().isoformat()

        if employee["shift_end"] <= employee["shift_start"]:
            if local_in.strftime("%H:%M") < employee["shift_end"]:
                expected_date = (local_in.date() - timedelta(days=1)).isoformat()

        if expected_date != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in does not match the attendance date",
            )

    if new_out is not None:
        if new_in is None:
            raise HTTPException(
                status_code=422,
                detail="punch_out requires punch_in",
            )
        if new_out.tzinfo is None:
            new_out = new_out.replace(tzinfo=timezone.utc)
        elapsed = (new_out - new_in).total_seconds()
        if elapsed <= 0 or elapsed > 86400:
            raise HTTPException(
                status_code=422,
                detail="punch_out must be after punch_in and within 24 hours",
            )

    if new_status != current_status:
        changes["status"] = {"from": current_status, "to": new_status}

    def same_time(a, b):
        if a is None or b is None:
            return a is None and b is None
        if a.tzinfo is None:
            a = a.replace(tzinfo=timezone.utc)
        if b.tzinfo is None:
            b = b.replace(tzinfo=timezone.utc)
        return a == b

    if not same_time(new_in, current_in):
        changes["punch_in"] = {
            "from": current_in.isoformat() if current_in else None,
            "to": new_in.isoformat() if new_in else None,
        }

    if not same_time(new_out, current_out):
        changes["punch_out"] = {
            "from": current_out.isoformat() if current_out else None,
            "to": new_out.isoformat() if new_out else None,
        }

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="No attendance changes supplied",
        )

    if new_status in ("ABSENT", "LEAVE"):
        late_minutes = 0
        overtime_minutes = 0
        work_hours = None
        half_day = False
    else:
        if new_in is None:
          raise HTTPException(
            status_code=422,
            detail="punch_in is required",
        )
        late_minutes = compute_late_minutes(
            new_in, employee["shift_start"], date
        )

       
        if new_out is not None:
            if new_in is None:
                raise HTTPException(
                    status_code=422,
                    detail="punch_in is required to calculate work hours",
                )

            work_hours = compute_work_hours(new_in, new_out)
            overtime_minutes = compute_overtime(
                new_out,
                employee["shift_start"],
                employee["shift_end"],
                date,
            )
            half_day = work_hours < 4.50

        else:
            work_hours = None
            overtime_minutes = 0
            half_day = False

    updated_fields = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "work_hours": work_hours,
        "half_day": half_day,
    }

    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "status": record.get("status"),
            "punch_in": current_in,
            "punch_out": current_out,
            "history": record.get("history", []),
        },
        {
            "$set": updated_fields,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the request",
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )
    if updated is None:
      raise HTTPException(
        status_code=404,
        detail="Attendance record not found",
    )

    updated["punch_in"] = (
        int(updated["punch_in"].replace(tzinfo=timezone.utc).timestamp() * 1000)
        if updated.get("punch_in") else None
    )
    updated["punch_out"] = (
        int(updated["punch_out"].replace(tzinfo=timezone.utc).timestamp() * 1000)
        if updated.get("punch_out") else None
    )
    updated["history"] = updated.get("history", [])
    updated["half_day"] = updated.get("half_day", False)

    for entry in updated["history"]:
        if entry.get("at") and entry["at"].tzinfo is None:
            entry["at"] = entry["at"].replace(tzinfo=timezone.utc)
        if entry.get("at"):
            entry["at"] = int(entry["at"].timestamp() * 1000)

    return updated

# ---------------------------------------------------------------------------
# Employee Monthly Analytics
# ---------------------------------------------------------------------------

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    employee = db.employees.find_one({"emp_code": emp_code})

    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    year, month_number = map(int, month.split("-"))
    month_start = date(year, month_number, 1)

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    month_end = next_month - timedelta(days=1)

    joined_on = date.fromisoformat(employee["joined_on"])
    working_start = max(month_start, joined_on)

    working_days = 0
    current_day = working_start
    while current_day <= month_end:
        if current_day.weekday() < 5:
            working_days += 1
        current_day += timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": next_month.isoformat(),
                },
            }
        },
        {
            "$addFields": {
                "date_obj": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d",
                    }
                }
            }
        },
        {
            "$addFields": {
                "is_weekday": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$date_obj"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$date_obj"}, 6]},
                    ]
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    "$is_weekday",
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]

    results = list(db.attendance_logs.aggregate(pipeline))
    stats = results[0] if results else {}

    present_days = stats.get("present_days", 0)

    attendance_pct = None
    if working_days > 0:
        attendance_pct = float(
            (Decimal(str(present_days)) * Decimal("100") /
             Decimal(working_days)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": stats.get("leave_days", 0),
        "late_count": stats.get("late_count", 0),
        "total_late_minutes": stats.get("total_late_minutes", 0),
        "total_overtime_minutes": stats.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    year, month_number = map(int, month.split("-"))
    month_start = date(year, month_number, 1)

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    month_end = next_month - timedelta(days=1)

    employee_match: dict = {"joined_on": {"$lte": month_end.isoformat()}}
    if department:
        employee_match["department"] = department

    employees = list(
        db.employees.find(
            employee_match,
            {"_id": 0, "emp_code": 1, "department": 1},
        )
    )

    departments = {}
    for employee in employees:
        dept = employee["department"]
        if dept not in departments:
            departments[dept] = {
                "department": dept,
                "headcount": 0,
                "present_days": 0,
                "leave_count": 0,
                "on_duty_count": 0,
                "late_count": 0,
                "total_late_minutes": 0,
                "avg_work_hours": None,
            }
        departments[dept]["headcount"] += 1

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": next_month.isoformat(),
                }
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
        {"$match": {"employee.joined_on": {"$lte": month_end.isoformat()}}},
        {
            "$addFields": {
                "date_obj": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d",
                    }
                }
            }
        },
        {
            "$addFields": {
                "is_weekday": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$date_obj"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$date_obj"}, 6]},
                    ]
                }
            }
        },
    ]

    if department:
        pipeline.append({"$match": {"employee.department": department}})

    pipeline.extend([
        {
            "$group": {
                "_id": "$employee.department",
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    "$is_weekday",
                                ]
                            },
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                            0,
                        ]
                    }
                },
                "leave_count": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "on_duty_count": {
                    "$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "avg_work_hours": {
                    "$avg": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$ne": ["$work_hours", None]},
                                ]
                            },
                            "$work_hours",
                            None,
                        ]
                    }
                },
            }
        }
    ])

    for result in db.attendance_logs.aggregate(pipeline):
        dept = result["_id"]
        if dept in departments:
            for key in [
                "present_days",
                "leave_count",
                "on_duty_count",
                "late_count",
                "total_late_minutes",
                "avg_work_hours",
            ]:
                departments[dept][key] = result.get(key, 0)

    output = []
    for item in departments.values():
        avg_hours = item["avg_work_hours"]
        item["avg_work_hours"] = (
            float(
                Decimal(str(avg_hours)).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
            )
            if avg_hours is not None
            else None
        )
        output.append(item)

    output.sort(key=lambda item: item["department"])

    return {
        "month": month,
        "items": output,
        "total": len(output),
    }


@app.get("/analytics/late-leaderboard")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=100),
    department: Optional[str] = None,
):
    year, month_number = map(int, month.split("-"))
    month_start = date(year, month_number, 1)

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": next_month.isoformat(),
                },
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department:
        pipeline.append({"$match": {"employee.department": department}})

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}},
                },
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1,
            }
        },
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "rank": 1,
            }
        },
    ])

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items,
        "total": len(items),
    }


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    date_from: str = Query(...),
    date_to: str = Query(...),
):
    try:
        start_day = date.fromisoformat(date_from)
        end_day = date.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(status_code=422, detail="Dates must use YYYY-MM-DD format")

    if start_day.isoformat() != date_from or end_day.isoformat() != date_to:
        raise HTTPException(status_code=422, detail="Dates must use YYYY-MM-DD format")

    if end_day < start_day:
        raise HTTPException(status_code=422, detail="date_to must be on or after date_from")

    if (end_day - start_day).days > 91:
        raise HTTPException(status_code=422, detail="Date range cannot exceed 92 days")

    if db.employees.count_documents({
    "department": department,
    "joined_on": {"$lte": date_to},
    }) == 0:
        raise HTTPException(status_code=404, detail="Department not found")

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": date_from,
                    "$lte": date_to,
                }
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {
            "$addFields": {
                "date_obj": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d",
                    }
                }
            }
        },
        {
            "$group": {
                "_id": "$date_obj",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$densify": {
                "field": "_id",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [
                        datetime.combine(start_day, datetime.min.time()),
                        datetime.combine(end_day, datetime.min.time()) + timedelta(days=1),
                    ],
                },
            }
        },
        {
            "$set": {
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
                "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$_id"}},
                "is_working_day": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$_id"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$_id"}, 6]},
                    ]
                },
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"trend_date": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$joined_on", "$$trend_date"]},
                                ]
                            }
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "headcount_data",
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_data.count", 0]},
                        0,
                    ]
                }
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                        {"$divide": ["$present_count", "$headcount"]},
                        None,
                    ]
                }
            }
        },
        {"$sort": {"_id": 1}},
        {
            "$setWindowFields": {
                "sortBy": {"_id": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
    ]

    items = list(db.attendance_logs.aggregate(pipeline))

    for item in items:
        for key in ("attendance_rate", "moving_avg_7d"):
            value = item.get(key)
            item[key] = (
                float(Decimal(str(value)).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                ))
                if value is not None
                else None
            )

    return {
        "department": department,
        "date_from": date_from,
        "date_to": date_to,
        "items": items,
        "total": len(items),
    }

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    month: Optional[str] = None,
    emp_code: Optional[str] = None,
    department: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    allowed = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed:
        raise HTTPException(status_code=422, detail="Unsupported endpoint")

    if endpoint == "attendance_list":
        query = {}

        if emp_code:
            query["emp_code"] = emp_code
        if department:
            employee_codes = db.employees.distinct(
                "emp_code", {"department": department}
            )
            query["emp_code"] = {"$in": employee_codes}
        if date_from:
            query.setdefault("date", {})["$gte"] = date_from
        if date_to:
            query.setdefault("date", {})["$lte"] = date_to

        
        explanation = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            verbosity="executionStats",
        )


    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month are required",
            )

        year, month_number = map(int, month.split("-"))
        month_start = date(year, month_number, 1)
        next_month = (
            date(year + 1, 1, 1)
            if month_number == 12
            else date(year, month_number + 1, 1)
        )

        explanation = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": [
                    {
                        "$match": {
                            "emp_code": emp_code,
                            "date": {
                                "$gte": month_start.isoformat(),
                                "$lt": next_month.isoformat(),
                            },
                        }
                    },
                    {"$sort": {"date": 1}},
                ],
                "cursor": {},
            },
            verbosity="executionStats",
        )

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(
                status_code=422, detail="month is required"
            )

        year, month_number = map(int, month.split("-"))
        month_start = date(year, month_number, 1)
        next_month = (
            date(year + 1, 1, 1)
            if month_number == 12
            else date(year, month_number + 1, 1)
        )

        explanation = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": [
                    {
                        "$match": {
                            "date": {
                                "$gte": month_start.isoformat(),
                                "$lt": next_month.isoformat(),
                            }
                        }
                    },
                    {"$sort": {"date": 1}},
                ],
                "cursor": {},
            },
            verbosity="executionStats",
        )

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(
                status_code=422, detail="month is required"
            )

        year, month_number = map(int, month.split("-"))
        month_start = date(year, month_number, 1)
        next_month = (
            date(year + 1, 1, 1)
            if month_number == 12
            else date(year, month_number + 1, 1)
        )

        query = {
            "date": {
                "$gte": month_start.isoformat(),
                "$lt": next_month.isoformat(),
            },
            "late_minutes": {"$gt": 0},
        }

        explanation = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": [
                    {"$match": query},
                    {
                        "$group": {
                            "_id": "$emp_code",
                            "total_late_minutes": {"$sum": "$late_minutes"},
                        }
                    },
                    {"$sort": {"total_late_minutes": -1}},
                ],
                "cursor": {},
            },
            verbosity="executionStats",
        )

    else:
        if not department or not date_from or not date_to:
            raise HTTPException(
                status_code=422,
                detail="department, date_from and date_to are required",
            )

        explanation = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": [
                    {
                        "$match": {
                            "date": {
                                "$gte": date_from,
                                "$lte": date_to,
                            }
                        }
                    },
                    {"$sort": {"date": 1}},
                ],
                "cursor": {},
            },
            verbosity="executionStats",
        )

    return JSONResponse(content=json.loads(dumps(explanation)))

# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
