import copy
import unittest

from baseline_common.utils import object_hash
from our_method.diagnostic_knowledge_query import CURRENT_FEEDBACK
from our_method.knowledge_applicability import ANCHORED, LEGACY
from our_method.knowledge_candidates import candidate_record
from our_method.knowledge_retrieval import prepare_reference, retrieve
from our_method.tests import test_knowledge_retrieval as fixtures


class KnowledgePrerequisiteRetrieval(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.KnowledgeRetrieval(); self.fixture.setUp()
        self.task = self.fixture.task
        self.config = {**self.fixture.config, 'knowledge_query_mode': CURRENT_FEEDBACK,
                       'knowledge_selection_mode': ANCHORED}
        self.records = self.fixture.records+[self.fixture.reference]

    def test_supported_reset_priority_still_retrieves_complete_knowledge(self):
        result = retrieve(self.records, self.task, [], self.config)
        self.assertEqual(result['items'][0]['id'], self.fixture.reference['id'])
        self.assertEqual(result['items'][0]['knowledge']['payload'], self.fixture.proposal['payload'])
        self.assertEqual(result['selection_audit']['knowledge_selection_mode'], ANCHORED)

    def test_legacy_is_explicitly_unchanged(self):
        config = self.fixture.config
        self.assertEqual(retrieve(self.records, self.task, [], config),
                         retrieve(self.records, self.task, [], {**config, 'knowledge_selection_mode': LEGACY}))

    def test_generic_public_words_and_diagnostic_cannot_supply_missing_prerequisites(self):
        public = {**self.task, 'requirement': 'Function block with inputs and outputs once per scan; merge bytes.',
                  'interface': {'inputs': {'ByteLow': 'BYTE', 'ByteHigh': 'BYTE'}, 'outputs': {'Result': 'WORD'}}}
        feedback = [{'stage': 'formal', 'status': 'fail', 'diagnostics': ['Reset counter priority increment.']}]
        self.assertFalse(retrieve(self.records, public, feedback, self.config)['items'])

    def test_partial_mechanism_match_does_not_skip_a_different_precondition(self):
        proposal = copy.deepcopy(self.fixture.proposal)
        proposal['knowledge_need'] = 'Reset priority with safety restart inhibition.'
        proposal['preconditions'].append({'statement': 'Safety faults require restart inhibition.',
                                         'check': 'unresolved', 'source_refs': ['e1', 'e2']})
        reference = prepare_reference(candidate_record(proposal, self.fixture.records, self.fixture.protocol),
                                      self.fixture.bank, self.fixture.protocol)
        self.assertFalse(retrieve([reference], self.task, [], self.config)['items'])
        supported = {**self.task, 'requirement': self.task['requirement']+' Safety faults require restart inhibition.'}
        self.assertTrue(retrieve([reference], supported, [], self.config)['items'])

    def test_component_switches_and_query_leave_assets_unchanged(self):
        before = object_hash([self.records, self.task])
        feedback = [{'stage': 'runtime', 'status': 'unknown', 'diagnostics': ['Reset counter priority unresolved.']}]
        no_feedback = {**self.config, 'use_current_task_feedback': False}
        self.assertEqual(retrieve(self.records, self.task, feedback, no_feedback),
                         retrieve(self.records, self.task, [], no_feedback))
        no_assets = {**self.config, 'use_code_memory': False}
        self.assertEqual(retrieve(self.records, self.task, feedback, no_assets),
                         retrieve(self.records, self.task, [], {**no_assets, 'knowledge_selection_mode': LEGACY}))
        self.assertEqual(before, object_hash([self.records, self.task]))


if __name__ == '__main__':
    unittest.main()
