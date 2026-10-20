"""School prospect endpoints; no student or bank customer records."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .database import connect, get_meta
from .exports import export_file
from .geography import all_areas
from .ingestion import now

router = APIRouter()
Program = Literal['gmm', 'simpel']
Priority = Literal['High', 'Medium', 'Low']
Status = Literal['Identified', 'To Be Visited', 'Contacted', 'Follow-up', 'Qualified', 'Not Interested', 'Closed']
EXPORT_COLUMNS = ['npsn', 'schoolName', 'program', 'level', 'educationForm', 'schoolStatus',
                  'city', 'district', 'subdistrict', 'address', 'phone', 'phoneStatus', 'email',
                  'website', 'latitude', 'longitude', 'accreditation', 'areaId', 'detailStatus',
                  'sourceUpdatedAt', 'fetchedAt', 'source', 'snapshotId']
SORT_COLUMNS = set(EXPORT_COLUMNS) - {'source', 'snapshotId'}


def school_filters(program: Program | None = None, q: str = Query('', max_length=200),
                   city: str = Query('', max_length=100), district: str = Query('', max_length=100),
                   subdistrict: str = Query('', max_length=100), area_id: str = Query('', max_length=30),
                   education_level: Literal['SD', 'SMP', 'SMA'] | None = None,
                   school_status: Literal['NEGERI', 'SWASTA'] | None = None,
                   phone_status: Literal['available', 'missing', 'review', 'unavailable'] | None = None,
                   area_quality: Literal['all', 'matched', 'unmatched'] = 'all'):
    return locals()


def contains(value):
    return '%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def school_where(selection):
    conditions, args = [], []
    for parameter, column in [('program', 'program'), ('city', 'city'), ('district', 'district'),
                              ('subdistrict', 'subdistrict'), ('area_id', 'areaId'), ('education_level', 'level'),
                              ('school_status', 'schoolStatus'), ('phone_status', 'phoneStatus')]:
        if selection.get(parameter):
            conditions.append(f's.{column}=? COLLATE NOCASE')
            args.append(selection[parameter])
    if selection.get('q'):
        conditions.append("(s.schoolName LIKE ? ESCAPE '\\' OR s.npsn LIKE ? ESCAPE '\\' OR s.address LIKE ? ESCAPE '\\')")
        args.extend([contains(selection['q'])] * 3)
    if selection.get('area_quality') in ('matched', 'unmatched'):
        conditions.append('s.areaId IS ' + ('NOT NULL' if selection['area_quality'] == 'matched' else 'NULL'))
    return ' AND '.join(conditions) or '1=1', args


def ordering(sort, direction):
    if sort not in SORT_COLUMNS or direction not in ('asc', 'desc'):
        raise HTTPException(422, 'Kolom atau arah pengurutan tidak valid.')
    return f's.{sort} IS NULL, s.{sort} COLLATE NOCASE {direction.upper()},s.npsn'


@router.get('/api/individuals/data/status')
def school_data_status():
    with connect() as db:
        return dict(snapshot=get_meta(db, 'schoolSnapshot'), boundaries=get_meta(db, 'schoolBoundaries'),
                    areaCount=db.execute('SELECT COUNT(*) FROM areas').fetchone()[0], refreshConfigured=False,
                    refreshState='idle', mode='static')


@router.get('/api/individuals/filters')
def school_filter_options():
    with connect() as db:
        locations = [dict(r) for r in db.execute('SELECT DISTINCT city,district,subdistrict,areaId FROM schools ORDER BY city,district,subdistrict')]
        return dict(locations=locations, areas=all_areas(db), programs=['gmm', 'simpel'], levels=['SD', 'SMP', 'SMA'])


@router.get('/api/individuals/statistics')
def school_statistics(selection: dict = Depends(school_filters)):
    condition, args = school_where(selection)
    with connect() as db:
        row = dict(db.execute(f'''SELECT COUNT(*) total, COUNT(areaId) matched,
            COALESCE(SUM(phoneStatus='available'),0) phoneAvailable,
            COALESCE(SUM(phoneStatus='review'),0) phoneReview,
            COALESCE(SUM(phoneStatus='missing'),0) phoneMissing,
            COALESCE(SUM(phoneStatus='unavailable'),0) phoneUnavailable,
            COUNT(DISTINCT level) levels,
            COALESCE(SUM(EXISTS(SELECT 1 FROM school_shortlist ss WHERE ss.npsn=s.npsn)),0) shortlisted
            FROM schools s WHERE {condition}''', args).fetchone())
        baseline, baseline_args = school_where({'program': selection.get('program')})
        row['snapshotTotal'] = db.execute(f'SELECT COUNT(*) FROM schools s WHERE {baseline}', baseline_args).fetchone()[0]
        row['unmatched'] = row['total'] - row['matched']
        row['categoryBreakdown'] = [dict(r) for r in db.execute(f'SELECT educationForm name,COUNT(*) count FROM schools s WHERE {condition} GROUP BY educationForm ORDER BY count DESC', args)]
        return row


@router.get('/api/individuals/areas')
def school_areas(selection: dict = Depends(school_filters)):
    return school_opportunities(selection, 'subdistrict')


@router.get('/api/individuals/opportunities')
def school_opportunities(selection: dict = Depends(school_filters), level: Literal['district', 'subdistrict'] = 'subdistrict'):
    condition, args = school_where(selection)
    with connect() as db:
        group = 's.areaId' if level == 'subdistrict' else "s.city || '|' || s.district"
        aggregates = {r['groupId']: dict(r) for r in db.execute(f'''SELECT {group} groupId,
            COUNT(*) count,COUNT(DISTINCT s.level) levels,SUM(phoneStatus='available') phoneAvailable,
            SUM(EXISTS(SELECT 1 FROM school_shortlist ss WHERE ss.npsn=s.npsn)) shortlisted
            FROM schools s WHERE ({condition}) AND s.areaId IS NOT NULL GROUP BY {group}''', args)}
        items, seen = [], set()
        for area in all_areas(db):
            if any(selection.get(k) and selection[k].casefold() != area[v].casefold()
                   for k, v in [('city', 'city'), ('district', 'district'), ('subdistrict', 'subdistrict'), ('area_id', 'id')]):
                continue
            key = area['id'] if level == 'subdistrict' else area['city'] + '|' + area['district']
            if key in seen:
                continue
            seen.add(key)
            counts = aggregates.get(key, dict(count=0, levels=0, phoneAvailable=0, shortlisted=0))
            items.append({**area, **counts, 'id': key, 'name': area['subdistrict'] if level == 'subdistrict' else area['district']})
        maximum = max((r['count'] for r in items), default=0)
        for item in items:
            item['index'] = round(100 * item['count'] / maximum, 1) if maximum else 0
            item['phoneCoverage'] = round(100 * item['phoneAvailable'] / item['count'], 1) if item['count'] else None
        items.sort(key=lambda r: (-r['count'], r['city'], r['name']))
        unmatched = db.execute(f'SELECT COUNT(*) FROM schools s WHERE ({condition}) AND areaId IS NULL', args).fetchone()[0]
        return dict(items=items, unmatched=unmatched, method='100 × sekolah wilayah / jumlah sekolah terbanyak sesuai filter')


@router.get('/api/map/areas')
def map_areas(period: str | None = Query(None, pattern=r'^(\d{4}|all)$')):
    with connect() as db:
        snapshot = get_meta(db, 'snapshot', {})
        period = period or next(iter(snapshot.get('periods', [])), None)
        business_counts = dict(db.execute('SELECT areaId,COUNT(*) FROM businesses WHERE (? IS NULL OR ?=\'all\' OR dataPeriod=?) AND areaId IS NOT NULL GROUP BY areaId', (period, period, period)))
        school_counts = {(r[0], r[1]): r[2] for r in db.execute('SELECT areaId,program,COUNT(*) FROM schools WHERE areaId IS NOT NULL GROUP BY areaId,program')}
        school_ready = get_meta(db, 'schoolSnapshot') is not None
        return dict(period=period, items=[dict(**a,
            pebisnis=business_counts.get(a['id'], 0) if a['id'].startswith('3174') and snapshot else None,
            gmm=school_counts.get((a['id'], 'gmm'), 0) if school_ready else None,
            simpel=school_counts.get((a['id'], 'simpel'), 0) if school_ready else None) for a in all_areas(db)])


@router.get('/api/individuals/export')
def school_export(selection: dict = Depends(school_filters), format: Literal['csv', 'xlsx'] = 'csv',
                  sort: str = 'schoolName', direction: str = 'asc'):
    condition, args = school_where(selection)
    order = ordering(sort, direction)
    with connect() as db:
        rows = [dict(r) for r in db.execute(f'SELECT s.* FROM schools s WHERE {condition} ORDER BY {order}', args)]
    return export_file(rows, EXPORT_COLUMNS, format, 'geoprospect-schools')


class SchoolShortlistCreate(BaseModel):
    npsn: str = Field(pattern=r'^[A-Z0-9]{8}$')
    priority: Priority = 'Medium'


class SchoolShortlistUpdate(BaseModel):
    priority: Priority | None = None
    status: Status | None = None
    notes: str | None = Field(None, max_length=4000)


@router.get('/api/individuals/shortlist/export')
def school_shortlist_export(program: Program | None = None):
    with connect() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM school_shortlist WHERE (? IS NULL OR program=?) ORDER BY updatedAt DESC', (program, program))]
    return export_file(rows, ['npsn', 'schoolName', 'program', 'city', 'district', 'subdistrict', 'address', 'phone', 'priority', 'status', 'notes', 'updatedAt'], 'csv', 'school-shortlist')


@router.get('/api/individuals/shortlist')
def school_shortlist(program: Program | None = None, q: str = Query('', max_length=200),
                     page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    with connect() as db:
        condition = "(? IS NULL OR program=?) AND (schoolName LIKE ? ESCAPE '\\' OR address LIKE ? ESCAPE '\\')"
        args = [program, program, contains(q), contains(q)]
        rows = db.execute(f'''SELECT ss.*,EXISTS(SELECT 1 FROM schools s WHERE s.npsn=ss.npsn) inCurrentSnapshot
            FROM school_shortlist ss WHERE {condition} ORDER BY updatedAt DESC,id LIMIT ? OFFSET ?''', [*args, page_size, (page-1)*page_size])
        return dict(items=[dict(r) for r in rows], total=db.execute(f'SELECT COUNT(*) FROM school_shortlist WHERE {condition}', args).fetchone()[0])


@router.post('/api/individuals/shortlist', status_code=201)
def school_shortlist_create(payload: SchoolShortlistCreate):
    with connect() as db:
        existing = db.execute('SELECT * FROM school_shortlist WHERE npsn=?', (payload.npsn,)).fetchone()
        if existing:
            return dict(existing)
        row = db.execute('SELECT * FROM schools WHERE npsn=?', (payload.npsn,)).fetchone()
        if not row:
            raise HTTPException(404, 'Sekolah tidak ditemukan.')
        item = {k: row[k] for k in ['npsn', 'schoolName', 'program', 'city', 'district', 'subdistrict', 'address', 'phone']}
        item.update(id=uuid.uuid4().hex, priority=payload.priority, status='Identified', notes='', updatedAt=now())
        db.execute(f"INSERT OR IGNORE INTO school_shortlist ({','.join(item)}) VALUES ({','.join('?' for _ in item)})", tuple(item.values()))
        return dict(db.execute('SELECT * FROM school_shortlist WHERE npsn=?', (payload.npsn,)).fetchone())


@router.patch('/api/individuals/shortlist/{item_id}')
def school_shortlist_update(item_id: str, payload: SchoolShortlistUpdate):
    changes = payload.model_dump(exclude_none=True)
    with connect() as db:
        if not db.execute('SELECT 1 FROM school_shortlist WHERE id=?', (item_id,)).fetchone():
            raise HTTPException(404, 'Prospek tidak ditemukan.')
        if changes:
            changes['updatedAt'] = now()
            db.execute(f"UPDATE school_shortlist SET {','.join(k+'=?' for k in changes)} WHERE id=?", [*changes.values(), item_id])
        return dict(db.execute('SELECT * FROM school_shortlist WHERE id=?', (item_id,)).fetchone())


@router.delete('/api/individuals/shortlist/{item_id}', status_code=204)
def school_shortlist_delete(item_id: str):
    with connect() as db:
        if not db.execute('DELETE FROM school_shortlist WHERE id=?', (item_id,)).rowcount:
            raise HTTPException(404, 'Prospek tidak ditemukan.')
    return Response(status_code=204)


@router.get('/api/individuals')
def schools(selection: dict = Depends(school_filters), page: int = Query(1, ge=1, le=1000000),
            page_size: int = Query(25, ge=1, le=100), sort: str = 'schoolName', direction: str = 'asc'):
    condition, args = school_where(selection)
    order = ordering(sort, direction)
    with connect() as db:
        total = db.execute(f'SELECT COUNT(*) FROM schools s WHERE {condition}', args).fetchone()[0]
        rows = db.execute(f'''SELECT s.*,EXISTS(SELECT 1 FROM school_shortlist ss WHERE ss.npsn=s.npsn) shortlisted
            FROM schools s WHERE {condition} ORDER BY {order} LIMIT ? OFFSET ?''', [*args, page_size, (page-1)*page_size])
        return dict(items=[dict(r) for r in rows], total=total, page=page, pageSize=page_size)


@router.get('/api/individuals/{npsn}')
def school_detail(npsn: str):
    with connect() as db:
        row = db.execute('SELECT s.*,EXISTS(SELECT 1 FROM school_shortlist ss WHERE ss.npsn=s.npsn) shortlisted FROM schools s WHERE npsn=?', (npsn,)).fetchone()
        if not row:
            raise HTTPException(404, 'Sekolah tidak tersedia pada snapshot aktif.')
        return dict(row)
