"""Adversarial HTTP acceptance on synthetic isolated SQLite, no external calls."""
import csv
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app import app, db_connection, safe_csv
from backend.eligibility import validate_birth_date
from backend.security import model_answers
from test_smoke import applicant

TOKEN='synthetic-test-token-with-at-least-32-characters'
AUTH={'Authorization':'Bearer '+TOKEN}


class ProductAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,{'GIM_DB_PATH':str(Path(self.temp.name)/'isolated.sqlite3'),
            'GIM_COORDINATOR_TOKEN':TOKEN,'GIM_EVALUATION_MODE':'demo','GIM_GOOGLE_SPREADSHEET_ID':''})
        env.start();self.addCleanup(env.stop)
        self.client=TestClient(app)

    def post(self, **changes):
        return self.client.post('/api/v1/applications',json=applicant(**changes).model_dump())

    def test_all_private_routes_deny_missing_and_wrong_credentials(self):
        paths=[('GET','/api/v1/applications'),('GET','/api/v1/applications/guess'),
            ('GET','/api/v1/export.csv'),('POST','/api/v1/applications/guess/sync'),
            ('POST','/api/v1/preview'),('GET','/api/v1/admin/applications'),
            ('GET','/api/v1/admin/analytics'),('GET','/api/v1/admin/applications/guess/export.csv'),
            ('DELETE','/api/v1/admin/applications/guess')]
        for method,path in paths:
            for headers in ({},{'Authorization':'Bearer wrong'}):
                with self.subTest(path=path,headers=bool(headers)):
                    r=self.client.request(method,path,headers=headers,json=applicant().model_dump() if method=='POST' else None)
                    self.assertEqual(r.status_code,401,r.text)
                    self.assertNotIn('Тестовый кандидат',r.text)
                    self.assertEqual(r.headers['cache-control'],'no-store')
        with patch.dict(os.environ,{'GIM_COORDINATOR_TOKEN':''}):
            self.assertEqual(self.client.get('/api/v1/applications',headers=AUTH).status_code,401)

    def test_public_receipt_retry_and_conflicting_payload(self):
        a=self.post(application_id='receipt-one');b=self.post(application_id='receipt-one')
        self.assertEqual(a.status_code,201,a.text);self.assertTrue(a.json()['created']);self.assertFalse(b.json()['created'])
        self.assertEqual(set(a.json()),{'application_id','status','message','created','candidate_feedback'})
        c=self.post(application_id='receipt-one',motivation='Другое содержание достаточно длинное.')
        self.assertEqual(c.status_code,409)
        detail=self.client.get('/api/v1/applications/receipt-one',headers=AUTH)
        self.assertIn('evaluation',detail.json());self.assertEqual(detail.json()['answers']['motivation'],applicant().motivation)

    def test_age_boundary_dates_and_no_side_effects(self):
        for day in ('2008-10-23','2008-10-22','2008-10-21'):
            if day=='2008-10-23':
                with self.assertRaises(ValueError):validate_birth_date(day,date(2026,10,22))
            else:self.assertEqual(validate_birth_date(day,date(2026,10,22)),18)
        with self.assertRaises(ValueError):validate_birth_date('2008-02-29',date(2026,2,28))
        self.assertEqual(validate_birth_date('2008-02-29',date(2026,3,1)),18)
        for born,adult in [('2099-01-01',True),('2020-01-01',True),('2000-02-31',True),('',True),('01.01.2000',True),('2000-01-01',False)]:
            for path in ('/api/v1/applications','/api/v1/clarification','/api/v1/preview'):
                with self.subTest(born=born,path=path),patch('backend.app.evaluate_application') as e,patch('backend.clarification_agent.questions_before_verdict') as q:
                    r=self.client.post(path,json=applicant(birth_date=born,age_over_18=adult).model_dump(),headers=AUTH)
                    self.assertEqual(r.status_code,422,r.text);e.assert_not_called();q.assert_not_called()
        self.assertFalse((Path(self.temp.name)/'isolated.sqlite3').exists())

    def test_all_required_answers_and_valid_negative_answers(self):
        fields=['applicant_name','email','vk_url','birth_date','status','university','faculty','course','scientific_interests','experience','motivation','expectations','hobbies','source','university_other']
        for field in fields:
            with self.subTest(field=field):
                payload=applicant().model_dump();payload[field]=''
                self.assertEqual(self.client.post('/api/v1/applications',json=payload).status_code,422)
        self.assertEqual(self.post(foreign_languages=[]).status_code,422)
        self.assertEqual(self.post(experience='Нет опыта',scientific_interests='Нет',hobbies='Не указываю',availability_hours_month=0,desired_direction='undecided').status_code,201)

    def test_csv_single_full_roundtrip_and_formula_safety(self):
        original='История\nмузея; "тест" =1+1 🙂\r\nКонец'
        self.assertEqual(self.post(application_id='csv-one',experience=original).status_code,201)
        r=self.client.get('/api/v1/admin/applications/csv-one/export.csv',headers=AUTH)
        self.assertEqual(r.status_code,200);self.assertTrue(r.content.startswith(b'\xef\xbb\xbf'))
        rows=list(csv.reader(io.StringIO(r.content.decode('utf-8-sig'),newline=''),delimiter=';'))
        self.assertEqual(len(rows),2);self.assertEqual(len(rows[1]),43);self.assertEqual(rows[1][15],original)
        full=self.client.get('/api/v1/export.csv',headers=AUTH)
        self.assertEqual(full.content,r.content)
        self.assertEqual(self.client.get('/api/v1/admin/applications/unknown/export.csv',headers=AUTH).status_code,404)
        for formula in ('=1+1','+cmd','-1+2','@thing',' \t=1+1','\tformula','\r=1'):
            self.assertTrue(safe_csv(formula).startswith("'"))
        d=self.client.get('/api/v1/applications/csv-one',headers=AUTH).json()
        self.assertEqual(d['answers']['experience'],original)

    def test_ranking_analytics_all_1000_records_and_filters(self):
        scores=[3,2,2,1,0,None,None,3]
        with db_connection() as con:
            for i in range(1000):
                score=scores[i%8];payload=applicant().model_dump()
                e={'score':score,'summary':'Синтетика','evaluation_mode':'llm', 'score_status':'pilot_unvalidated' if score is not None else 'insufficient_evidence',
                   'recommended_direction':'media','scorecard':[{'criterion':c,'level':score} for c in ['motivation','skills','availability','form_and_contact','logistics']]}
                con.execute('INSERT INTO applications(id,created_at,applicant_name,payload,evaluation) VALUES(?,?,?,?,?)',
                    (f'syn-{i:04}',f'2026-10-{21 if i<500 else 22}T00:00:00+00:00','Синтетика',json.dumps(payload),json.dumps(e)))
        r=self.client.get('/api/v1/admin/analytics',headers=AUTH).json()
        self.assertEqual(r['total'],1000);self.assertEqual(r['score']['counts'],{'0':125,'1':125,'2':250,'3':250,'unknown':250})
        self.assertEqual(r['rated_count'],750);self.assertEqual(r['unrated_count'],250)
        self.assertEqual(r['criteria']['motivation']['unknown_count'],250)
        ids=[];ordered=[]
        for page in range(1,6):
            data=self.client.get(f'/api/v1/admin/applications?page_size=200&page={page}',headers=AUTH).json()
            ids += [v['application_id'] for v in data['items']]; ordered += [v['score'] for v in data['items']]
            self.assertTrue(all(v['tied'] for v in data['items'] if v['score'] is not None))
        self.assertEqual(len(set(ids)),1000);self.assertEqual(ordered,[3]*250+[2]*250+[1]*125+[0]*125+[None]*250)
        self.assertEqual(self.client.get('/api/v1/admin/analytics?date_from=2026-10-22',headers=AUTH).json()['total'],500)
        self.assertEqual(self.client.get('/api/v1/admin/analytics?direction=collections',headers=AUTH).json()['total'],0)
        self.assertEqual(self.client.get('/api/v1/admin/analytics?date_from=bad',headers=AUTH).status_code,422)
        csv_rows=list(csv.reader(io.StringIO(self.client.get('/api/v1/export.csv',headers=AUTH).content.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(csv_rows)-1,r['total'])

    def test_model_boundary_masks_top_level_and_embedded_contacts(self):
        data=applicant(experience='Тестовый кандидат synthetic@example.test https://vk.com/synthetic 2000-01-01 +7 (999) 123-45-67')
        sent=json.dumps(model_answers(data),ensure_ascii=False)
        for value in (data.applicant_name,data.email,data.vk_url,data.birth_date,'123-45-67'):
            self.assertNotIn(value,sent)
        self.assertNotIn('application_id',model_answers(data))
        self.assertIs(model_answers(data)['contact_available'],True)

    def test_delete_blocks_reregistration_and_sync(self):
        self.assertEqual(self.post(application_id='delete-one').status_code,201)
        r=self.client.delete('/api/v1/admin/applications/delete-one',headers=AUTH)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.get('/api/v1/applications/delete-one',headers=AUTH).status_code,404)
        self.assertEqual(self.post(application_id='delete-one').status_code,409)
        with db_connection() as con:self.assertEqual(con.execute('SELECT count(*) FROM applications').fetchone()[0],0)

    def test_public_clarification_has_no_internal_criteria_review(self):
        r=self.client.post('/api/v1/clarification',json=applicant().model_dump())
        self.assertEqual(r.status_code,200)
        self.assertEqual(set(r.json()),{'question','questions','round','evaluation_mode'})

    def test_external_llm_off_by_default_and_validation_errors_hide_input(self):
        with patch.dict(os.environ,{'GIM_EVALUATION_MODE':'llm','GIM_ALLOW_EXTERNAL_LLM':'0'}),patch('backend.app.evaluate_application') as model:
            self.assertEqual(self.post().status_code,503);model.assert_not_called()
        r=self.client.post('/api/v1/applications',json={'email':'private-secret-value'})
        self.assertEqual(r.status_code,422);self.assertNotIn('private-secret-value',r.text)

    def test_revocation_during_model_cannot_restore_payload(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from backend.app import save_application, delete_application, evaluate_demo
        data=applicant(application_id='race-revoke')
        entered,resume=Event(),Event()
        def slow(value):
            entered.set();self.assertTrue(resume.wait(5));return evaluate_demo(value)
        with patch('backend.app.evaluate_application',side_effect=slow),ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(save_application,data)
            self.assertTrue(entered.wait(5));delete_application('race-revoke');resume.set()
            from fastapi import HTTPException
            with self.assertRaises(HTTPException):future.result(timeout=5)
        with db_connection() as con:self.assertEqual(con.execute('SELECT count(*) FROM applications').fetchone()[0],0)

    def test_google_delete_failure_blocks_sync_until_retried(self):
        self.post(application_id='delete-google')
        with patch.dict(os.environ,{'GIM_GOOGLE_SPREADSHEET_ID':'synthetic'}),patch('backend.google_sheets.clear_application',side_effect=RuntimeError('secret-provider-body')):
            r=self.client.delete('/api/v1/admin/applications/delete-google',headers=AUTH)
        self.assertEqual(r.status_code,503);self.assertNotIn('secret-provider-body',r.text)
        self.assertEqual(self.client.post('/api/v1/applications/delete-google/sync',headers=AUTH).status_code,409)
        with patch.dict(os.environ,{'GIM_GOOGLE_SPREADSHEET_ID':'synthetic'}),patch('backend.google_sheets.clear_application') as clear:
            self.assertEqual(self.client.delete('/api/v1/admin/applications/delete-google',headers=AUTH).status_code,200)
            clear.assert_called_once_with('delete-google')

    def test_feedback_has_specific_constraints_and_no_raw_html_or_score(self):
        a=self.post(desired_direction='collections',expectations='Хочу оплачиваемую работу <script>alert(1)</script>',availability_hours_month=0)
        message=a.json()['candidate_feedback']
        self.assertIn('Места ограничены',message);self.assertIn('0 часов',message);self.assertIn('не гарантирует оплачиваемую',message)
        self.assertNotIn('<script>',message);self.assertNotIn('score',a.text)
        b=self.post(desired_direction='undecided')
        self.assertIn('не определились',b.json()['candidate_feedback'])
