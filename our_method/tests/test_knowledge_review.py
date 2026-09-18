import copy
import unittest

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash
from our_method.knowledge_candidates import candidate_record
from our_method.knowledge_review import (
    claim_index, remaining_budget, review_payload, reviewed_candidate, validate_review,
)


class KnowledgeReview(unittest.TestCase):
    def setUp(self):
        self.protocol = {'training_ids': ['TR_1', 'TR_2'],
                         'asset_source_ids': ['TR_1', 'TR_2'], 'protocol_sha256': 'fixture'}
        code = 'IF Reset THEN Count := 0; END_IF;'
        self.records = [{'id': tid, 'kind': 'verified_program', 'task_id': tid,
                         'target': 'fixture', 'code': code, 'candidate_sha256': content_hash(code),
                         'requirement': 'Reset takes priority.', 'interface': 'Reset : BOOL;'}
                        for tid in ['TR_1', 'TR_2']]
        self.evidence = [{'id': f'e{i}', 'source_id': tid, 'field': 'code', 'quote': code}
                         for i, tid in enumerate(['TR_1', 'TR_2'])]
        clause = {'statement': 'Reset clears count.', 'check': 'runtime', 'source_refs': ['e0', 'e1']}
        self.proposal = {'decision': 'propose', 'knowledge_need': 'Preserve reset priority.',
                         'representation': 'conditional_rule', 'selection_reason': 'Guarded operation.',
                         'preconditions': [copy.deepcopy(clause)], 'payload': {'when': 'Reset', 'then': 'Count := 0'},
                         'verification_obligations': [copy.deepcopy(clause)],
                         'boundaries': ['Only for reset-priority tasks.'], 'evidence': self.evidence}
        self.parent = candidate_record(self.proposal, self.records, self.protocol)
        self.review = {'decision': 'retain', 'reason': 'Recorded branches match the stated scope.',
                       'evidence': copy.deepcopy(self.evidence), 'proposal': None,
                       'findings': [{'claim_id': claim['id'], 'assessment': 'supported',
                                     'explanation': 'Both programs clear Count in the reset branch.',
                                     'source_refs': ['e0', 'e1'], 'resolution_refs': []}
                                    for claim in claim_index(self.proposal)]}

    def test_retention_has_no_verification_or_promotion_claim(self):
        before = copy.deepcopy(self.parent)
        result = reviewed_candidate(self.review, self.parent, self.records, self.protocol)
        self.assertEqual(result, before)
        for key in ('semantic_entailment_verified', 'transfer_verified', 'released_for_generation'):
            self.assertIs(result[key], False)
        self.assertEqual(self.parent, before)

    def test_full_source_exposes_reset_exception(self):
        payload = review_payload(self.parent, self.records, self.protocol)
        self.assertEqual(payload['programs']['TR_1']['documents']['code'], self.records[0]['code'])
        self.assertEqual(len(payload['claims']), 4)

    def test_omission_duplicate_and_single_program_review_fail(self):
        for mutation in ('omit', 'duplicate', 'single_program'):
            value = copy.deepcopy(self.review)
            if mutation == 'omit': value['findings'].pop()
            elif mutation == 'duplicate': value['findings'][1] = value['findings'][0]
            else: value['findings'][0]['source_refs'] = ['e0']
            with self.subTest(mutation=mutation), self.assertRaises(ProtocolError):
                validate_review(value, self.parent, self.records, self.protocol)

    def test_fabricated_quote_and_source_tampering_fail(self):
        value = copy.deepcopy(self.review)
        value['evidence'][0]['quote'] = 'IF Reset THEN Count := 99; END_IF;'
        with self.assertRaises(ProtocolError):
            validate_review(value, self.parent, self.records, self.protocol)
        self.records[0]['code'] += '\nCount := 9;'
        with self.assertRaises(ProtocolError):
            review_payload(self.parent, self.records, self.protocol)

    def test_test_source_and_parent_metadata_tampering_fail(self):
        for change in ('test', 'metadata'):
            records, parent = copy.deepcopy(self.records), copy.deepcopy(self.parent)
            if change == 'test': records[0]['task_id'] = 'TE_1'
            else: parent['transfer_verified'] = True
            with self.subTest(change=change), self.assertRaises(ProtocolError):
                review_payload(parent, records, self.protocol)

    def test_retain_with_exception_or_unresolved_claim_fails(self):
        for assessment in ('exception_missing', 'contradicted', 'unresolved'):
            value = copy.deepcopy(self.review)
            value['findings'][0]['assessment'] = assessment
            with self.subTest(assessment=assessment), self.assertRaises(ProtocolError):
                validate_review(value, self.parent, self.records, self.protocol)

    def revised(self):
        value = copy.deepcopy(self.review)
        value.update(decision='revise', proposal=copy.deepcopy(self.proposal))
        value['proposal']['payload']['then'] = 'Clear Count during reset before any normal increment.'
        value['findings'][0].update(assessment='exception_missing', resolution_refs=['/payload'])
        return value

    def test_revision_preserves_parent_and_provenance(self):
        value = self.revised()
        result = reviewed_candidate(value, self.parent, self.records, self.protocol)
        self.assertNotEqual(result['id'], self.parent['id'])
        self.assertEqual(result['source_records'], self.parent['source_records'])
        self.assertEqual(self.parent['proposal'], self.proposal)
        self.assertIs(result['semantic_entailment_verified'], False)

    def test_revision_requires_changed_clause_for_defect(self):
        for change in ('unchanged', 'title_only', 'no_ref', 'bad_ref', 'unchanged_ref', 'unresolved'):
            value = self.revised()
            if change in ('unchanged', 'title_only'):
                value['proposal'] = copy.deepcopy(self.proposal)
                if change == 'title_only': value['proposal']['knowledge_need'] = 'Changed title.'
            elif change == 'no_ref': value['findings'][0]['resolution_refs'] = []
            elif change == 'bad_ref': value['findings'][0]['resolution_refs'] = ['/payload/arbitrary']
            elif change == 'unchanged_ref': value['findings'][0]['resolution_refs'] = ['/boundaries/0']
            else: value['findings'][0]['assessment'] = 'unresolved'
            with self.subTest(change=change), self.assertRaises(ProtocolError):
                validate_review(value, self.parent, self.records, self.protocol)

    def test_abstention_may_be_partial_but_never_publishes(self):
        value = {'decision': 'abstain', 'reason': 'No sufficiently supported common mechanism.',
                 'evidence': [], 'findings': [], 'proposal': None}
        self.assertIsNone(reviewed_candidate(value, self.parent, self.records, self.protocol))

    def test_remaining_budget_does_not_reset_calls_tokens_or_active_time(self):
        budget = {'limits': {'max_model_calls': 3, 'max_total_tokens': 120000,
                             'max_wall_seconds': 3600, 'max_output_tokens': 16384},
                  'model_calls': 2, 'input_tokens': 30000, 'output_tokens': 20000,
                  'total_tokens': 50000, 'elapsed_seconds': 60.5,
                  'estimated_charge_calls': 0, 'candidates': 0, 'tool_calls': 0}
        result = remaining_budget(budget)
        self.assertEqual(result, {'max_model_calls': 1, 'max_total_tokens': 70000,
                                 'max_wall_seconds': 3539.5, 'max_output_tokens': 16384})
        self.assertEqual(budget['limits']['max_model_calls'], 3)
        for field, bad in [('estimated_charge_calls', 1), ('total_tokens', 60000),
                           ('model_calls', 4), ('elapsed_seconds', float('nan')), ('tool_calls', 1)]:
            changed = copy.deepcopy(budget); changed[field] = bad
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                remaining_budget(changed)
        budget['model_calls'] = 3
        self.assertEqual(remaining_budget(budget)['max_model_calls'], 0)


if __name__ == '__main__':
    unittest.main()
