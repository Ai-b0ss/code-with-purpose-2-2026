"""Check live runners reach transport under the current mandatory/public contract."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from backend.app import db_connection, validate_submission
from backend.google_sheets import SheetSyncError
from scripts import check_google_sheets_live, qa_agent_live, qa_four_live


def test_all_live_fixtures_are_valid_and_original_expert_inputs_stay_unchanged():
    from backend.app import ApplicationInput
    original = (Path(__file__).parents[1] / 'qa/four_cases.json').read_bytes()
    fixtures = [c['input'] for c in qa_agent_live.cases]
    fixtures += [qa_four_live.complete_synthetic_form(c['answers'])
                 for c in json.loads(original)['cases']]
    with patch.dict(os.environ, {'GIM_EVALUATION_MODE': 'demo'}):
        for item in fixtures:
            validate_submission(ApplicationInput(**item))
    assert len(qa_agent_live.cases) == 24
    assert (Path(__file__).parents[1] / 'qa/four_cases.json').read_bytes() == original


def test_google_probe_survives_delivery_failure_and_private_receipt_contract(capsys):
    seen = []

    def write(application_id, values):
        with db_connection() as con:
            assert con.execute('SELECT COUNT(*) FROM applications').fetchone()[0] == 1
        seen.append(application_id)
        if len(seen) == 1:
            assert os.environ['GIM_GOOGLE_SPREADSHEET_ID'].startswith('invalid-')
            raise SheetSyncError('google_http_404')
        assert os.environ['GIM_GOOGLE_SPREADSHEET_ID'] == 'synthetic-spreadsheet'
        return 'https://docs.google.com/spreadsheets/d/synthetic/edit#gid=0&range=A2'

    with patch.dict(os.environ, {'GIM_GOOGLE_SPREADSHEET_ID': 'synthetic-spreadsheet'}), \
         patch.object(sys, 'argv', ['probe', '--exercise-failure']), \
         patch('backend.google_sheets.write_row', side_effect=write):
        assert check_google_sheets_live.main() == 0
    assert len(seen) == 2 and len(set(seen)) == 1
    assert 'repeat created: False' in capsys.readouterr().out


def test_live_runner_does_not_count_missing_private_result_or_duplicate_as_pass():
    import copy
    row = {'clarification': {'status': 200}, 'final': {'status': 201},
           'receipt': {'created': True},
           'duplicate': {'status': 201, 'body': {'created': False}},
           'stored': {'status': 200, 'body': {'evaluation': {'evaluation_mode': 'llm',
                      'scorecard': [{}] * 5, 'score': None, 'provenance': {'model': 'synthetic'}}}}}
    assert qa_agent_live.technical_pass(row)
    for key in ['duplicate', 'stored', 'receipt']:
        corrupt = copy.deepcopy(row)
        corrupt.pop(key)
        assert not qa_agent_live.technical_pass(corrupt)
    corrupt = copy.deepcopy(row)
    corrupt['receipt']['evaluation'] = {}
    assert not qa_agent_live.technical_pass(corrupt)
