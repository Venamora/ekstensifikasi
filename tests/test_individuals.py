"""Captured school records; all mutations use a temporary database."""
import csv
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from backend.database import connect, get_meta, initialize, set_meta
from backend.geography import save_areas, school_area_key
from backend.ingestion import normalize, parse_boundaries, refresh_reserved, reserve_refresh
from backend.main import app
from backend.school_ingestion import import_schools, normalize_boundaries, phone_status

FIXTURES = Path(__file__).parent / 'fixtures'


class IndividualTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'GEOPROSPECT_DB': str(Path(self.temp.name) / 'test.sqlite3')})
        self.env.start()
        self.addCleanup(self.env.stop)
        initialize()
        self.bundle = Path(self.temp.name) / 'bundle'
        shutil.copytree(FIXTURES / 'schools', self.bundle)
        self.boundaries = json.loads((FIXTURES / 'boundaries.json').read_text())
        self.areas = parse_boundaries(self.boundaries)
        self.registry = json.loads((FIXTURES / 'registry.json').read_text())
        records, summary = normalize(self.registry, self.areas, 'fixture')
        with connect() as db:
            save_areas(db, self.areas + json.loads((self.bundle / 'areas.json').read_text()))
            cols = list(records[0])
            db.executemany(f"INSERT INTO businesses ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                           [tuple(r[c] for c in cols) for r in records])
            set_meta(db, 'snapshot', summary)
        self.summary = import_schools(self.bundle)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def get(self, path, params=None):
        result = self.client.get(path, params=params)
        self.assertEqual(result.status_code, 200, result.text[:300])
        return result.json()

    def test_programs_filters_exports_and_counts_reconcile(self):
        for selection in [{}, {'program':'gmm'}, {'program':'simpel'}, {'education_level':'SMP'},
                          {'phone_status':'unavailable'}, {'area_quality':'unmatched'},
                          {'city':'Kota Bogor'}, {'school_status':'NEGERI'}, {'q':'%'}]:
            with self.subTest(selection=selection):
                data = self.get('/api/individuals', selection)
                stats = self.get('/api/individuals/statistics', selection)
                areas = self.get('/api/individuals/areas', selection)
                districts = self.get('/api/individuals/opportunities', {**selection, 'level':'district'})
                response = self.client.get('/api/individuals/export', params=selection)
                exported = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
                self.assertEqual(data['total'], stats['total'])
                self.assertEqual(len(exported), stats['total'])
                self.assertEqual(sum(a['count'] for a in areas['items']), stats['matched'])
                self.assertEqual(sum(a['count'] for a in districts['items']), stats['matched'])
                self.assertEqual(stats['matched'] + stats['unmatched'], stats['total'])
                self.assertEqual(sum(stats[k] for k in ['phoneAvailable','phoneReview','phoneMissing','phoneUnavailable']), stats['total'])
                if selection.get('program') == 'gmm':
                    self.assertTrue(all(r['level'] == 'SMA' for r in data['items']))
                if selection.get('program') == 'simpel':
                    self.assertTrue(all(r['level'] in ('SD','SMP') for r in data['items']))
        response = self.client.get('/api/individuals/export?format=xlsx')
        rows = list(load_workbook(io.BytesIO(response.content), read_only=True).active.values)
        self.assertEqual(len(rows), self.summary['storedRows'] + 1)
        phone_index = rows[0].index('phone')
        self.assertTrue(any(isinstance(r[phone_index], str) and r[phone_index].startswith('0') for r in rows[1:]))

    def test_area_selection_and_overlap_keep_city_identity(self):
        rows = self.get('/api/individuals')['items']
        for row in rows:
            if not row['areaId']:
                continue
            selection = {k:row[k] for k in ['city','district','subdistrict','program']}
            selection['area_id'] = row['areaId']
            selected = self.get('/api/individuals', selection)
            self.assertIn(row['npsn'], [r['npsn'] for r in selected['items']])
        cross = self.get('/api/map/areas?period=all')['items']
        self.assertTrue(any(a['pebisnis'] is not None for a in cross))
        self.assertTrue(all(a['pebisnis'] is None for a in cross if not a['id'].startswith('3174')))
        self.assertEqual(sum(a['gmm'] + a['simpel'] for a in cross), self.summary['matched'])
        self.assertEqual(len(self.get('/api/areas/geojson')['features']), 65)
        self.assertGreater(len(self.get('/api/areas/geojson?scope=jabodetabek')['features']), 65)
        self.assertEqual(self.get('/api/opportunities', {'city':'Kota Bogor'})['items'], [])
        self.assertNotEqual(school_area_key('Kota Bogor', 'Bogor Barat', 'X'), school_area_key('Kabupaten Bogor', 'Bogor Barat', 'X'))
        self.assertEqual(school_area_key('Kota Bogor', 'Kota Bogor Barat', 'X'), school_area_key('Kota Bogor','Bogor Barat','X'))
        self.assertEqual(school_area_key('Kepulauan Seribu','Kepulauan Seribu Utara','Pulau Kelapa'), school_area_key('Kepulauan Seribu','Kep. Seribu Utara','Pulau Kelapa'))

    def test_shortlist_identity_persistence_and_validation(self):
        row = next(r for r in self.get('/api/individuals')['items'] if r['npsn'].startswith('NP'))
        path = '/api/individuals/shortlist'
        created = self.client.post(path, json={'npsn':row['npsn']})
        self.assertEqual(created.status_code, 201)
        item = created.json()
        self.assertEqual(self.client.post(path, json={'npsn':row['npsn']}).json()['id'], item['id'])
        self.assertEqual(self.client.patch(path+'/'+item['id'], json={'status':'Invented'}).status_code, 422)
        self.assertEqual(self.client.patch(path+'/'+item['id'], json={'status':'Contacted','notes':'=1+1'}).status_code, 200)
        self.assertIn("'=1+1", self.client.get(path+'/export').text)
        self.assertEqual(self.get(path, {'program':row['program']})['total'], 1)
        self.assertEqual(self.get(path, {'program':'simpel' if row['program']=='gmm' else 'gmm'})['total'], 0)
        import_schools(self.bundle)
        self.assertEqual(self.get(path)['items'][0]['notes'], '=1+1')
        with connect() as db:
            db.execute('DELETE FROM schools WHERE npsn=?', (row['npsn'],))
        self.assertFalse(self.get(path)['items'][0]['inCurrentSnapshot'])
        self.assertEqual(self.client.delete(path+'/'+item['id']).status_code, 204)
        self.assertEqual(self.get('/api/shortlist')['total'], 0)

    def test_invalid_import_rolls_back_and_missing_details_remain(self):
        rows = self.get('/api/individuals')['items']
        missing = next(r for r in rows if r['detailStatus']=='unavailable')
        self.assertEqual(missing['phoneStatus'], 'unavailable')
        self.assertTrue(missing['schoolName'])
        (self.bundle/'detail-sekolah.jsonl').write_text('')
        with self.assertRaises(ValueError):
            import_schools(self.bundle)
        self.assertEqual(self.get('/api/individuals')['items'], rows)
        self.assertEqual(self.get('/api/individuals/data/status')['snapshot']['id'], self.summary['id'])

    def test_business_refresh_preserves_shared_geography_and_schools(self):
        school_before = self.get('/api/individuals')['items']
        area_before = self.get('/api/areas/geojson?scope=jabodetabek')
        self.assertIsNone(reserve_refresh())
        with patch('backend.ingestion.download_json', side_effect=[(self.boundaries,b'boundaries',{}), (self.registry,json.dumps(self.registry).encode(),{})]):
            refresh_reserved()
        with connect() as db:
            self.assertIsNone(get_meta(db, 'lastError'))
        self.assertEqual(self.get('/api/individuals')['items'], school_before)
        self.assertEqual(len(self.get('/api/areas/geojson?scope=jabodetabek')['features']), len(area_before['features']))

    def test_input_validation_empty_snapshot_and_phone_quality(self):
        for query in ['program=wrong','education_level=TK','phone_status=active','page_size=101','sort=npsn;DROP','direction=sideways','page=0']:
            self.assertEqual(self.client.get('/api/individuals?'+query).status_code, 422)
        a=self.get('/api/individuals?page_size=1&page=1')['items'][0]
        b=self.get('/api/individuals?page_size=1&page=2')['items'][0]
        self.assertNotEqual(a['npsn'], b['npsn'])
        self.assertEqual(self.get('/api/individuals', {'q':"' OR 1=1--"})['total'], 0)
        self.assertEqual(phone_status('00000000'), 'missing')
        self.assertEqual(phone_status('021 1234567'), 'available')
        self.assertEqual(phone_status('123'), 'review')
        feature = {'properties':{'KDEPUM':'32.71.01.1001','WADMKC':'Bogor Tengah','WADMKD':'Fixture'},'geometry':{'type':'Point','coordinates':[106,-6]}}
        with self.assertRaises(ValueError):
            normalize_boundaries([feature], 'test')
        with connect() as db:
            db.execute('DELETE FROM schools')
            db.execute("DELETE FROM metadata WHERE key='schoolSnapshot'")
        self.assertIsNone(self.get('/api/individuals/data/status')['snapshot'])
        self.assertTrue(all(r['gmm'] is None and r['simpel'] is None for r in self.get('/api/map/areas')['items']))
