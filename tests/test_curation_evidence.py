import copy
import json
from pathlib import Path
import tempfile
import unittest

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash, object_hash
from our_method.curation_evidence import select_repair_sources, validate_repair_source, repair_projections
from our_method.skills import FIELDS, skill_record, verify_skill


class CurationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.protocol = {'training_ids': ['TR_1', 'TR_2', 'TR_DEV'], 'asset_source_ids': ['TR_1', 'TR_2']}
        self.programs = [{'id': tid, 'task_id': tid, 'kind': 'verified_program', 'target': 'PLC',
                          'code': 'Count := 1;', 'candidate_sha256': content_hash('Count := 1;')}
                         for tid in ('TR_1', 'TR_2')]
        self.repair = {'id': 'repair:1', 'task_id': 'TR_1', 'target': 'PLC',
                       'kind': 'successful_trajectory_repair', 'before_code': 'Count := TRUE;',
                       'after_code': 'Count := 1;', 'trigger_stages': ['compiler'],
                       'observed_failure': [{'name': 'compiler', 'status': 'fail', 'summary': 'type mismatch'}],
                       'after_feedback': [{'name': n, 'status': 'pass'} for n in ('compiler', 'plcverif', 'openplc_feedback')],
                       'scope': 'synthetic protocol test; no real PLC validation', 'trajectory_terminal_success': True}

    def test_matching_endpoint_produces_bounded_evidence_without_changing_source(self):
        before = copy.deepcopy(self.repair)
        chosen = select_repair_sources(self.programs, [self.repair], self.protocol)
        self.assertEqual(chosen, [self.repair])
        projection = repair_projections(chosen)[0]
        self.assertEqual(projection['after_sha256'], self.programs[0]['candidate_sha256'])
        self.assertTrue(projection['change_excerpts'])
        self.assertEqual(before, self.repair)

    def test_development_test_and_transitive_sources_are_rejected(self):
        for source_id in ('TR_DEV', 'TE_ANY', 'v2-M-23'):
            repair = {**self.repair, 'learning_context_task_ids': [source_id]}
            with self.assertRaises(ProtocolError):
                validate_repair_source(repair, self.programs, self.protocol)
            self.assertEqual(select_repair_sources(self.programs, [repair], self.protocol), [])

    def test_wrong_endpoint_hash_and_missing_checks_cannot_support_a_proposal(self):
        for repair in ({**self.repair, 'after_code': 'Count := 2;'},
                       {**self.repair, 'after_feedback': [{'name': 'compiler', 'status': 'pass'}]}):
            with self.assertRaises(ProtocolError):
                validate_repair_source(repair, self.programs, self.protocol)
        programs = copy.deepcopy(self.programs); programs[0]['candidate_sha256'] = 'wrong'
        with self.assertRaises(ProtocolError):
            validate_repair_source(self.repair, programs, self.protocol)

    def test_one_repair_per_task_and_stable_selection(self):
        another = {**self.repair, 'id': 'repair:2'}
        a = select_repair_sources(self.programs, [another, self.repair], self.protocol)
        b = select_repair_sources(self.programs, [self.repair, another], self.protocol)
        self.assertEqual([r['id'] for r in a], ['repair:1'])
        self.assertEqual(a, b)

    def test_skill_source_binding_rechecks_the_repair_after_record_creation(self):
        protocol = {**self.protocol, 'protocol_sha256': object_hash(self.protocol)}
        sources = self.programs + [self.repair]
        content = {field: 'Synthetic test of source binding.' for field in FIELDS}
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            (directory / 'proposal.json').write_text(json.dumps(content))
            (directory / 'input.json').write_text(json.dumps({
                'source_records': {r['id']: object_hash(r) for r in sources},
                'protocol_sha256': protocol['protocol_sha256']}))
            model = directory / 'context/model/0001'; model.mkdir(parents=True)
            (model / 'response.json').write_text(json.dumps({'test_only': True, 'text': json.dumps(content)}))
            record = skill_record(content, sources, audit_directory=directory, protocol=protocol, parent='fixture-v1')
            available = {r['id']: r for r in sources}
            verify_skill(record, available, protocol)
            changed = copy.deepcopy(available)
            changed[self.repair['id']]['after_feedback'][0]['status'] = 'unknown'
            with self.assertRaises(ProtocolError):
                verify_skill(record, changed, protocol)


if __name__ == '__main__':
    unittest.main()
