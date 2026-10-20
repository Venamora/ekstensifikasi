"""Tests use a small, attributed subset of the actual registry, never seed demo data."""
import csv
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from backend.database import connect, get_meta, initialize, set_meta
from backend.ingestion import area_key, city_key, normalize, parse_boundaries, refresh_reserved, reserve_refresh
from backend.main import app, safe_cell

FIXTURES = Path(__file__).parent / "fixtures"
REGISTRY = json.loads((FIXTURES / "registry.json").read_text())
BOUNDARIES = json.loads((FIXTURES / "boundaries.json").read_text())


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"GEOPROSPECT_DB": str(Path(self.temp.name) / "test.sqlite3"),
                                          "GEOPROSPECT_ADMIN_TOKEN": "test-token"})
        self.env.start()
        initialize()
        self.areas = parse_boundaries(BOUNDARIES)
        self.records, summary = normalize(REGISTRY, self.areas, "fixture-source-snapshot")
        with connect() as db:
            db.executemany("INSERT INTO areas VALUES (:id,:district,:subdistrict,:match_key,:feature)", self.areas)
            cols = list(self.records[0])
            db.executemany(f"INSERT INTO businesses ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                           [tuple(r[c] for c in cols) for r in self.records])
            set_meta(db, "snapshot", {**summary, "id": "fixture-source-snapshot", "fetchedAt": "2026-10-14T00:00:00+00:00"})
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def test_normalization_preserves_unknowns_and_area_conflicts(self):
        self.assertTrue(all(r["latitude"] is None and r["longitude"] is None for r in self.records))
        self.assertTrue(any(r["employeeCount"] is None for r in self.records))
        self.assertTrue(any(r["areaId"] is None for r in self.records))
        self.assertTrue(any(r["kbliCode"] is None for r in self.records))
        self.assertEqual(area_key("Kota Adm. Jakarta Selatan", "Setiabudi", "Karet"),
                         area_key("Jakarta Selatan", "SETIA BUDI", "KARET"))
        self.assertEqual(city_key("Kota Administrasi Jakarta Selatan"), "jakarta selatan")
        for record in self.records:
            raw = REGISTRY["data"][record["rawIndex"]]
            if raw["kecamatan"] == "Setiabudi":
                self.assertIsNotNone(record["areaId"])

    def test_exact_duplicates_only_and_stable_identity(self):
        payload = {**REGISTRY, "data": [*REGISTRY["data"], REGISTRY["data"][0]], "total_file": REGISTRY["total_file"] + 1}
        result, summary = normalize(payload, self.areas, "next-snapshot")
        self.assertEqual(len(result), len(self.records))
        self.assertEqual(summary["exactDuplicatesRemoved"], 1)
        self.assertEqual(result[0]["id"], self.records[0]["id"])

    def test_schema_and_partial_snapshot_rejected(self):
        with self.assertRaises(ValueError):
            normalize({**REGISTRY, "total_file": 999}, self.areas, "bad")
        with self.assertRaises(ValueError):
            normalize({"data": [{}], "total_file": 1, "message": "success"}, self.areas, "bad")
        with self.assertRaises(ValueError):
            parse_boundaries({**BOUNDARIES, "features": BOUNDARIES["features"][:1]})

    def test_filters_statistics_map_and_exports_reconcile(self):
        for query in ["period=all", "period=2026", "period=2024&employee_data=missing", "period=all&area_quality=unmatched",
                      "period=all&min_employees=1&max_employees=10", "period=all&district=Setia%20Budi"]:
            with self.subTest(query=query):
                directory = self.client.get("/api/businesses?" + query).json()
                stats = self.client.get("/api/statistics?" + query).json()
                areas = self.client.get("/api/areas?" + query).json()
                export = self.client.get("/api/export/businesses?" + query)
                exported = list(csv.DictReader(io.StringIO(export.content.decode("utf-8-sig"))))
                self.assertEqual(directory["total"], stats["total"])
                self.assertEqual(len(exported), stats["total"])
                self.assertEqual(sum(a["count"] for a in areas["items"]), stats["matched"])
                self.assertEqual(stats["matched"] + stats["unmatched"], stats["total"])
                values = [r["employeeCount"] for r in directory["items"] if r["employeeCount"] is not None]
                self.assertEqual(stats["reportedEmployees"], sum(values) if values else None)

    def test_area_selection_and_index(self):
        matched = next(r for r in self.records if r["areaId"])
        query = f"period=all&area_id={matched['areaId']}"
        data = self.client.get("/api/businesses?" + query).json()
        self.assertTrue(data["items"])
        self.assertTrue(all(r["areaId"] == matched["areaId"] for r in data["items"]))
        areas = self.client.get("/api/opportunities?" + query).json()["items"]
        self.assertEqual(len(areas), 1)
        self.assertEqual(areas[0]["index"], 100)
        self.assertEqual(len(self.client.get("/api/areas/geojson").json()["features"]), 65)

    def test_pagination_and_input_validation(self):
        first = self.client.get("/api/businesses?period=all&page_size=1&page=1").json()
        second = self.client.get("/api/businesses?period=all&page_size=1&page=2").json()
        self.assertEqual(len(first["items"]), 1)
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        for query in ["sort=id;DROP%20TABLE%20businesses", "page_size=101", "page=0", "min_employees=5&max_employees=1", "direction=sideways", "period=latest"]:
            self.assertEqual(self.client.get("/api/businesses?" + query).status_code, 422)
        self.assertEqual(self.client.get("/api/businesses?q=%25").json()["total"], 0)
        self.assertEqual(self.client.get("/api/businesses?period=all&q=%27%20OR%201=1--").json()["total"], 0)

    def test_shortlist_lifecycle_and_refresh_survival(self):
        record = self.records[0]
        result = self.client.post("/api/shortlist", json={"businessRef": record["id"]})
        self.assertEqual(result.status_code, 201)
        item = result.json()
        self.assertEqual(item["status"], "Identified")
        self.assertEqual(self.client.post("/api/shortlist", json={"businessRef": record["id"]}).json()["id"], item["id"])
        self.assertEqual(self.client.patch(f"/api/shortlist/{item['id']}", json={"status": "Invented"}).status_code, 422)
        changed = self.client.patch(f"/api/shortlist/{item['id']}", json={"status": "To Be Visited", "priority": "High", "notes": "=1+1"}).json()
        self.assertEqual(changed["priority"], "High")
        self.assertIn("'=1+1", self.client.get("/api/export/shortlist").text)
        self.assertEqual(self.client.get("/api/statistics?period=all").json()["shortlisted"], 1)
        with connect() as db:
            db.execute("DELETE FROM businesses WHERE id=?", (record["id"],))
        listed = self.client.get("/api/shortlist").json()
        self.assertEqual(listed["total"], 1)
        self.assertFalse(listed["items"][0]["inCurrentSnapshot"])
        self.assertEqual(listed["items"][0]["notes"], "=1+1")
        self.assertEqual(self.client.delete(f"/api/shortlist/{item['id']}").status_code, 204)
        self.assertEqual(self.client.get("/api/shortlist").json()["total"], 0)

    def test_refresh_failure_retains_cache_and_rate_limit(self):
        self.assertIsNone(reserve_refresh())
        with patch("backend.ingestion.download_json", side_effect=httpx.ConnectError("Unavailable")), self.assertLogs("backend.ingestion", level="ERROR"):
            refresh_reserved()
        status = self.client.get("/api/data/status").json()
        self.assertEqual(status["snapshot"]["id"], "fixture-source-snapshot")
        self.assertEqual(self.client.get("/api/businesses?period=all").json()["total"], len(self.records))
        self.assertIsNotNone(status["lastError"])
        self.assertIsNotNone(reserve_refresh())

    def test_successful_refresh_is_atomic_and_retains_shortlist(self):
        self.client.post("/api/shortlist", json={"businessRef": self.records[0]["id"]})
        self.assertIsNone(reserve_refresh())
        def downloaded(url, params=None):
            payload = BOUNDARIES if params else REGISTRY
            return payload, json.dumps(payload).encode(), {"etag": None, "lastModified": None}
        with patch("backend.ingestion.download_json", side_effect=downloaded):
            refresh_reserved()
        status = self.client.get("/api/data/status").json()
        self.assertIsNone(status["lastError"])
        self.assertNotEqual(status["snapshot"]["id"], "fixture-source-snapshot")
        self.assertTrue(self.client.get("/api/shortlist").json()["items"][0]["inCurrentSnapshot"])

    def test_security_and_excel(self):
        self.assertEqual(self.client.post("/api/data/refresh").status_code, 401)
        self.assertEqual(self.client.post("/api/shortlist", headers={"Origin": "https://other.example"}, json={"businessRef": self.records[0]["id"]}).status_code, 403)
        self.assertEqual(self.client.get("/api/health", headers={"Host": "evil.example"}).status_code, 400)
        self.assertIn("frame-ancestors 'none'", self.client.get("/").headers["content-security-policy"])
        for value in ["=1+1", " +1", "@SUM(A1)", "\tdata"]:
            self.assertTrue(safe_cell(value).startswith("'"))
        response = self.client.get("/api/export/businesses?period=all&format=xlsx")
        workbook = load_workbook(io.BytesIO(response.content), read_only=True)
        self.assertEqual(len(list(workbook.active.values)), len(self.records) + 1)
        workbook.close()

    def test_empty_cache_is_transparent(self):
        with connect() as db:
            db.execute("DELETE FROM businesses")
            db.execute("DELETE FROM metadata")
        self.assertIsNone(self.client.get("/api/data/status").json()["snapshot"])
        stats = self.client.get("/api/statistics").json()
        self.assertEqual(stats["total"], 0)
        self.assertIsNone(stats["reportedEmployees"])


if __name__ == "__main__":
    unittest.main()
