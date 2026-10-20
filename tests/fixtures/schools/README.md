# School fixture provenance

Ten records captured from Data Induk Kemendikdasmen on 19 October 2026,
selected from `exports/sekolah-jabodetabek-2026-10-19/sumber` for deterministic tests.
The original list, successful detail payloads, and one failed-detail entry are retained.
Cases include SD/SMP/SMA, SPK, alphanumeric NPSN, missing/review/unavailable phone,
unmatched geography, Kota vs Kabupaten Bogor, and Kepulauan Seribu.

Detail source: `https://api.data.belajar.id/data-portal-backend/v1/master-data/satuan-pendidikan/details/{npsn}`.
`areas.json` contains six normalized polygons from the cached DPMPTSP DKI / BIG
Jabodetabek snapshot, with source URLs per record. BIG edition: October 2020,
display tolerance 0.0001 degrees. These fixtures never seed the application.
