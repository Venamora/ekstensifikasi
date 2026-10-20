# Mandiri GeoProspect

Location-Based Acquisition Intelligence for Pebisnis and Individu. Uses the real Satu Data Jakarta food and beverage registry and a static Kemendikdasmen school snapshot, with a shared kelurahan map that filters the active directory.

**Independent prototype. Not operated or endorsed by Bank Mandiri.** Registry entries do not confirm current operation, New CIF eligibility, or an existing banking relationship.

![Dashboard with real registry data](docs/dashboard-desktop.png)

## Run locally

Python 3.12 or newer is recommended; verified here with Python 3.14.6. No Node build, frontend framework, or Google API key is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set `GEOPROSPECT_ADMIN_TOKEN` in `.env` to the generated secret. Do not overwrite an existing `.env`. The completed workspace already has a generated local token and an ingested cache; these are ignored by Git. To reproduce the exact tested dependency set, use `pip install -r requirements.lock` instead.

```bash
python -m backend.ingestion
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. Use a single Uvicorn worker. Run commands from the project root. On Windows, activate with `.venv\Scripts\activate`.

The first ingestion can take around a minute because the upstream returns approximately 46 MB in one response. The UI also runs before ingestion, showing an explicit empty state. A failed request never creates demo records. Later loads use SQLite and do not fetch the government dataset again.

### Refresh

Click **Refresh data**, enter the token from your local `.env`, and wait for completion. The token is sent in a request header, held only for that operation, and never saved in browser storage. Alternatively run `python -m backend.ingestion` from the terminal. Refresh attempts have a 300-second cooldown by default; the server retains the last successful snapshot on failure.

Configuration is listed in [.env.example](.env.example). The source URLs and SQLite path can be overridden through environment variables. Credentials belong only in server configuration. Restart the server after changing `.env`.

## Using the dashboard

- The initial view shows Jakarta Selatan and the newest **available data period**, currently 2026. It does not claim that the period contains every active business.
- Click a kelurahan polygon, or choose kecamatan/kelurahan from the filters. The directory, KPIs, area popup, category chart, and analysis follow the same selection. Panning and zooming alone do not filter records.
- **Lihat semua** clears geography while preserving business filters. **Reset** clears all filters and returns to the newest period.
- Switch between map, table, and split view. Use the column menu, sorting, pagination, and advanced filters. Multiple category/KBLI/type choices use Ctrl/Cmd on desktop or the native selection control on mobile.
- Click a business name for its actual registry fields and provenance. **Cari di Google Maps** searches its name and registered address. It does not assert that the Google result is the registered location.
- Add a business to the shortlist, then save its priority, status, and notes in **Shortlist Prospek**. Initial status is `Identified`. The shortlist is shared by this local installation and persists independently of registry refreshes. It is not affected by directory filters.
- CSV and Excel directory exports include **all rows matching the active filters**, not only the visible page. Shortlist export includes all saved prospects. Spreadsheet formula prefixes are escaped.
- **Sumber & Kualitas** explains timestamps, matching, missing fields, and provenance. Business names, addresses, and notes are rendered with DOM text nodes.

## Architecture

### Individu: GMM and Tabungan SimPel

Choose **Individu → GMM** (SMA, including SPK SMA) or **Tabungan SimPel** (SD/SMP, including SPK).
On mobile, use the segment selector above the dashboard. Schools cover Jabodetabek, including
Kepulauan Seribu. The active program controls the directory, filters, KPIs, map shading,
opportunities, shortlist, and exports. A selected kelurahan stays selected when switching programs.
Map popups show all three baseline counts: Pebisnis for the selected business period, GMM, and SimPel;
the active filtered count is shown separately. Grey business polygons outside Jakarta Selatan mean
**no business data coverage**, not zero businesses.

School data is imported explicitly from the downloaded local bundle; opening a page does not call
the school source. No refresh button is provided for schools. Import/update commands:

```bash
# Explicit network fetch; validated DPMPTSP DKI + BIG boundaries, cached locally.
python -m backend.school_ingestion --boundaries
# Local files only. Use the sumber directory containing these three files:
# daftar-terpilih.json, detail-sekolah.jsonl, gagal-detail.json
python -m backend.school_ingestion --snapshot-dir exports/sekolah-jabodetabek-2026-10-19/sumber
```

Back up the SQLite database before import. School replacement is atomic and preserves both shortlists.
The existing business importer updates its 65 kelurahan without removing the shared Jabodetabek map.
School identities use NPSN as text, including alphanumeric source identifiers. Phone numbers retain
leading zeroes. `available` means a plausible number is present, **not verified active**.
Nine missing source details retain their base school rows and an explicit unavailable status.

The 19 October 2026 import contains **13,963 schools**: **1,560 GMM** and **12,403 SimPel**.
**13,548** match the **1,508** cached kelurahan; **415** remain unmatched and searchable/exportable.
All **793 Jakarta Selatan** schools match. Matching uses the city, district and kelurahan hierarchy;
Kota and Kabupaten stay distinct. Only formatting differences (case/space and scoped administrative
prefixes for Bogor/Seribu) are normalized. Matched school display names follow the polygon names;
original source names remain in the downloaded bundle. No coordinate or fuzzy-name guessing is used.
The BIG boundaries outside DKI are the **October 2020 edition**, simplified to 0.0001 degrees for display.
Unmatched rows and boundary age are visible in the quality view; an official newer crosswalk can improve coverage.

Schools represent prospecting institutions, not student records or confirmed New CIF counts.
School shortlist storage is separate from business prospects and filtered by program. Saved records
survive snapshot replacement, with a stale flag when their NPSN is no longer present.

### Components

```text
Government registry ─┐
                    ├─ FastAPI ingestion → raw snapshot + SQLite → filtered APIs
