import copy
import hashlib
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from baseline_common.errors import ModelResponseError
from baseline_common.utils import content_hash
from our_method.bound_edits import apply_revision
from our_method.feedback import failure_query
from our_method.guarded_workflow import repairable_frontend, run
from experiments.hard45_study.feedback_projection import encode_steps, decode_steps, compact_steps, runtime_feedback, frontend_feedback, project_feedback
from experiments.hard45_study.prepare_next import cumulative_budget, project_receipt
from tests import test_guarded_repair as fixtures


class RevisionTests(unittest.TestCase):
    def test_edits_are_simultaneous_and_bound_to_one_version(self):
        base = 'A := 1;\nB := 2;\n'
        reply = {'base_code_sha256': content_hash(base), 'edits': [
            {'old': 'A := 1;', 'new': 'B := 2;'}, {'old': 'B := 2;', 'new': 'B := 3;'}]}
        code, audit = apply_revision(base, reply)
        self.assertEqual(code, 'B := 2;\nB := 3;\n')
        self.assertEqual(audit['edit_count'], 2)
        with self.assertRaises(ModelResponseError):
            apply_revision(base + ' ', reply)

    def test_ambiguous_and_overlapping_edits_are_rejected(self):
        for base, edits in [('aaa', [{'old': 'aa', 'new': 'b'}]),
                            ('abcdef', [{'old': 'abc', 'new': 'x'}, {'old': 'cde', 'new': 'y'}]),
                            ('abc', [{'old': '', 'new': 'x'}])]:
            with self.assertRaises(ModelResponseError):
                apply_revision(base, {'base_code_sha256': content_hash(base), 'edits': edits})

    def test_full_replacement_fallback_is_explicit_and_bound(self):
        base = 'ABC'; response = {'base_code_sha256': content_hash(base), 'code': 'NEW'}
        self.assertEqual(apply_revision(base, response)[0], 'NEW')
        with self.assertRaises(ModelResponseError):
            apply_revision(base, {**response, 'edits': [{'old': 'ABC', 'new': 'NEW'}]})

    def test_step_codec_preserves_presence_types_repetitions_and_call_order(self):
        rng = random.Random(72)
        steps = [{}, {'inputs': {}}, {'inputs': {'X': True}}, {'inputs': {'X': 1}},
                 {'inputs': {'X': 1.0}}, {'inputs': {'X': None}}, {'cycles': None}]
        for _ in range(300):
            if rng.random() < .3:
                steps.append(copy.deepcopy(steps[-1]))
            else:
                steps.append({'inputs': {k: rng.choice([False, True, -1, 0, 1, 1.0, None, 'T#1ns']) for k in ('X', 'Y') if rng.random() < .8},
                              'cycles': rng.choice([1, 20]), 'calls': ['A', 'B'] if rng.random() < .5 else ['B', 'A']})
        decoded = decode_steps(encode_steps(steps))
        self.assertEqual(json.dumps(decoded, sort_keys=True), json.dumps(steps, sort_keys=True))

    def test_runtime_prefix_is_complete_without_reference_or_success_assertions(self):
        steps = [{'inputs': {'Enable': False}, 'cycles': 1, 'expect': {'HIDDEN': 7}}]
        steps += [{'inputs': {'Enable': True}, 'cycles': 1, 'expect': {'HIDDEN': 8}}] * 100
        raw = {'diagnostics': [{'case': 'demo', 'step': 100, 'variable': 'Run', 'expected': True, 'observed': False}],
               'evidence': {'reference_code': 'PRIVATE_REFERENCE'}}
        effective = {'plan': {'cases': [{'id': 'demo', 'steps': steps}], 'scan_period_ms': 10}, 'reference_code': 'PRIVATE_REFERENCE'}
        before = copy.deepcopy((raw, effective))
        result = runtime_feedback(raw, effective)
        self.assertFalse(result[0]['earlier_inputs_omitted'])
        self.assertEqual(len(decode_steps(result[0]['input_steps'])), 101)
        self.assertEqual(result[0]['scan_period_ms'], 10)
        self.assertNotIn('PRIVATE_REFERENCE', json.dumps(result))
        self.assertNotIn('HIDDEN', json.dumps(result))
        self.assertEqual((raw, effective), before)
        query = failure_query([{'stage': 'runtime', 'status': 'fail', 'diagnostics': result}])
        self.assertIn('Run', query)

    def test_dictionary_history_keeps_long_nonconsecutive_revisits_within_budget(self):
        steps = [{'inputs': {'Slot': (i * 7) % 19}, 'cycles': 1,
                  'expect': {'PRIVATE_PASS_VALUE': 1000+i}} for i in range(320)]
        raw = {'diagnostics': [{'case': 'revisits', 'step': 319, 'variable': 'Busy',
                               'expected': True, 'observed': False}]}
        actual = runtime_feedback(raw, {'plan': {'cases': [{'id': 'revisits', 'steps': steps}]}}, maximum=4000)
        self.assertFalse(actual[0]['earlier_inputs_omitted'])
        self.assertEqual(actual[0]['input_steps']['format'], 'step_dictionary_v1')
        self.assertEqual(decode_steps(actual[0]['input_steps']),
                         [{k:v for k,v in step.items() if k!='expect'} for step in steps])
        self.assertLessEqual(len(json.dumps(actual, ensure_ascii=False)), 4000)
        self.assertNotIn('PRIVATE_PASS_VALUE', json.dumps(actual))

    def test_dictionary_roundtrip_preserves_types_absence_and_independent_objects(self):
        cycle = [{}, {'inputs': {}}, {'inputs': {'X': True}}, {'inputs': {'X': 1}},
                 {'inputs': {'X': 1.0}}, {'inputs': {'X': None}},
                 {'calls': ['A','B']}, {'calls': ['B','A']}]
        steps = cycle * 40
        encoded, _ = compact_steps(steps)
        self.assertIsInstance(encoded, dict)
        decoded = decode_steps(encoded)
        self.assertEqual(json.dumps(decoded, sort_keys=True), json.dumps(steps, sort_keys=True))
        decoded[2]['inputs']['X'] = 'changed'
        self.assertIs(decoded[10]['inputs']['X'], True)
        for bad in [True, -1, 100, '0']:
            with self.assertRaises(ValueError):
                decode_steps({'format':'step_dictionary_v1','steps':[{}],'order':[bad]})

    def test_oversized_state_prefix_is_bounded_and_never_claimed_complete(self):
        steps = [{'inputs': {'X': i, 'Text': str(i) * 150}, 'cycles': 1} for i in range(100)]
        result = runtime_feedback({'diagnostics': [{'case': 'large', 'step': 99, 'variable': 'X', 'expected': 0, 'observed': 1}]},
                                  {'plan': {'cases': [{'id': 'large', 'steps': steps}]}}, maximum=2000)
        self.assertTrue(result[0]['earlier_inputs_omitted'])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 2000)
        self.assertEqual(result[0]['assertions'][0]['expected'], 0)

    def test_frontend_parser_diagnostic_keeps_unknown_and_excludes_cli_arguments(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); path = root / 'formal/job'; path.mkdir(parents=True)
            text = ('Arguments: private-oracle-property\n'
                    'Error: The file candidate.st contains the following errors:\n'
                    "- unexpected token (2:4) [Control-flow declaration generation]\n"
                    'Error: Unable to generate the CFA due to errors in parsing the source file. [Control-flow declaration generation]\n')
            (path / 'stdout.txt').write_text(text)
            raw = {'status': 'unknown', 'code_hash': 'a' * 64, 'evidence': {'property_results': [
                {'attempts': [{'artifact_directory': 'formal/job'}]}]}}
            before = copy.deepcopy(raw)
            result = frontend_feedback(raw, root)
            self.assertTrue(result[0]['repairable_input'])
            self.assertEqual(result[0]['status'], 'unknown')
            self.assertNotIn('private-oracle-property', json.dumps(result))
            self.assertEqual(result[0]['sources'][0]['sha256'], hashlib.sha256(text.encode()).hexdigest())
            self.assertTrue(repairable_frontend({'status': 'unknown', 'diagnostics': result}))
            self.assertEqual(raw, before)
            (path / 'stdout.txt').write_text('Solver timeout; no conclusive result.')
            self.assertEqual(frontend_feedback(raw, root), [])
            raw['evidence']['property_results'][0]['attempts'][0]['artifact_directory'] = '../outside'
            self.assertEqual(frontend_feedback(raw, root), [])

    def test_projection_rejects_other_candidate_receipt(self):
        with self.assertRaises(ValueError):
            project_feedback({'code_hash': 'a'}, {'code_hash': 'b'}, '.')

    def test_cumulative_cost_does_not_reset_or_relabel_round_limits(self):
        old = {'model_calls': 5, 'total_tokens': 100, 'candidates': 5, 'limits': {'max_candidates': 5}}
        new = {'model_calls': 3, 'total_tokens': 40, 'candidates': 3, 'limits': {'max_candidates': 5}}
        combined = cumulative_budget(old, new)
        self.assertEqual((combined['model_calls'], combined['candidates'], combined['total_tokens']), (8, 8, 140))
        self.assertNotIn('limits', combined)
        self.assertEqual(cumulative_budget(combined, new)['model_calls'], 11)

    def test_seed_reprojection_is_bound_and_does_not_modify_the_original_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); workspace = root / 'workspace'; workspace.mkdir()
            original = {'stage': 'runtime', 'status': 'fail', 'code_hash': 'x', 'diagnostics': ['old projection']}
            receipt = root / 'receipt.json'; receipt.write_text(json.dumps(original))
            raw = {**original, 'diagnostics': [{'case': 'case', 'step': 0, 'variable': 'Out', 'expected': 1, 'observed': 0}]}
            request = {'stage': 'runtime', 'code_hash': 'x', 'plan': {'cases': [{'id': 'case', 'steps': [{'inputs': {'In': 1}}]}]}}
            (workspace / 'authorized_check_result.json').write_text(json.dumps(raw))
            (workspace / 'authorized_check_request.json').write_text(json.dumps(request))
            projected, binding = project_receipt(receipt, 'x')
            self.assertEqual(projected['status'], 'fail')
            self.assertTrue(projected['derived_context_only'])
            self.assertEqual(len(binding), 3)
            self.assertEqual(json.loads(receipt.read_text()), original)
            (workspace / 'authorized_check_result.json').write_text(json.dumps({**raw, 'status': 'pass'}))
            with self.assertRaises(ValueError):
                project_receipt(receipt, 'x')


