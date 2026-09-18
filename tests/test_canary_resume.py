import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from experiments.hard45_study.canary_resume import closed_canary
from experiments.hard45_study.setup import sha


class CanaryResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        root = self.root; p = root/'tests/OurMethod/A/generation'; p.mkdir(parents=True)
        (p/'result.json').write_text(json.dumps({'code_hash': 'code', 'budget': {'model_calls': 2, 'total_tokens': 10}}))
        (p.parent/'summary.json').write_text(json.dumps({'code_hash': 'code', 'new_model_calls': 2}))
        (root/'launch_canary_receipt.json').write_text(json.dumps({'task_id': 'A', 'returncode': 0,
            'unit': 'fixture.service', 'command': ['python', '--root', str(root), 'test', 'A']}))
        self.audit = {'status': 'pass', 'study_complete': False, 'canary_task_id': 'A', 'canary_calls': 2,
            'canary_tokens': 10, 'unstarted_task_ids': ['B'], 'files_sha256': {
                str(f.relative_to(root)): sha(f) for f in root.rglob('*') if f.is_file()}}
        self.save(); self.study = {'pending_task_ids': ['A', 'B']}
        self.process = subprocess.CompletedProcess([], 0, 'MainPID=0\nActiveState=inactive\nExecMainStatus=0\n', '')

    def save(self):
        (self.root/'canary_audit.json').write_text(json.dumps(self.audit))

    def tearDown(self): self.temp.cleanup()

    def test_closed_control_preserves_exact_unstarted_set(self):
        with patch('experiments.hard45_study.canary_resume.subprocess.run', return_value=self.process):
            self.assertEqual(closed_canary(self.root, self.study), {'B'})
            (self.root/'tests/OurMethod/B').mkdir()
            with self.assertRaisesRegex(ValueError, 'execution artifacts'): closed_canary(self.root, self.study)

    def test_live_control_cannot_be_treated_as_completed(self):
        live = subprocess.CompletedProcess([], 0, 'MainPID=123\nActiveState=active\nExecMainStatus=0\n', '')
        with patch('experiments.hard45_study.canary_resume.subprocess.run', return_value=live):
            with self.assertRaisesRegex(ValueError, 'authoritatively terminated'): closed_canary(self.root, self.study)

    def test_audited_cost_cannot_be_dropped(self):
        self.audit['canary_tokens'] = 0; self.save()
        with patch('experiments.hard45_study.canary_resume.subprocess.run', return_value=self.process):
            with self.assertRaisesRegex(ValueError, 'budget'): closed_canary(self.root, self.study)

    def test_changed_result_or_scheduler_launch_is_rejected(self):
        (self.root/'tests/OurMethod/A/generation/result.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'execution evidence changed'): closed_canary(self.root, self.study)
        (self.root/'state.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'scheduler launch'): closed_canary(self.root, self.study)


if __name__ == '__main__': unittest.main()