Government polygons ┘                                        ↓
                                               Vanilla JS + Leaflet
                                                        ↓
                                                 Local RM shortlist
```

- `backend/ingestion.py`: inspected source adapters, validation, Pandas selection, normalization, exact duplicate removal, and atomic snapshot replacement.
- `backend/school_ingestion.py`, `backend/geography.py`: validated static school import and shared administrative identities; `backend/individuals.py`: school APIs and shortlist; `backend/exports.py`: spreadsheet-safe exports shared by both segments.
- `backend/database.py`: indexed SQLite storage; `backend/queries.py`: shared query filters; `backend/main.py`: APIs, export, security headers, and same-origin static serving.
- `frontend/`: responsive HTML/CSS and ES module application. Leaflet 1.9.4 is vendored with its license; JS/CSS hashes were checked against the official distribution. OpenStreetMap tiles load directly as needed with visible attribution and normal browser caching.
- `data/`: local database plus immutable original JSON snapshots identified by SHA-256. A normalized record's `rawIndex` is its zero-based index in the corresponding snapshot's `data` array. Raw files are not web-served.
- `tests/fixtures/`: seven real registry records and the actual 65 boundary geometries, for deterministic tests. They never seed the application.

SQLite registry and area updates commit in one transaction. A schema change, partial response, or invalid period rejects the new registry. If only the boundary service fails, ingestion can use cached polygons; without polygons the directory still works and records are unmatched. Shortlists retain their saved fields even when an upstream record changes or disappears.

## API

See [API reference](docs/API.md) and the machine-readable schema at `/openapi.json`. Interactive Swagger/ReDoc pages are disabled to keep frontend scripts local and the Content Security Policy strict.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Application/database availability |
| `GET /api/data/status` | Snapshot, periods, last attempt, errors, boundary cache |
| `POST /api/data/refresh` | Protected refresh, `X-Admin-Token` required |
| `GET /api/filters` | Available periods and filter choices |
| `GET /api/businesses` | Filtered, sorted, paginated directory |
| `GET /api/businesses/{id}` | Record and provenance |
| `GET /api/areas/geojson` | Cached real kelurahan polygons |
| `GET /api/areas` | Area counts under active filters |
| `GET /api/statistics` | Reconciled KPIs and category counts |
| `GET /api/opportunities` | District or subdistrict concentration index |
| `GET/POST /api/shortlist` | List or save local prospects |
| `PATCH/DELETE /api/shortlist/{id}` | Update or remove a prospect |
| `GET /api/export/businesses` | CSV or Excel of filtered records |
| `GET /api/export/shortlist` | CSV of the shortlist |

## Verified source and results

The government registry and polygon APIs were successfully fetched on **14 October 2026**. The complete registry response contained 96,701 rows, including 27,607 marked Jakarta Selatan; none were exact duplicate rows. The schema, field mapping, and evidence are in [API exploration](docs/API_EXPLORATION.md).

| Period | South Jakarta entries | Matched to polygons | Reported TKI | Entries with TKI |
| --- | ---: | ---: | ---: | ---: |
| 2026 | 2,550 | 2,550 | 8,313 | 2,550 |
| 2024 | 9,916 | 9,916 | 6,676 | 3,351 |
| 2023 | 15,141 | 14,646 | 57,520 | 15,141 |

These are observations from that snapshot, **not hardcoded dashboard metrics**. Entries across years may describe the same establishment. The 495 unmatched 2023 rows remain in the directory but are excluded from polygon counts and area rankings. Missing workforce values remain null.

## Tests

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
node --check frontend/js/app.js   # optional syntax check if Node is available
```

