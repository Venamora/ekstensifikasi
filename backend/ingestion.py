"""Adapters for the two inspected public government endpoints; no geocoding."""
import hashlib
import json
import logging
import math
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

from .database import connect, database_path, get_meta, initialize, set_meta

DATA_URL = "https://ws.jakarta.go.id/gateway/DataPortalSatuDataJakarta/1.0/satudata?kategori=dataset&tipe=detail&url=jumlah-usaha-jasa-makanan-dan-minuman-pariwisata-yang-memiliki-perizinan"
BOUNDARY_URL = "https://gis-dpmptsp.jakarta.go.id/arcgis/rest/services/Hosted/Batas_Administrasi_Kelurahan_DKI_Jakarta/FeatureServer/85/query"
SOURCE_PAGE = "https://satudata.jakarta.go.id/open-data/detail?kategori=dataset&page_url=jumlah-usaha-jasa-makanan-dan-minuman-pariwisata-yang-memiliki-perizinan"
LOCK = threading.Lock()
LOGGER = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).isoformat()


def text(value):
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return None if value.casefold() in ("", "null", "nan", "n/a", "-", "--") else value


def key(value):
    return (text(value) or "").casefold()


def city_key(value):
    return re.sub(r"^kota\s+(?:(?:administrasi|adm\.?)\s+)?", "", key(value))


def area_key(city, district, subdistrict):
    # Government sources disagree on word spacing (Setiabudi / Setia Budi).
    return "|".join((city_key(city), key(district).replace(" ", ""), key(subdistrict).replace(" ", "")))


def parse_boundaries(payload):
    if payload.get("type") != "FeatureCollection" or payload.get("exceededTransferLimit"):
        raise ValueError("Respons batas wilayah bukan GeoJSON lengkap.")
    areas = []
    for feature in payload.get("features", []):
        prop = feature.get("properties", {})
        if city_key(prop.get("wadmkk")) != "jakarta selatan":
            continue
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in ("Polygon", "MultiPolygon") or not geometry.get("coordinates"):
            raise ValueError("Geometri kelurahan tidak valid.")
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        for polygon in polygons:
            for ring in polygon:
                if len(ring) < 4 or ring[0] != ring[-1]:
                    raise ValueError("Ring batas kelurahan tidak tertutup.")
                for point in ring:
                    if len(point) < 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in point[:2]) or not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
                        raise ValueError("Koordinat batas wilayah tidak valid untuk WGS 84.")
        area_id = text(prop.get("kdepum"))
        district, subdistrict = text(prop.get("wadmkc")), text(prop.get("wadmkd"))
        if not all((area_id, district, subdistrict)):
            raise ValueError("Identitas kelurahan tidak lengkap.")
        clean = {"type": "Feature", "geometry": geometry,
                 "properties": {"id": area_id, "district": district.title(), "subdistrict": subdistrict.title()}}
        areas.append({"id": area_id, "district": district.title(), "subdistrict": subdistrict.title(),
                      "match_key": area_key(prop["wadmkk"], district, subdistrict),
                      "feature": json.dumps(clean, ensure_ascii=False)})
    # Prevent publishing an accidentally partial geographic snapshot.
    if len(areas) != 65 or len({a["id"] for a in areas}) != 65:
        raise ValueError("Batas Jakarta Selatan tidak berisi 65 kelurahan unik; perlu peninjauan schema.")
    return areas


def normalize(payload, areas, snapshot_id):
    raw = payload.get("data")
    if payload.get("message") != "success" or not isinstance(raw, list) or not raw:
        raise ValueError("API tidak mengembalikan daftar data yang berhasil dan tidak kosong.")
    if int(payload.get("total_file", -1)) != len(raw):
        raise ValueError("Jumlah record berbeda dari total_file; snapshot tidak lengkap.")
    required = {"nama_usaha_atau_kegiatan", "alamat_usaha", "kabupaten_atau_kota", "kecamatan", "kelurahan", "periode_data"}
    if any(not isinstance(r, dict) or not required.issubset(r) for r in raw):
        raise ValueError("Schema registry berubah atau ada record yang tidak valid.")
    frame = pd.DataFrame(raw)
    selected = frame[frame["kabupaten_atau_kota"].map(city_key).eq("jakarta selatan")]
    lookup = {a["match_key"]: a for a in areas}
    records, seen = [], set()
    for raw_index in selected.index:
        r = raw[int(raw_index)]
        canonical = json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        record_id = hashlib.sha256(canonical.encode()).hexdigest()
        if record_id in seen:
            continue
        seen.add(record_id)
        period = text(r.get("periode_data"))
        if not period or not re.fullmatch(r"\d{4}", period):
            raise ValueError("Periode data tidak valid; snapshot sebelumnya dipertahankan.")
        area = lookup.get(area_key(r["kabupaten_atau_kota"], r["kecamatan"], r["kelurahan"]))
        kbli = text(r.get("kbli"))
        valid_kbli = bool(kbli and re.fullmatch(r"\d{5}", kbli))
        workers = text(r.get("jumlah_tki"))
        # Source uses integer worker counts; reject ambiguous numeric formats.
        employee_count = int(workers) if workers and re.fullmatch(r"\d{1,9}", workers) else None
        warnings = []
        if not area:
            warnings.append("Wilayah tidak cocok dengan batas administrasi")
        if not valid_kbli:
            warnings.append("KBLI tidak tersedia atau tidak valid")
        if workers is not None and employee_count is None:
            warnings.append("Jumlah TKI tidak valid")
        if not text(r["alamat_usaha"]):
            warnings.append("Alamat tidak tersedia")
        if not text(r["nama_usaha_atau_kegiatan"]):
            warnings.append("Nama usaha tidak tersedia")
        category = text(r.get("uraian_kbli"))
        business_type = text(r.get("jenis_usaha"))
        records.append({"id": record_id, "businessName": text(r["nama_usaha_atau_kegiatan"]),
            "address": text(r["alamat_usaha"]), "city": "Jakarta Selatan",
            "district": area["district"] if area else (text(r["kecamatan"]) or "").title() or None,
            "subdistrict": area["subdistrict"] if area else (text(r["kelurahan"]) or "").title() or None,
            "businessCategory": category.upper() if category else None, "kbliCode": kbli if valid_kbli else None,
            "kbliDescription": category, "businessType": business_type.upper() if business_type else None,
            "employeeCount": employee_count, "latitude": None, "longitude": None, "dataPeriod": period,
            "source": SOURCE_PAGE, "geocodingStatus": "unresolved", "locationConfidence": None,
            "areaId": area["id"] if area else None, "quality": json.dumps(warnings, ensure_ascii=False),
            "rawIndex": int(raw_index), "rawKbli": kbli, "snapshotId": snapshot_id})
    if not records:
        raise ValueError("Tidak ditemukan record Jakarta Selatan; perlu pemeriksaan sumber.")
    return records, {"upstreamRows": len(raw), "southRawRows": len(selected),
                     "storedRows": len(records), "exactDuplicatesRemoved": len(selected) - len(records),
                     "periods": sorted({r["dataPeriod"] for r in records}, reverse=True)}


