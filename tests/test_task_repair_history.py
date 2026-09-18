import copy
import json
import unittest

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash
from our_method.repair_history import history_item, validate_history, history_packet, feedback_for_code
from our_method.guarded_workflow import run
from tests import test_guarded_repair as fixture
from tests.test_comparison import public_task


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.task = public_task()
        self.feedback = [{'stage': 'runtime', 'status': 'fail', 'code_hash': content_hash('OLD'),
                          'diagnostics': ['actual recorded mismatch']}]
        self.history = [history_item(self.task, 'OLD', self.feedback, origin='prior_round')]

    def test_task_contract_code_and_receipt_binding(self):
        validate_history(self.history, self.task)
        for field, value in [('task_id','ANOTHER'), ('code','CHANGED'), ('contract_sha256','wrong')]:
            h = copy.deepcopy(self.history); h[0][field] = value
            with self.assertRaises(ProtocolError): validate_history(h, self.task)
        h = copy.deepcopy(self.history); h[0]['feedback'][0]['code_hash'] = content_hash('OTHER')
        with self.assertRaises(ProtocolError): validate_history(h, self.task)

    def test_empty_response_round_preserves_exact_code_diagnostics_as_context(self):
        restored = feedback_for_code(self.history, self.task, 'OLD', [])
        self.assertEqual(restored[0]['status'], 'fail')
        self.assertEqual(restored[0]['diagnostics'], ['actual recorded mismatch'])
        self.assertTrue(restored[0]['derived_context_only'])
        self.assertTrue(restored[0]['restored_from_task_history'])
        self.assertNotIn('derived_context_only', self.history[0]['feedback'][0])

    def test_different_code_cannot_inherit_a_previous_failure(self):
        self.assertEqual(feedback_for_code(self.history, self.task, 'CHANGED', []), [])
        with self.assertRaises(ProtocolError):
            feedback_for_code(self.history, self.task, 'CHANGED', self.feedback)

    def test_actual_current_stage_supersedes_historical_context(self):
        current = [{**self.feedback[0], 'status': 'pass', 'diagnostics': []}]
        restored = feedback_for_code(self.history, self.task, 'OLD', current)
        self.assertEqual(restored, current)
        self.assertNotIn('restored_from_task_history', restored[0])

    def test_current_compile_does_not_erase_historical_runtime_failure(self):
        current = [{'stage': 'compile', 'status': 'pass', 'code_hash': content_hash('OLD'), 'diagnostics': []}]
        restored = feedback_for_code(self.history, self.task, 'OLD', current)
        self.assertEqual([(r['stage'], r['status']) for r in restored], [('compile', 'pass'), ('runtime', 'fail')])

    def test_packet_bounds_and_truthful_omission(self):
        h = []
        for i in range(9):
            code = str(i)
            feedback = [{**self.feedback[0], 'code_hash':content_hash(code), 'diagnostics':['large '*400]}]
            h.append(history_item(self.task, code, feedback, origin='prior_round'))
        packet = history_packet(h, self.task, characters=1700)
        self.assertLessEqual(len(json.dumps(packet, ensure_ascii=False)), 1700)
        self.assertEqual(packet['known_versions'], 9)
        self.assertEqual(packet['omitted_versions'], 9-len(packet['items']))
        self.assertTrue(packet['items'][0]['checks'][0]['diagnostic_excerpt']['truncated'])

    def test_cross_round_repeat_is_rejected_without_revalidating(self):
        f = fixture.GuardedRepairTests(); f.setUp()
        try:
            ctx, config = f.context([{'code':'OLD'}]*5)
            config['use_task_local_history'] = True
            ctx.resume_previous_code = 'LATEST'
            ctx.resume_task_history = self.history
            result = run(self.task, ctx, config)
            self.assertEqual(result['budget']['model_calls'], 5)
            self.assertEqual(result['budget']['tool_calls'], 0)
            packet = json.loads((ctx.output/'model/0001/request.json').read_text())['payload']['task_local_attempts']
            self.assertEqual(packet['known_versions'], 1)
        finally: f.tearDown()

    def test_new_failures_are_bound_and_visible_to_next_attempt(self):
        f = fixture.GuardedRepairTests(); f.setUp()
        try:
            ctx, config = f.context([{'code':'NEW'+str(i),'change_summary':'attempt '+str(i)} for i in range(5)], mode='fail')
            config['use_task_local_history'] = True
            result = run(self.task, ctx, config)
            self.assertEqual(result['budget']['model_calls'], 5)
            self.assertEqual(result['budget']['tool_calls'], 5)
            payload = json.loads((ctx.output/'model/0005/request.json').read_text())['payload']
            self.assertEqual(payload['task_local_attempts']['known_versions'], 4)
            self.assertEqual(payload['task_local_attempts']['items'][-1]['change_summary'], 'attempt 3')
        finally: f.tearDown()

    def test_pure_unknown_is_not_cached_as_a_confirmed_failure(self):
        f = fixture.GuardedRepairTests(); f.setUp()
        try:
            ctx, config = f.context([{'code':'OLD'}])
            config['use_task_local_history'] = True
            h = copy.deepcopy(self.history); h[0]['feedback'][0].update(status='unknown', diagnostics=['timeout'])
            ctx.resume_task_history = h
            result = run(self.task, ctx, config)
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['budget']['tool_calls'], 3)
        finally: f.tearDown()


if __name__ == '__main__': unittest.main()
