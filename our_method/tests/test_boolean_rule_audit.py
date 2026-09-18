import copy
import unittest

from experiments.rsi_study.boolean_rule_audit import mutation_witnesses, reconstruct_trace, require_runtime_traces


class RuleAuditTests(unittest.TestCase):
    def row(self, variable='Blocked', matched=True, observed=1):
        return {'case': 'case1', 'step': 0, 'assertion': 0, 'variable': variable,
                'operator': 'eq', 'type': 'BOOL', 'expected': True,
                'matched': matched, 'observed': observed}

    def test_unrelated_failure_is_not_mutation_evidence(self):
        same = self.row(matched=False, observed=0)
        self.assertEqual(mutation_witnesses([same], [copy.deepcopy(same)], 'Blocked'), [])

    def test_targeted_change_requires_same_authored_assertion(self):
        good = self.row(); bad = self.row(matched=False, observed=0)
        self.assertEqual(len(mutation_witnesses([good], [bad], 'Blocked')), 1)
        bad['expected'] = False
        with self.assertRaisesRegex(ValueError, 'authored assertion'):
            mutation_witnesses([good], [bad], 'Blocked')

    def test_mutation_must_not_change_other_outputs(self):
        with self.assertRaisesRegex(ValueError, 'different observed output'):
            mutation_witnesses([self.row('Ready')], [self.row('Ready', False, 0)], 'Blocked')

    def test_raw_stdout_reconstructs_bool_pass_and_fail(self):
        assertion = {k: v for k, v in self.row().items() if k not in ('matched', 'observed')}
        self.assertEqual(reconstruct_trace([assertion], '0\t1\n'), [self.row()])
        self.assertEqual(reconstruct_trace([assertion], '0\t0\n'), [self.row(matched=False, observed=0)])

    def test_missing_observation_cannot_pass(self):
        assertion = {k: v for k, v in self.row().items() if k not in ('matched', 'observed')}
        with self.assertRaises(ValueError):
            reconstruct_trace([assertion], '')

    def test_missing_runtime_trace_is_an_explicit_refusal_with_task_identity(self):
        for good, bad in [(None, None), (None, []), ([], None), ('trace', []), ([], {})]:
            with self.subTest(good=good, bad=bad):
                with self.assertRaisesRegex(ValueError, 'runtime traces missing for TR_EXAMPLE'):
                    require_runtime_traces(good, bad, 'TR_EXAMPLE')

    def test_available_empty_traces_do_not_invent_a_positive_negative_witness(self):
        require_runtime_traces([], [], 'TR_EXAMPLE')
        self.assertEqual(mutation_witnesses([], [], 'Blocked'), [])


if __name__ == '__main__':
    unittest.main()
