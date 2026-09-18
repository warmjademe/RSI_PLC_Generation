import copy
import unittest

from our_method.source_contract_links import link_requirements, context_texts, bounded_context


class SourceContractLinksTest(unittest.TestCase):
    def program(self):
        return {'task_id': 'TR_example', 'id': 'source-example', 'metadata': {'split': 'train', 'requirements': [
            {'id': 'R1', 'text': 'Normal Out requires request.', 'property': 'G(!Mode -> (Out=Request))'},
            {'id': 'R2', 'text': 'Test mode also requires permission.', 'property': 'G(Mode -> (Out=(Request AND Permit)))'},
            {'id': 'R3', 'text': 'OutExtra is a separate indicator.', 'property': 'G(OutExtra=Mode)'},
        ]}}

    def test_failed_property_keeps_obligation_missing_from_text_match(self):
        p = self.program(); before = copy.deepcopy(p)
        result = link_requirements(p, 'out', [{'requirement_ids': ['R2']}])
        self.assertEqual([r['requirement']['id'] for r in result['requirements']], ['R2', 'R1'])
        self.assertEqual(result['requirements'][0]['requirement'], p['metadata']['requirements'][1])
        self.assertEqual(p, before)
        self.assertFalse(result['universal_applicability_established'])

    def test_unknown_and_batch_requirement_ids_are_not_unique_cause(self):
        r = link_requirements(self.program(), 'Out', [{'requirement_ids': ['R2', 'R3', 'Missing']}])
        self.assertEqual(r['unresolved_source_requirement_ids'], ['Missing'])
        self.assertEqual(r['requirements'][-1]['link_reasons'], ['id_listed_in_source_failure'])
        self.assertTrue(all(not x['unique_failed_requirement_established'] for x in r['requirements']))

    def test_context_follows_selected_witness_and_keeps_exact_source_text(self):
        p = self.program()
        links = link_requirements(p, 'Out', [{'requirement_ids': ['R1', 'R2']}])
        self.assertEqual(context_texts(links, [{'requirement_ids': ['R2']}]),
                         [p['metadata']['requirements'][i]['text'] for i in [1, 0]])
        self.assertEqual(context_texts(links, [{'requirement_ids': ['R1']}])[0],
                         p['metadata']['requirements'][0]['text'])

    def test_rejects_test_sources_and_ambiguous_ids(self):
        p = self.program(); p['metadata']['split'] = 'test'
        with self.assertRaises(ValueError): link_requirements(p, 'Out', [])

    def test_named_obligations_are_not_cut_at_two_or_partially_truncated(self):
        p = self.program()
        p['metadata']['requirements'][2] = {'id': 'R3', 'text': 'Out must also satisfy reset.', 'property': 'G(Reset -> !Out)'}
        ws = [{'requirement_ids': ['R1', 'R2', 'R3']}]
        links = link_requirements(p, 'Out', ws)
        texts = bounded_context(links, ws)
        self.assertEqual(len(texts), 3)
        self.assertEqual(set(texts), {r['text'] for r in p['metadata']['requirements']})
        self.assertIsNone(bounded_context(links, ws, maximum_characters=20))
        p = self.program(); p['metadata']['requirements'][1]['id'] = 'R1'
        with self.assertRaises(ValueError): link_requirements(p, 'Out', [])


if __name__ == '__main__': unittest.main()
