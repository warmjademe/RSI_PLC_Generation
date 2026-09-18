import json
from pathlib import Path
import tempfile
import unittest

from experiments.hard45_study.setup import first_passed, relocate
from experiments.hard45_study.run import report


class Hard45ProtocolTests(unittest.TestCase):
    def test_selection_requires_all_three_first_candidate_stages(self):
        for stages, expected in [({'compile': 'pass'}, False),
                                  ({'compile': 'pass', 'runtime': 'pass', 'formal': 'unknown'}, False),
                                  ({'compile': 'pass', 'runtime': 'pass', 'formal': 'pass'}, True)]:
            self.assertEqual(first_passed({'candidate_results': [{'last_status_by_stage': stages}]}), expected)
        with self.assertRaises(ValueError):
            first_passed({'candidate_results': []})

    def test_relocation_preserves_validator_logic_and_external_toolchain(self):
        source = {'command': ['python', '--lock', '/study/old/locks/v', '--tool-root', '/tools/frozen'],
                  'env': {'PYTHONPATH': '/study/old/evaluator_source', 'QUALIFIED_BACKEND': '1'}}
        changed = relocate(source, Path('/study/old'), Path('/study/new'))
        self.assertEqual(changed['command'][-1], '/tools/frozen')
        self.assertEqual(changed['command'][2], '/study/new/locks/v')
        self.assertEqual(changed['env']['QUALIFIED_BACKEND'], '1')
        self.assertEqual(source['command'][2], '/study/old/locks/v')

    def test_report_separates_existing_success_new_success_and_unsettled_judge(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            def write(name, value):
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(value))
            ids = [f'TEST_{i:02d}' for i in range(45)]
            write('study.json', {'task_ids': ids, 'pending_task_ids': ids[6:], 'carried_forward_task_ids': ids[:6]})
            for i, tid in enumerate(ids):
                write(f'task_local_inputs/{tid}.json', {'original_budget': {'total_tokens': 100, 'model_calls': 5}})
                if i < 6:
                    write(f'tests/OurMethod/{tid}/summary.json', {'success': True, 'carried_forward': True, 'judge_status': 'pass'})
            write(f'tests/OurMethod/{ids[6]}/summary.json', {'success': True, 'carried_forward': False, 'judge_status': 'pass'})
            for tid in ids[6:8]:
                write(f'tests/OurMethod/{tid}/generation/result.json', {'budget': {'total_tokens': 80, 'model_calls': 1}})
            result = report(root)
            self.assertEqual(result['cumulative_success'], 7)
            self.assertEqual(result['new_success'], 1)
            self.assertEqual(result['new_completed'], 1)
            self.assertEqual(result['new_calls_completed_generation'], 2)
            self.assertEqual(result['cumulative_tokens_completed_generation'], 4660)
            self.assertFalse(result['complete'])


if __name__ == '__main__':
    unittest.main()
