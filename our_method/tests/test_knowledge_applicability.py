import copy
import unittest

from baseline_common.errors import ProtocolError
from our_method.knowledge_applicability import ANCHORED, LEGACY, content_terms, mode, screen, signature


class KnowledgeApplicability(unittest.TestCase):
    def setUp(self):
        self.proposal = {'knowledge_need': 'Safety-fault restart inhibition and cross-ready latch isolation.',
                         'preconditions': [
                             {'statement': 'A function block called once per PLC scan.'},
                             {'statement': 'Safety faults inhibit restarting.'},
                             {'statement': 'A cross-ready latch isolates the downstream subsystem.'}],
                         'payload': {'steps': ['Other code, arithmetic, bytes and date calculations.']},
                         'boundaries': ['Not for pure numeric helpers.']}

    def test_framing_words_do_not_qualify_a_numeric_function(self):
        task = 'FUNCTION_BLOCK with BOOL inputs, outputs observed once after each scan; compute the day of year.'
        self.assertFalse(screen(self.proposal, task)['eligible'])
        self.assertFalse(content_terms('function inputs outputs after once bool'))

    def test_mechanism_and_each_specific_prerequisite_need_public_support(self):
        task = 'A safety fault inhibits restart; the cross ready latch controls downstream isolation.'
        result = screen(self.proposal, task)
        self.assertTrue(result['eligible'])
        self.assertFalse(result['semantic_applicability_established'])
        self.assertFalse(screen(self.proposal, 'Fault handling with safe restart.')['eligible'])
        self.assertFalse(screen(self.proposal, 'CrossReady latch controls downstream isolation.')['eligible'])

    def test_template_and_negative_boundaries_cannot_add_match_anchors(self):
        learned = signature(self.proposal)
        for token in ('arithmetic', 'bytes', 'date', 'numeric'):
            self.assertNotIn(token, learned['anchors'])
        self.assertFalse(screen(self.proposal, 'Arithmetic bytes date numeric helper.')['eligible'])

    def test_generic_or_no_prerequisite_proposals_do_not_match(self):
        for value in [{'knowledge_need': 'Reusable PLC function block implementation.',
                       'preconditions': [{'statement': 'Function inputs and outputs.'}]},
                      {'knowledge_need': 'Safety fault inhibit.', 'preconditions': []}]:
            self.assertFalse(screen(value, 'Safety fault inhibit.')['eligible'])

    def test_signature_and_screen_are_read_only(self):
        before = copy.deepcopy(self.proposal)
        signature(self.proposal); screen(self.proposal, 'fault cross ready isolation')
        self.assertEqual(self.proposal, before)

    def test_mode_is_explicit_and_default_preserves_legacy(self):
        self.assertEqual(mode({}), LEGACY)
        self.assertEqual(mode({'knowledge_selection_mode': ANCHORED}), ANCHORED)
        with self.assertRaises(ProtocolError): mode({'knowledge_selection_mode': 'silently_relaxed'})


if __name__ == '__main__':
    unittest.main()