def download_json(url, params=None):
    # A full registry is ~46 MB. Bound memory use and timeout, never accept a partial body.
    with httpx.Client(timeout=httpx.Timeout(120, connect=15), follow_redirects=True,
                      headers={"User-Agent": "MandiriGeoProspect/1.0 (public registry research)"}) as client:
        with client.stream("GET", url, params=params) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 150_000_000:
                    raise ValueError("Ukuran respons melebihi batas 150 MB.")
                chunks.append(chunk)
            body = b"".join(chunks)
            return json.loads(body.decode("utf-8-sig")), body, {
                "lastModified": response.headers.get("last-modified"), "etag": response.headers.get("etag")}


def reserve_refresh():
    if not LOCK.acquire(blocking=False):
        return "Refresh sedang berlangsung."
    try:
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            last = get_meta(db, "lastAttempt")
            cooldown = max(30, int(os.getenv("GEOPROSPECT_REFRESH_SECONDS", "300")))
            too_soon = last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < cooldown
            if not too_soon:
                set_meta(db, "lastAttempt", now())
                set_meta(db, "refreshState", "running")
        if too_soon:
            LOCK.release()
            return f"Tunggu {cooldown} detik antar-refresh."
    except Exception:
        LOCK.release()
        raise
    return None


def refresh_reserved():
    try:
        with connect() as db:
            areas = [dict(r) for r in db.execute("SELECT * FROM areas")]
        boundary_error, boundary_meta = None, None
        try:
            geo, _, geo_headers = download_json(os.getenv("GEOPROSPECT_BOUNDARY_URL", BOUNDARY_URL), {
                "where": "1=1", "outFields": "wadmkk,wadmkc,wadmkd,kdepum,kdcpum",
                "returnGeometry": "true", "outSR": "4326", "f": "geojson"})
            areas = parse_boundaries(geo)
            boundary_meta = {"fetchedAt": now(), "source": os.getenv("GEOPROSPECT_BOUNDARY_URL", BOUNDARY_URL), **geo_headers}
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            boundary_error = f"Batas wilayah gagal diperbarui ({type(exc).__name__}); gunakan cache/dropdown."
        payload, body, headers = download_json(os.getenv("GEOPROSPECT_DATA_URL", DATA_URL))
        snapshot_id = hashlib.sha256(body).hexdigest()
        records, summary = normalize(payload, areas, snapshot_id)
        snapshots = database_path().parent / "snapshots"
        snapshots.mkdir(exist_ok=True)
        raw_path = snapshots / f"{snapshot_id}.json"
        if not raw_path.exists():
            tmp = raw_path.with_suffix(".tmp")
            tmp.write_bytes(body)
            tmp.replace(raw_path)
        with connect() as db:
            db.execute("DELETE FROM businesses")
            columns = list(records[0])
            db.executemany(f"INSERT INTO businesses ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                           [tuple(r[c] for c in columns) for r in records])
            from .geography import save_areas
            save_areas(db, areas)
            set_meta(db, "snapshot", {**summary, "id": snapshot_id, "fetchedAt": now(),
                     "source": SOURCE_PAGE, "bytes": len(body), **headers})
            if boundary_meta:
                set_meta(db, "boundaries", boundary_meta)
            set_meta(db, "boundaryError", boundary_error)
            set_meta(db, "lastError", None)
            set_meta(db, "refreshState", "idle")
    except Exception as exc:
        LOGGER.exception("Registry refresh failed")
        with connect() as db:
            set_meta(db, "lastError", f"Sinkronisasi gagal ({type(exc).__name__}). Snapshot terakhir dipertahankan; periksa log server.")
            set_meta(db, "refreshState", "error")
    finally:
        LOCK.release()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    initialize()
    error = reserve_refresh()
    if error:
        raise SystemExit(error)
    refresh_reserved()
    with connect() as db:
        print(json.dumps({"snapshot": get_meta(db, "snapshot"), "error": get_meta(db, "lastError"),
                          "boundaryError": get_meta(db, "boundaryError")}, ensure_ascii=False, indent=2))
        if get_meta(db, "lastError"):
            raise SystemExit(1)
