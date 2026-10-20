"""Import a verified local school bundle; fetch boundaries only via an explicit CLI option."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from .database import connect, database_path, initialize, set_meta
from .geography import CITIES, city_name, locality, save_areas, school_area_key
from .ingestion import BOUNDARY_URL, download_json, now, text

BIG_LAYER = 'https://geoservices.big.go.id/gis/rest/services/BAPANAS/Batas_Administrasi/MapServer/2'
FORMS = {'SD': 'simpel', 'SMP': 'simpel', 'SMA': 'gmm',
         'SPK SD': 'simpel', 'SPK SMP': 'simpel', 'SPK SMA': 'gmm'}


def phone_status(phone, detail=True):
    if not detail:
        return 'unavailable'
    digits = re.sub(r'\D', '', phone or '')
    if not digits or set(digits) <= {'0'}:
        return 'missing'
    return 'available' if 7 <= len(digits) <= 15 else 'review'


def normalize_schools(bundle, areas):
    bundle = Path(bundle)
    selected_bytes = (bundle / 'daftar-terpilih.json').read_bytes()
    detail_bytes = (bundle / 'detail-sekolah.jsonl').read_bytes()
    error_bytes = (bundle / 'gagal-detail.json').read_bytes()
    snapshot = hashlib.sha256(selected_bytes + detail_bytes + error_bytes).hexdigest()
    selected = json.loads(selected_bytes)
    errors = json.loads(error_bytes)
    details = {}
    for line in detail_bytes.decode().splitlines():
        entry = json.loads(line)
        if entry['npsn'] in details or entry['payload']['satuanPendidikan']['npsn'] != entry['npsn']:
            raise ValueError('Detail duplikat atau NPSN tidak cocok')
        details[entry['npsn']] = entry
    ids = {r['NPSN'] for r in selected}
    if not selected or len(ids) != len(selected) or ids != details.keys() | errors.keys() or details.keys() & errors.keys():
        raise ValueError('Snapshot tidak lengkap atau mengandung NPSN duplikat')
    lookup = {a['match_key']: a for a in areas}
    rows = []
    for item in selected:
        npsn = item['NPSN']
        if not re.fullmatch(r'[A-Z0-9]{8}', npsn) or item['Bentuk'] not in FORMS or not text(item['Nama']):
            raise ValueError('Identitas atau jenjang sekolah tidak valid')
        detail = details.get(npsn)
        s = detail['payload']['satuanPendidikan'] if detail else {}
        city = city_name(item['Kabupaten'])
        district, subdistrict = locality(item['Kecamatan']), locality(item['Kelurahan'])
        area_key = school_area_key(city, district, subdistrict)
        area = lookup.get(area_key)
        if area:
            district, subdistrict = area['district'], area['subdistrict']
        phone = s.get('nomorTelepon')
        if phone is not None and not isinstance(phone, str):
            raise ValueError('Telepon harus berupa teks')
        lat, lon = s.get('lintang'), s.get('bujur')
        if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and
                math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon)):
            lat = lon = None
        province = 'DKI Jakarta' if city.startswith('Jakarta') or city == 'Kepulauan Seribu' else 'Banten' if 'Tangerang' in city else 'Jawa Barat'
        rows.append(dict(npsn=npsn, schoolName=text(s.get('nama')) or item['Nama'], program=FORMS[item['Bentuk']],
            level=item['Bentuk'].removeprefix('SPK '), educationForm=item['Bentuk'], schoolStatus=item['Status'],
            city=city, province=province, district=district, subdistrict=subdistrict,
            address=text(s.get('alamatJalan')) or text(item['Alamat']), phone=phone,
            phoneStatus=phone_status(phone, bool(detail)), email=text(s.get('email')), website=text(s.get('website')),
            latitude=lat, longitude=lon, accreditation=text(s.get('akreditasi')), areaId=area['id'] if area else None,
            areaKey=area_key, detailStatus='available' if detail else 'unavailable',
            source=f'https://api.data.belajar.id/data-portal-backend/v1/master-data/satuan-pendidikan/details/{npsn}',
            sourceUpdatedAt=detail['payload'].get('meta', {}).get('lastUpdatedAt') if detail else None,
            fetchedAt=detail.get('retrievedAt') if detail else None, snapshotId=snapshot))
    return rows, dict(id=snapshot, importedAt=now(), storedRows=len(rows), detailUnavailable=len(errors),
                     fetchedAt=max((r['fetchedAt'] for r in rows if r['fetchedAt']), default=None),
                     sourceUpdatedAt=sorted({r['sourceUpdatedAt'] for r in rows if r['sourceUpdatedAt']}),
                     source='https://data.kemendikdasmen.go.id/data-induk/satpen/daftar')


def import_schools(bundle):
    initialize()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        rows, meta = normalize_schools(bundle, [dict(r) for r in db.execute('SELECT * FROM areas')])
        db.execute('DELETE FROM schools')
        cols = list(rows[0])
        db.executemany(f"INSERT INTO schools ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                       [tuple(r[c] for c in cols) for r in rows])
        set_meta(db, 'schoolSnapshot', meta)
        return {**meta, 'matched': sum(r['areaId'] is not None for r in rows)}


def normalize_boundaries(features, source, dki=False):
    areas = {}
    for feature in features:
        p = {k.lower(): v for k, v in feature['properties'].items()}
        code = re.sub(r'\D', '', str(p.get('kdepum') or ''))
        if len(code) != 10 or code[:4] not in CITIES:
            raise ValueError(f'Code kelurahan tidak valid: {code}')
        city = CITIES[code[:4]]
        if dki != code.startswith('31'):
            raise ValueError('Batas di luar cakupan sumber')
        district, subdistrict = locality(p.get('wadmkc')), locality(p.get('wadmkd'))
        if not district or not subdistrict:
            raise ValueError('Nama wilayah tidak lengkap')
        geometry = feature.get('geometry') or {}
        if geometry.get('type') not in ('Polygon', 'MultiPolygon') or not geometry.get('coordinates'):
            raise ValueError('Geometri bukan polygon')
        polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
        for poly in polygons:
            for ring in poly:
                if len(ring) < 4 or ring[0][:2] != ring[-1][:2]:
                    raise ValueError('Ring polygon tidak tertutup')
                for point in ring:
                    if len(point) < 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in point[:2]) or not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
                        raise ValueError('Koordinat polygon tidak valid')
        if code in areas:
            old = json.loads(areas[code]['feature'])
            if areas[code]['match_key'] != school_area_key(city, district, subdistrict):
                raise ValueError('Kode kelurahan memiliki nama berbeda')
            old_polys = [old['geometry']['coordinates']] if old['geometry']['type'] == 'Polygon' else old['geometry']['coordinates']
            geometry = {'type': 'MultiPolygon', 'coordinates': old_polys + polygons}
        province = 'DKI Jakarta' if dki else 'Banten' if code.startswith('36') else 'Jawa Barat'
        clean = {'type': 'Feature', 'geometry': geometry, 'properties': dict(id=code, city=city, province=province, district=district, subdistrict=subdistrict)}
        areas[code] = dict(id=code, district=district, subdistrict=subdistrict, city=city, province=province,
                           match_key=school_area_key(city, district, subdistrict), source=source,
                           feature=json.dumps(clean, ensure_ascii=False))
    return list(areas.values())


def fetch_boundaries():
    all_rows = []
    for url, dki in [(BOUNDARY_URL, True), (BIG_LAYER + '/query', False)]:
        codes = [c[:2] + '.' + c[2:] for c in CITIES if not c.startswith('31')]
        where = '1=1' if dki else 'KDPKAB IN (' + ','.join("'" + c + "'" for c in codes) + ')'
        ids, _, _ = download_json(url, {'where': where, 'returnIdsOnly': 'true', 'f': 'json'})
        object_ids = ids.get('objectIds')
        if not object_ids or len(set(object_ids)) != len(object_ids):
            raise ValueError('Daftar object ID batas wilayah tidak lengkap')
        features = []
        for offset in range(0, len(object_ids), 100):
            batch = object_ids[offset:offset + 100]
            params = {'objectIds': ','.join(map(str, batch)), 'outFields': '*', 'returnGeometry': 'true',
                      'outSR': '4326', 'returnZ': 'false', 'returnM': 'false', 'f': 'geojson'}
            if not dki:
                params.update(maxAllowableOffset='0.0001', geometryPrecision=6)
            result, _, _ = download_json(url, params)
            if result.get('exceededTransferLimit') or len(result.get('features', [])) != len(batch):
                raise ValueError('Snapshot batas wilayah parsial')
            features.extend(result['features'])
            print(f'Boundaries {"DKI" if dki else "BIG"}: {len(features)}/{len(object_ids)}', flush=True)
        all_rows.extend(normalize_boundaries(features, url, dki))
    if {a['id'][:4] for a in all_rows} != set(CITIES) or sum(a['id'].startswith('3174') for a in all_rows) != 65:
        raise ValueError('Cakupan Jabodetabek atau 65 kelurahan Jaksel tidak lengkap')
    if len({a['id'] for a in all_rows}) != len(all_rows) or len({a['match_key'] for a in all_rows}) != len(all_rows):
        raise ValueError('Identitas batas wilayah duplikat')
    return all_rows


def import_boundaries(rows):
    initialize()
    body = json.dumps(rows, ensure_ascii=False).encode()
    digest = hashlib.sha256(body).hexdigest()
    path = database_path().parent / 'snapshots' / f'jabodetabek-{digest}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(body)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        save_areas(db, rows)
        set_meta(db, 'schoolBoundaries', dict(id=digest, fetchedAt=now(), count=len(rows),
                 source=BIG_LAYER, edition='BIG Oktober 2020; DKI menggunakan sumber DPMPTSP yang ada',
                 displayTolerance='BIG 0.0001 derajat; DKI asli'))


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot-dir', type=Path)
    parser.add_argument('--boundaries', action='store_true')
    args = parser.parse_args()
    if not args.snapshot_dir and not args.boundaries:
        parser.error('Pilih --snapshot-dir atau --boundaries')
    if args.boundaries:
        import_boundaries(fetch_boundaries())
    if args.snapshot_dir:
        print(json.dumps(import_schools(args.snapshot_dir), ensure_ascii=False, indent=2))