class RevisionWorkflowTests(unittest.TestCase):
    setUp = fixtures.GuardedRepairTests.setUp
    tearDown = fixtures.GuardedRepairTests.tearDown
    context = fixtures.GuardedRepairTests.context
    def test_bound_edit_reaches_all_real_context_receipts(self):
        base = 'BROKEN'
        ctx, config = self.context([{'base_code_sha256': content_hash(base), 'edits': [{'old': base, 'new': 'FIXED'}]}])
        config['response_representation'] = 'bound_exact_edits'
        ctx.resume_previous_code = base
        ctx.resume_feedback = [{'stage': 'compile', 'status': 'fail', 'diagnostics': ['bad syntax']}]
        from tests.test_comparison import public_task
        result = run(public_task(), ctx, config)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['code'], 'FIXED')
        self.assertEqual(result['budget']['tool_calls'], 3)

    def test_frontend_unknown_can_be_repaired_without_becoming_a_fake_fail(self):
        ctx, config = self.context([{'code': 'FIRST'}, {'code': 'SECOND'}])
        config['repair_frontend_errors'] = True
        original = ctx.check
        def check(stage, code):
            if stage == 'formal' and code == 'FIRST':
                return {'stage': stage, 'status': 'unknown', 'diagnostics': [
                    {'kind': 'verification_frontend_error', 'repairable_input': True, 'diagnostics': ['frontend rejection']}],
                    'evidence': {}, 'code_hash': content_hash(code)}
            return original(stage, code)
        with patch.object(ctx, 'check', side_effect=check):
            from tests.test_comparison import public_task
            result = run(public_task(), ctx, config)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['budget']['model_calls'], 2)
        request = json.loads((ctx.output / 'model/0002/request.json').read_text())['payload']
        self.assertEqual(request['verification_uncertainty'][0]['status'], 'unknown')
        self.assertEqual(request['confirmed_errors'], [])


if __name__ == '__main__':
    unittest.main()
