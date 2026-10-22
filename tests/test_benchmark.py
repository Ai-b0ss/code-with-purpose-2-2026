from unittest import TestCase
from backend.benchmark import metrics, frozen_split, report

class BenchmarkTests(TestCase):
    def test_hand_calculated_metrics_zero_null_and_empty(self):
        r=metrics([0,1,2,3],[0,None,3,3])
        self.assertEqual(r['numeric_pairs'],3);self.assertEqual(r['null_count'],1)
        self.assertEqual(r['coverage'],.75);self.assertEqual(r['accuracy'],.5)
        self.assertAlmostEqual(r['mae'],1/3)
        self.assertEqual(sum(map(sum,r['confusion_matrix'])),3)
        self.assertEqual(r['per_class'][1]['recall'],0)
        self.assertIsNone(metrics([],[])['mae'])
        self.assertEqual(metrics([0],[0])['accuracy'],1)
        with self.assertRaises(ValueError):metrics([0],[4])

    def test_split_is_stable_duplicate_safe_and_metrics_use_holdout_only(self):
        records=[{'candidate_id':str(i),'fingerprint':str(i),'expert_score':i%4,'season':'2024'} for i in range(32)]
        records.append({**records[0],'candidate_id':'duplicate'})
        split=frozen_split(records);self.assertEqual(split,frozen_split(list(reversed(records))))
        self.assertEqual(split['0'],split['duplicate'])
        predictions={r['candidate_id']:r['expert_score'] for r in records if split[r['candidate_id']]=='test'}
        self.assertEqual(report(records,predictions,split)['test']['model']['accuracy'],1)
        with self.assertRaises(ValueError):report(records,{},split)
        broken={**split,'duplicate':'test' if split['0']=='tune' else 'tune'}
        with self.assertRaises(ValueError):report(records,predictions,broken)
