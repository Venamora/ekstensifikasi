"""Small SQLite cache. A refresh replaces registry rows, never RM work."""
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def database_path():
    path = Path(os.getenv("GEOPROSPECT_DB", "data/geoprospect.sqlite3"))
    return path if path.is_absolute() else ROOT / path


@contextmanager
def connect():
    db = sqlite3.connect(database_path(), timeout=30)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize():
    database_path().parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS areas (
                id TEXT PRIMARY KEY, district TEXT NOT NULL, subdistrict TEXT NOT NULL,
                match_key TEXT UNIQUE NOT NULL, feature TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS businesses (
                id TEXT PRIMARY KEY, businessName TEXT, address TEXT, city TEXT,
                district TEXT, subdistrict TEXT, businessCategory TEXT, kbliCode TEXT,
                kbliDescription TEXT, businessType TEXT, employeeCount INTEGER,
                latitude REAL, longitude REAL, dataPeriod TEXT NOT NULL, source TEXT NOT NULL,
                geocodingStatus TEXT NOT NULL, locationConfidence TEXT,
                areaId TEXT, quality TEXT NOT NULL, rawIndex INTEGER NOT NULL,
                rawKbli TEXT, snapshotId TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS business_period_area ON businesses(dataPeriod, areaId);
            CREATE INDEX IF NOT EXISTS business_district ON businesses(district, subdistrict);
            CREATE INDEX IF NOT EXISTS business_category ON businesses(businessCategory, kbliCode);
            CREATE TABLE IF NOT EXISTS shortlist (
                id TEXT PRIMARY KEY, businessRef TEXT NOT NULL UNIQUE,
                businessName TEXT NOT NULL, address TEXT, areaId TEXT, district TEXT,
                subdistrict TEXT, dataPeriod TEXT NOT NULL,
                segment TEXT NOT NULL DEFAULT 'Pebisnis', priority TEXT NOT NULL,
                status TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', updatedAt TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS area_metadata (
                id TEXT PRIMARY KEY, city TEXT NOT NULL, province TEXT NOT NULL, source TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schools (
                npsn TEXT PRIMARY KEY, schoolName TEXT NOT NULL, program TEXT NOT NULL,
                level TEXT NOT NULL, educationForm TEXT NOT NULL, schoolStatus TEXT,
                city TEXT NOT NULL, province TEXT, district TEXT, subdistrict TEXT, address TEXT,
                phone TEXT, phoneStatus TEXT NOT NULL, email TEXT, website TEXT,
                latitude REAL, longitude REAL, accreditation TEXT, areaId TEXT,
                areaKey TEXT NOT NULL, detailStatus TEXT NOT NULL, source TEXT NOT NULL,
                sourceUpdatedAt TEXT, fetchedAt TEXT, snapshotId TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS school_program_area ON schools(program,areaId);
            CREATE INDEX IF NOT EXISTS school_location ON schools(city,district,subdistrict);
            CREATE TABLE IF NOT EXISTS school_shortlist (
                id TEXT PRIMARY KEY, npsn TEXT NOT NULL UNIQUE, schoolName TEXT NOT NULL,
                program TEXT NOT NULL, city TEXT, district TEXT, subdistrict TEXT,
                address TEXT, phone TEXT, priority TEXT NOT NULL, status TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '', updatedAt TEXT NOT NULL
            );
        """)


def get_meta(db, key, default=None):
    row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_meta(db, key, value):
    db.execute("INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value, ensure_ascii=False)))
