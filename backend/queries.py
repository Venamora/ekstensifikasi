"""Shared filters keep the directory, summaries, map and exports consistent."""
import json
from typing import Annotated

from fastapi import HTTPException, Query

from .database import get_meta

SORT_COLUMNS = {"businessName", "businessCategory", "kbliCode", "businessType", "district",
                "subdistrict", "address", "employeeCount", "dataPeriod", "areaId"}


def filters(
    period: str | None = Query(None, pattern=r"^(\d{4}|all)$"),
    q: str = Query("", max_length=200),
    city: str = Query("", max_length=100),
    district: str = Query("", max_length=100),
    subdistrict: str = Query("", max_length=100),
    area_id: str = Query("", max_length=30),
    category: Annotated[list[str] | None, Query()] = None,
    kbli: Annotated[list[str] | None, Query()] = None,
    business_type: Annotated[list[str] | None, Query()] = None,
    kbli_description: str = Query("", max_length=200),
    name: str = Query("", max_length=200),
    address: str = Query("", max_length=200),
    min_employees: int | None = Query(None, ge=0, le=999999999),
    max_employees: int | None = Query(None, ge=0, le=999999999),
    employee_data: str = Query("all", pattern="^(all|available|missing)$"),
    area_quality: str = Query("all", pattern="^(all|matched|unmatched)$"),
):
    if min_employees is not None and max_employees is not None and min_employees > max_employees:
        raise HTTPException(422, "Minimum TKI tidak boleh melebihi maksimum.")
    for values in (category, kbli, business_type):
        if values and (len(values) > 50 or any(len(v) > 200 for v in values)):
            raise HTTPException(422, "Terlalu banyak nilai filter.")
    return locals()


def where(db, selection):
    conditions, args = [], []
    snapshot = get_meta(db, "snapshot", {})
    period = selection.get("period") or next(iter(snapshot.get("periods", [])), None)
    if period and period != "all":
        conditions.append("b.dataPeriod=?")
        args.append(period)
    for param, column in (("city", "city"), ("district", "district"), ("subdistrict", "subdistrict"), ("area_id", "areaId")):
        if selection.get(param):
            conditions.append(f"b.{column}=? COLLATE NOCASE")
            args.append(selection[param])
    for param, column in (("category", "businessCategory"), ("kbli", "kbliCode"), ("business_type", "businessType")):
        values = selection.get(param)
        if values:
            conditions.append(f"b.{column} IN ({','.join('?' for _ in values)})")
            args.extend(values)
    def contains(value):
        return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    if selection.get("q"):
        conditions.append("(b.businessName LIKE ? ESCAPE '\\' OR b.address LIKE ? ESCAPE '\\' OR b.kbliDescription LIKE ? ESCAPE '\\')")
        args.extend([contains(selection["q"])] * 3)
    for param, column in (("name", "businessName"), ("address", "address"), ("kbli_description", "kbliDescription")):
        if selection.get(param):
            conditions.append(f"b.{column} LIKE ? ESCAPE '\\'")
            args.append(contains(selection[param]))
    for param, operator in (("min_employees", ">="), ("max_employees", "<=")):
        if selection.get(param) is not None:
            conditions.append(f"b.employeeCount {operator} ?")
            args.append(selection[param])
    if selection.get("employee_data") in ("available", "missing"):
        conditions.append("b.employeeCount IS " + ("NOT NULL" if selection["employee_data"] == "available" else "NULL"))
    if selection.get("area_quality") in ("matched", "unmatched"):
        conditions.append("b.areaId IS " + ("NOT NULL" if selection["area_quality"] == "matched" else "NULL"))
    return " AND ".join(conditions) or "1=1", args, period


def order_by(sort, direction):
    if sort not in SORT_COLUMNS or direction not in ("asc", "desc"):
        raise HTTPException(422, "Kolom atau arah pengurutan tidak valid.")
    return f"b.{sort} IS NULL, b.{sort} COLLATE NOCASE {direction.upper()}, b.id"


def business(row):
    result = dict(row)
    result["quality"] = json.loads(result["quality"])
    return result
