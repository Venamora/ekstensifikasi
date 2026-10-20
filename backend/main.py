import json
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .exports import export_file, safe_cell
from .database import ROOT, connect, get_meta, initialize, set_meta
from .ingestion import LOCK, now, refresh_reserved, reserve_refresh
from .queries import business, filters, order_by, where

load_dotenv(ROOT / ".env")


@asynccontextmanager
async def lifespan(app):
    initialize()
    # This MVP runs one worker. A previous process can leave a running flag behind.
    with connect() as db:
        if get_meta(db, "refreshState") == "running" and not LOCK.locked():
            set_meta(db, "refreshState", "error")
            set_meta(db, "lastError", "Sinkronisasi sebelumnya terhenti. Silakan refresh ulang.")
    yield


app = FastAPI(title="Mandiri GeoProspect", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None,
              description="Public registry intelligence; not confirmation of active businesses or banking relationships.")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=os.getenv(
    "GEOPROSPECT_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver").split(","))


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method in ("POST", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")
        if origin and origin != expected:
            return JSONResponse({"detail": "Origin tidak diizinkan."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; "
        "font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def health():
    with connect() as db:
        db.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/data/status")
def data_status():
    with connect() as db:
        return {"snapshot": get_meta(db, "snapshot"), "boundaries": get_meta(db, "boundaries"),
                "lastAttempt": get_meta(db, "lastAttempt"), "lastError": get_meta(db, "lastError"),
                "boundaryError": get_meta(db, "boundaryError"), "refreshState": get_meta(db, "refreshState", "idle"),
                "refreshConfigured": bool(os.getenv("GEOPROSPECT_ADMIN_TOKEN")),
                "areaCount": db.execute("SELECT COUNT(*) FROM areas WHERE id LIKE '3174%'").fetchone()[0]}


@app.post("/api/data/refresh", status_code=202)
def data_refresh(tasks: BackgroundTasks, x_admin_token: str = Header("")):
    expected = os.getenv("GEOPROSPECT_ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(503, "Atur GEOPROSPECT_ADMIN_TOKEN di server sebelum menggunakan refresh web.")
    if not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(401, "Token administrator tidak valid.")
    error = reserve_refresh()
    if error:
        raise HTTPException(429, error)
    tasks.add_task(refresh_reserved)
    return {"status": "running"}


@app.get("/api/filters")
def filter_options():
    with connect() as db:
        result = {}
        for name, column in (("periods", "dataPeriod"), ("categories", "businessCategory"),
                             ("kbli", "kbliCode"), ("businessTypes", "businessType")):
            result[name] = [r[0] for r in db.execute(f"SELECT DISTINCT {column} FROM businesses WHERE {column} IS NOT NULL ORDER BY {column}")]
        result["periods"].reverse()
        result["locations"] = [dict(r) for r in db.execute("SELECT DISTINCT district,subdistrict,areaId FROM businesses ORDER BY district,subdistrict")]
        result["areas"] = [dict(r) for r in db.execute("SELECT id,district,subdistrict FROM areas WHERE id LIKE '3174%' ORDER BY district,subdistrict")]
        return result


@app.get("/api/businesses")
def businesses(selection: dict = Depends(filters), page: int = Query(1, ge=1, le=1000000),
               page_size: int = Query(25, ge=1, le=100), sort: str = "businessName", direction: str = "asc"):
    ordering = order_by(sort, direction)
    with connect() as db:
        condition, args, period = where(db, selection)
        total = db.execute(f"SELECT COUNT(*) FROM businesses b WHERE {condition}", args).fetchone()[0]
        rows = db.execute(f"SELECT b.*, EXISTS(SELECT 1 FROM shortlist s WHERE s.businessRef=b.id) AS shortlisted FROM businesses b WHERE {condition} ORDER BY {ordering} LIMIT ? OFFSET ?",
                          [*args, page_size, (page-1)*page_size]).fetchall()
        return {"items": [business(r) for r in rows], "total": total, "page": page, "pageSize": page_size, "period": period}


@app.get("/api/businesses/{record_id}")
def business_detail(record_id: str):
    with connect() as db:
        row = db.execute("SELECT b.*, EXISTS(SELECT 1 FROM shortlist s WHERE s.businessRef=b.id) AS shortlisted FROM businesses b WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Record tidak tersedia pada snapshot aktif.")
        return business(row)


@app.get("/api/statistics")
def statistics(selection: dict = Depends(filters)):
    with connect() as db:
        condition, args, period = where(db, selection)
        row = db.execute(f"""SELECT COUNT(*) total, COUNT(areaId) matched,
            COUNT(employeeCount) employeeAvailable, SUM(employeeCount) reportedEmployees,
            COUNT(DISTINCT businessCategory) categories,
            SUM(EXISTS(SELECT 1 FROM shortlist s WHERE s.businessRef=b.id)) shortlisted
            FROM businesses b WHERE {condition}""", args).fetchone()
        base_condition, base_args, _ = where(db, {"period": period})
        total = db.execute(f"SELECT COUNT(*) FROM businesses b WHERE {base_condition}", base_args).fetchone()[0]
        categories = [dict(r) for r in db.execute(f"SELECT COALESCE(businessCategory,'Tidak tersedia') name, COUNT(*) count FROM businesses b WHERE {condition} GROUP BY businessCategory ORDER BY count DESC, name LIMIT 8", args)]
        return {**dict(row), "shortlisted": row["shortlisted"] or 0, "period": period,
                "periodTotal": total, "unmatched": row["total"]-row["matched"], "categoryBreakdown": categories}


@app.get("/api/areas/geojson")
def geojson(scope: Literal["jaksel", "jabodetabek"] = "jaksel"):
    with connect() as db:
        from .geography import all_areas
        meta = {a['id']: a for a in all_areas(db)}
        features = []
        for row in db.execute("SELECT id,feature FROM areas"):
            if scope == 'jaksel' and not row['id'].startswith('3174'):
                continue
            feature = json.loads(row['feature'])
            feature['properties'].update(meta[row['id']])
            features.append(feature)
        return {"type": "FeatureCollection", "features": features}


@app.get("/api/areas")
@app.get("/api/opportunities")
def opportunities(selection: dict = Depends(filters), level: Literal["district", "subdistrict"] = "subdistrict"):
    with connect() as db:
        condition, args, period = where(db, selection)
        column = "b.areaId" if level == "subdistrict" else "b.district"
        aggregates = {r["groupId"]: dict(r) for r in db.execute(f"""SELECT {column} groupId,
            COUNT(*) count, COUNT(DISTINCT b.businessCategory) categories,
            SUM(b.employeeCount) employees, COUNT(b.employeeCount) employeeAvailable,
            SUM(EXISTS(SELECT 1 FROM shortlist s WHERE s.businessRef=b.id)) shortlisted
            FROM businesses b WHERE ({condition}) AND b.areaId IS NOT NULL GROUP BY {column}""", args)}
        categories = {}
        for r in db.execute(f"SELECT {column} groupId, b.businessCategory category, COUNT(*) count FROM businesses b WHERE ({condition}) AND b.areaId IS NOT NULL GROUP BY {column},b.businessCategory ORDER BY count DESC, category", args):
            if r["category"]:
                categories.setdefault(r["groupId"], []).append({"name": r["category"], "count": r["count"]})
        areas = [dict(r) for r in db.execute("SELECT id,district,subdistrict FROM areas WHERE id LIKE '3174%' ORDER BY district,subdistrict")]
        items, seen = [], set()
        for area in areas:
            if selection.get("city") and selection["city"].casefold() != "jakarta selatan":
                continue
            if selection.get("district") and area["district"].casefold() != selection["district"].casefold():
                continue
            if selection.get("subdistrict") and area["subdistrict"].casefold() != selection["subdistrict"].casefold():
                continue
            if selection.get("area_id") and area["id"] != selection["area_id"]:
                continue
            group_id = area["id"] if level == "subdistrict" else area["district"]
            if group_id in seen:
                continue
            seen.add(group_id)
            values = aggregates.get(group_id, {"count": 0, "categories": 0, "employees": None, "employeeAvailable": 0, "shortlisted": 0})
            items.append({**area, **values, "id": group_id, "name": area["subdistrict"] if level == "subdistrict" else area["district"],
                          "dominantCategories": categories.get(group_id, [])[:3]})
        maximum = max((i["count"] for i in items), default=0)
        for item in items:
            item["index"] = round(100 * item["count"] / maximum, 1) if maximum else 0
            item["employeeCoverage"] = round(100 * item["employeeAvailable"] / item["count"], 1) if item["count"] else None
        items.sort(key=lambda r: (-r["count"], r["name"]))
        unmatched = db.execute(f"SELECT COUNT(*) FROM businesses b WHERE ({condition}) AND b.areaId IS NULL", args).fetchone()[0]
        return {"items": items, "period": period, "unmatched": unmatched,
                "method": "100 × entri wilayah / entri terbanyak dalam kelompok pembanding dan filter aktif"}


Priority = Literal["High", "Medium", "Low"]
Status = Literal["Identified", "To Be Visited", "Contacted", "Follow-up", "Qualified", "Not Interested", "Closed"]


class ShortlistCreate(BaseModel):
    businessRef: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    priority: Priority = "Medium"


class ShortlistUpdate(BaseModel):
    priority: Priority | None = None
    status: Status | None = None
    notes: str | None = Field(None, max_length=4000)


@app.get("/api/shortlist")
def shortlist(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), q: str = Query("", max_length=200)):
    with connect() as db:
        search = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        condition = "(s.businessName LIKE ? ESCAPE '\\' OR s.address LIKE ? ESCAPE '\\')"
        args = [search, search]
        rows = db.execute(f"SELECT s.*, EXISTS(SELECT 1 FROM businesses b WHERE b.id=s.businessRef) inCurrentSnapshot FROM shortlist s WHERE {condition} ORDER BY s.updatedAt DESC LIMIT ? OFFSET ?", [*args, page_size, (page-1)*page_size])
        return {"items": [dict(r) for r in rows], "total": db.execute(f"SELECT COUNT(*) FROM shortlist s WHERE {condition}", args).fetchone()[0]}


@app.post("/api/shortlist", status_code=201)
def shortlist_create(payload: ShortlistCreate):
    with connect() as db:
        existing = db.execute("SELECT * FROM shortlist WHERE businessRef=?", (payload.businessRef,)).fetchone()
        if existing:
            return dict(existing)
        row = db.execute("SELECT * FROM businesses WHERE id=?", (payload.businessRef,)).fetchone()
        if not row:
            raise HTTPException(404, "Record tidak ditemukan.")
        item = {"id": uuid.uuid4().hex, "businessRef": row["id"], "businessName": row["businessName"] or "Nama tidak tersedia",
                "address": row["address"], "areaId": row["areaId"], "district": row["district"], "subdistrict": row["subdistrict"],
                "dataPeriod": row["dataPeriod"], "segment": "Pebisnis", "priority": payload.priority,
                "status": "Identified", "notes": "", "updatedAt": now()}
        db.execute(f"INSERT OR IGNORE INTO shortlist ({','.join(item)}) VALUES ({','.join('?' for _ in item)})", tuple(item.values()))
        return dict(db.execute("SELECT * FROM shortlist WHERE businessRef=?", (payload.businessRef,)).fetchone())


@app.patch("/api/shortlist/{item_id}")
def shortlist_update(item_id: str, payload: ShortlistUpdate):
    changes = payload.model_dump(exclude_none=True)
    with connect() as db:
        if not db.execute("SELECT 1 FROM shortlist WHERE id=?", (item_id,)).fetchone():
            raise HTTPException(404, "Prospek tidak ditemukan.")
        if changes:
            changes["updatedAt"] = now()
            db.execute(f"UPDATE shortlist SET {','.join(k+'=?' for k in changes)} WHERE id=?", [*changes.values(), item_id])
        return dict(db.execute("SELECT * FROM shortlist WHERE id=?", (item_id,)).fetchone())


@app.delete("/api/shortlist/{item_id}", status_code=204)
def shortlist_delete(item_id: str):
    with connect() as db:
        if not db.execute("DELETE FROM shortlist WHERE id=?", (item_id,)).rowcount:
            raise HTTPException(404, "Prospek tidak ditemukan.")
    return Response(status_code=204)


@app.get("/api/export/businesses")
def export_businesses(selection: dict = Depends(filters), format: Literal["csv", "xlsx"] = "csv",
                      sort: str = "businessName", direction: str = "asc"):
    ordering = order_by(sort, direction)
    columns = ["businessName", "businessCategory", "kbliCode", "kbliDescription", "businessType", "district", "subdistrict", "address",
               "employeeCount", "dataPeriod", "areaId", "geocodingStatus", "source", "quality", "snapshotId", "rawIndex"]
    with connect() as db:
        condition, args, _ = where(db, selection)
        rows = [dict(r) for r in db.execute(f"SELECT b.* FROM businesses b WHERE {condition} ORDER BY {ordering}", args)]
    return export_file(rows, columns, format, "geoprospect-businesses")


@app.get("/api/export/shortlist")
def export_shortlist():
    columns = ["businessRef", "businessName", "address", "district", "subdistrict", "dataPeriod", "segment", "priority", "status", "notes", "updatedAt"]
    with connect() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM shortlist ORDER BY updatedAt DESC")]
    return export_file(rows, columns, "csv", "geoprospect-shortlist")


@app.get("/")
def index():
    return FileResponse(ROOT / "frontend" / "index.html")


from .individuals import router as individuals_router
app.include_router(individuals_router)

app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")
