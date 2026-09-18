"""Synthetic protocol checks for bounded format recovery, not PLC correctness."""
import json
import unittest

from baseline_common.utils import content_hash
from our_method.guarded_workflow import run
from tests import test_guarded_repair as fixtures
from tests.test_comparison import public_task


class ResponseFormatFallback(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GuardedRepairTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def context(self, responses, enabled=True, feedback=None):
        ctx, config = self.fixture.context(responses)
        config['response_representation'] = 'bound_exact_edits'
        if enabled is not None:
            config['complete_code_after_invalid_edits'] = enabled
        ctx.resume_previous_code = 'BROKEN'
        ctx.resume_feedback = feedback or [{'stage': 'compile', 'status': 'fail', 'diagnostics': ['syntax']}]
        return ctx, config

    def bad_patch(self):
        return {'base_code_sha256': content_hash('BROKEN'), 'edits': [{'old': 'ABSENT', 'new': 'FIXED'}]}

    def full_code(self, digest=None):
        return {'base_code_sha256': digest or content_hash('BROKEN'), 'code': 'FIXED'}

    def request(self, ctx, n):
        return json.loads((ctx.output/f'model/{n:04d}/request.json').read_text())

    def events(self, ctx):
        return [json.loads(line) for line in (ctx.output/'events.jsonl').read_text().splitlines()]

    def test_invalid_patch_switches_format_without_extra_calls_or_asset_changes(self):
        ctx, config = self.context([self.bad_patch(), self.full_code()])
        assets = (self.fixture.asset/'records.jsonl.gz').read_bytes()
        result = run(public_task(), ctx, config)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['code'], 'FIXED')
        self.assertEqual(result['budget']['model_calls'], 2)
        self.assertEqual(result['budget']['candidates'], 2)
        self.assertEqual(result['budget']['tool_calls'], 3)
        req = self.request(ctx, 2)
        self.assertEqual(req['payload']['previous_code'], 'BROKEN')
        self.assertEqual(req['payload']['base_code_sha256'], content_hash('BROKEN'))
        self.assertEqual(req['payload']['required_response_representation'], 'complete_code_with_base_hash')
        self.assertIn('Do not return edits.', req['system'])
        self.assertEqual((self.fixture.asset/'records.jsonl.gz').read_bytes(), assets)
        transitions = [e for e in self.events(ctx) if e['event'] == 'response_format_fallback']
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0]['extra_model_calls'], 0)
        self.assertFalse(transitions[0]['budget_reset'])

    def test_default_preserves_existing_edit_protocol(self):
        ctx, config = self.context([self.bad_patch(), {'base_code_sha256': content_hash('BROKEN'),
            'edits': [{'old': 'BROKEN', 'new': 'FIXED'}]}], enabled=None)
        result = run(public_task(), ctx, config)
        self.assertEqual(result['status'], 'passed')
        self.assertNotIn('required_response_representation', self.request(ctx, 2)['payload'])
        self.assertFalse(any(e['event'] == 'response_format_fallback' for e in self.events(ctx)))

    def test_more_edit_responses_are_rejected_and_consume_the_original_limit(self):
        ctx, config = self.context([self.bad_patch()] * 5)
        result = run(public_task(), ctx, config)
        self.assertNotEqual(result['status'], 'passed')
        self.assertEqual(result['budget']['model_calls'], 5)
        self.assertEqual(result['budget']['candidates'], 5)
        self.assertEqual(result['budget']['tool_calls'], 0)
        self.assertEqual(result['code'], 'BROKEN')
        self.assertTrue(all('required_response_representation' in self.request(ctx, n)['payload'] for n in range(2, 6)))

    def test_full_replacement_still_requires_the_exact_base_hash(self):
        ctx, config = self.context([self.bad_patch(), self.full_code('0'*64), self.full_code()])
        result = run(public_task(), ctx, config)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['budget']['model_calls'], 3)
        self.assertEqual(result['budget']['tool_calls'], 3)
        self.assertEqual(self.request(ctx, 3)['payload']['previous_code'], 'BROKEN')

    def test_formal_unknown_is_not_relabelled_as_behavior_failure(self):
        feedback = [{'stage': stage, 'status': 'pass', 'diagnostics': []} for stage in ('compile', 'runtime')]
        feedback.append({'stage': 'formal', 'status': 'unknown', 'diagnostics': ['backend incomplete']})
        ctx, config = self.context([self.bad_patch(), self.full_code()], feedback=feedback)
        result = run(public_task(), ctx, config)
        self.assertEqual(result['status'], 'passed')
        req = self.request(ctx, 2)
        self.assertEqual(req['payload']['verification_uncertainty'][0]['status'], 'unknown')
        self.assertFalse(any(c['stage'] == 'formal' for c in req['payload']['confirmed_errors']))
        self.assertIn('does not establish a PLC behavioral error', req['system'])
        self.assertEqual(result['budget']['tool_calls'], 3)

    def test_complete_code_without_prior_edit_rejection_keeps_original_prompt(self):
        ctx, config = self.context([self.full_code()])
        result = run(public_task(), ctx, config)
        self.assertEqual(result['status'], 'passed')
        self.assertNotIn('required_response_representation', self.request(ctx, 1)['payload'])
        self.assertFalse(any(e['event'] == 'response_format_fallback' for e in self.events(ctx)))


if __name__ == '__main__':
    unittest.main()
