"""Historical zero/unknown/type semantics; no real candidate fixtures."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from backend.historical_dataset import read_sources, birth, outcome, score

class Sheet:
    def __init__(self,title,rows):self.title,self.rows=title,rows
    def iter_rows(self,values_only=True):return iter(self.rows)
class Workbook(list):
    def close(self):pass

class HistoricalTests(TestCase):
    def test_zero_is_not_missing_and_missing_admission_is_unknown(self):
        self.assertEqual(score(0),0);self.assertEqual(score('0.0'),0);self.assertIsNone(score(None))
        self.assertIsNone(outcome('—'));self.assertIsNone(outcome(0));self.assertIs(outcome(False),False)
        self.assertIs(outcome('ДА'),True);self.assertIs(outcome('нет'),False)
        self.assertEqual(birth(datetime(2000,2,29)),'2000-02-29');self.assertIsNone(birth(datetime(1900,1,1)))
        self.assertIsNone(birth(2007));self.assertIsNone(birth('31.02.2000'))

    def test_three_sheets_duplicate_detection_stable_id_and_no_network(self):
        header=['Сколько баллов дали','Взяли ли в итоге','Дата рождения','ВУЗ','Чего Вы ожидаете']
        wb=Workbook([Sheet(str(i),[header,[0,None,datetime(2000,1,1),'Синтетический вуз','Практика'],[None]*5]) for i in range(3)])
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'2024.xlsx';path.write_bytes(b'synthetic placeholder')
            with patch('openpyxl.load_workbook',return_value=wb),patch('socket.socket',side_effect=AssertionError('Network forbidden')):
                records,summary=read_sources([path]);again,_=read_sources([path])
        self.assertEqual(summary['total'],3);self.assertEqual(len(summary['sheets']),3)
        self.assertEqual(summary['duplicate_rows'],2);self.assertEqual(summary['expert_labels'],{'0':3})
        self.assertTrue(all(r['accepted'] is None for r in records));self.assertEqual(records,again)
        self.assertEqual(len({r['candidate_id'] for r in records}),3)

    def test_wrong_header_fails_without_guessing(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'x.xlsx';path.write_bytes(b'x')
            with patch('openpyxl.load_workbook',return_value=Workbook([Sheet('bad',[['x']])])),self.assertRaises(ValueError):
                read_sources([path])
