"""One administrative identity shared by business and school snapshots."""
import json
import re

from .ingestion import area_key

CITIES = {
    '3101': 'Kepulauan Seribu', '3171': 'Jakarta Pusat', '3172': 'Jakarta Utara',
    '3173': 'Jakarta Barat', '3174': 'Jakarta Selatan', '3175': 'Jakarta Timur',
    '3201': 'Kabupaten Bogor', '3216': 'Kabupaten Bekasi', '3271': 'Kota Bogor',
    '3275': 'Kota Bekasi', '3276': 'Kota Depok', '3603': 'Kabupaten Tangerang',
    '3671': 'Kota Tangerang', '3674': 'Kota Tangerang Selatan',
}


def city_name(value):
    name = re.sub(r'\s+', ' ', str(value or '').strip()).casefold()
    name = re.sub(r'^(kota|kab\.?|kabupaten)\s+(adm\.?|administrasi)\s+', '', name)
    name = re.sub(r'^kab\.?\s+', 'kabupaten ', name)
    if 'kep. seribu' in name or 'kepulauan seribu' in name:
        return 'Kepulauan Seribu'
    for city in CITIES.values():
        if name == city.casefold():
            return city
    raise ValueError(f'Kota/kabupaten di luar cakupan: {value}')


def locality(value):
    return re.sub(r'^(?:kec\.?|kecamatan|kel\.?|kelurahan|desa)\s+', '', str(value or '').strip(), flags=re.I).title()


def school_area_key(city, district, subdistrict):
    city, district = city_name(city), locality(district)
    # Source formatting prefixes, scoped to their city; no fuzzy name matching.
    if city == 'Kota Bogor' and district.startswith('Kota Bogor '):
        district = district.removeprefix('Kota ')
    if city == 'Kepulauan Seribu':
        district = district.replace('Kepulauan Seribu ', 'Kep. Seribu ')
    return area_key(city, district, locality(subdistrict))


def all_areas(db):
    return [dict(r) for r in db.execute("""SELECT a.id,a.district,a.subdistrict,
        COALESCE(m.city,'Jakarta Selatan') city, COALESCE(m.province,'DKI Jakarta') province
        FROM areas a LEFT JOIN area_metadata m ON m.id=a.id ORDER BY city,a.district,a.subdistrict""")]


def remap_schools(db):
    db.execute('UPDATE schools SET areaId=(SELECT id FROM areas WHERE match_key=schools.areaKey)')
    db.execute('''UPDATE schools SET
        district=(SELECT district FROM areas WHERE id=schools.areaId),
        subdistrict=(SELECT subdistrict FROM areas WHERE id=schools.areaId)
        WHERE areaId IS NOT NULL''')


def save_areas(db, areas):
    for a in areas:
        db.execute('''INSERT INTO areas(id,district,subdistrict,match_key,feature)
            VALUES(:id,:district,:subdistrict,:match_key,:feature)
            ON CONFLICT(id) DO UPDATE SET district=excluded.district,subdistrict=excluded.subdistrict,
            match_key=excluded.match_key,feature=excluded.feature''', a)
        if 'city' in a:
            db.execute('INSERT OR REPLACE INTO area_metadata VALUES(?,?,?,?)',
                       (a['id'], a['city'], a['province'], a['source']))
    remap_schools(db)