The tests create temporary SQLite databases, use real captured records, and mock network failure/success without calling government services. They cover source validation, naming differences, missing fields, duplicate handling, exact filter/KPI/map/export reconciliation, ordering and pagination validation, shortlist persistence, failed refresh recovery, rate limits, request origin protection, and spreadsheet safety. See [validation results](docs/VALIDATION.md) for live API and browser checks.

## Limits and deployment

- This MVP maps **administrative areas**. There are no business coordinates, geocoding, location pins, radius searches, GPS collection, or direct coordinate-based Street View. External Google Maps search lets an RM manually inspect a possible location and available imagery.
- The index is `100 × area record count / largest compared area record count` under the active filters. Workforce is shown separately; it does not influence the score. A single selected area can therefore score 100. There is no conversion prediction, revenue estimate, credit decision, or EDA/CIF integration.
- Registry data can contain missing names, invalid KBLI codes, outdated addresses, and inconsistent administrative fields. Categories and business types are normalized for case; different source classifications are not silently merged. There is no inferred owner-to-business linkage.
- IDs hash exact raw record content. A changed source row gets a new ID; an existing shortlist entry is retained and marked for review. Cross-period deduplication is intentionally not performed without a reliable establishment identifier.
- Business boundary validation expects 65 Jakarta Selatan kelurahan. The shared map additionally validates all 14 Jabodetabek city/regency codes. Administrative changes require inspecting and updating the adapter. Boundaries are for exploration, not a legal boundary determination.
- Shortlist notes are local shared data with **no account isolation**. Keep the default loopback binding. Before public deployment, put the whole application behind authenticated HTTPS access, configure `GEOPROSPECT_ALLOWED_HOSTS`, protect all shortlist access, and retain refresh-token protection. The token alone does not secure a public deployment. No private customer identification data should be entered.
- Network access is needed for refreshes, OpenStreetMap tiles, and Google Maps. Cached data and polygons remain usable without upstream APIs; failed base tiles do not disable polygon selection. Follow the [OpenStreetMap tile usage policy](https://operations.osmfoundation.org/policies/tiles/) and use an appropriate provider if public traffic grows.
- Snapshots are retained for traceability. Back up the SQLite database and snapshot directory; plan explicit archival/retention before sustained use. Do not run simultaneous CLI refresh processes or multiple Uvicorn workers.
- Future internal EDA integration should use a separate authenticated aggregate-data adapter and tables, leaving public registry ingestion independent. Authentication, audited RM ownership, verified source identifiers, and an official administrative code crosswalk are useful next additions when needed.
