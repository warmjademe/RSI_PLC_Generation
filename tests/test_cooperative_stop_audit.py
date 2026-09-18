import copy
import json
from pathlib import Path
import tempfile
import unittest

from experiments.hard45_study.completion_audit import stopped_before_dispatch


class CooperativeStopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        (self.root/'STOP').write_text(json.dumps({'reason': 'confirmed repeated reasoning-only truncation; drain'}))
        p = self.root/'tests/OurMethod/started'; p.mkdir(parents=True); (p/'summary.json').write_text('{}')
        self.state = {'phase': 'interrupted', 'exits': [{'task_id': 'started', 'returncode': 0},
                      {'task_id': 'deferred', 'status': 'stopped_before_dispatch'}]}
        self.study = {'pending_task_ids': ['started', 'deferred']}
        self.report = {'complete': False}

    def tearDown(self):
        self.temp.cleanup()

    def test_only_never_dispatched_tasks_can_be_deferred(self):
        self.assertEqual(stopped_before_dispatch(self.root, self.state, self.study, self.report), ['deferred'])
        (self.root/'tests/OurMethod/deferred').mkdir()
        with self.assertRaisesRegex(ValueError, 'execution artifacts'):
            stopped_before_dispatch(self.root, self.state, self.study, self.report)

    def test_inflight_or_failed_started_process_is_not_a_drained_stop(self):
        state = copy.deepcopy(self.state); state['exits'][0]['returncode'] = 1
        with self.assertRaisesRegex(ValueError, 'completed and drained'):
            stopped_before_dispatch(self.root, state, self.study, self.report)
        (self.root/'tests/OurMethod/started/summary.json').unlink()
        with self.assertRaisesRegex(ValueError, 'completed and drained'):
            stopped_before_dispatch(self.root, self.state, self.study, self.report)

    def test_missing_or_duplicate_exit_cannot_erase_a_task(self):
        for exits in [self.state['exits'][:1], self.state['exits']+self.state['exits'][:1]]:
            state = {**self.state, 'exits': exits}
            with self.assertRaisesRegex(ValueError, 'account for all'):
                stopped_before_dispatch(self.root, state, self.study, self.report)

    def test_live_scheduler_or_different_stop_reason_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'supported drained'):
            stopped_before_dispatch(self.root, {**self.state, 'phase': 'running'}, self.study, self.report)
        (self.root/'STOP').write_text(json.dumps({'reason': 'provider transport incident'}))
        with self.assertRaisesRegex(ValueError, 'supported drained'):
            stopped_before_dispatch(self.root, self.state, self.study, self.report)


if __name__ == '__main__':
    unittest.main()
