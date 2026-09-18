"""Evidence projection tests use synthetic tool records, never a PLC oracle."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from baseline_common.utils import content_hash
from experiments.hard45_study.feedback_projection import backend_timeout_evidence, project_feedback
from experiments.hard45_study.prepare_next import project_receipt
from our_method.guarded_workflow import refactorable_formal_timeout, run
from tests.test_comparison import public_task
from tests import test_formal_timeout_refactor as timeout_fixture


UNKNOWN = 'input_predicate portfolio did not complete a conclusive check.'
TIMEOUT = 'PLCverif/backend exceeded its allocated property time.'


def raw_result(reason=UNKNOWN):
    return {'stage': 'formal', 'status': 'unknown', 'code_hash': content_hash('TIMEOUT'),
            'diagnostics': [{'property_index': 1, 'status': 'unknown', 'reason': reason}],
            'evidence': {'property_results': [{'property_index': 1, 'status': 'unknown',
                'reason': reason, 'attempts': []}]}}


def checks(raw, diagnostics):
    return [{'stage': s, 'status': 'pass', 'diagnostics': [], 'code_hash': raw['code_hash']}
            for s in ('compile', 'runtime')] + [{**raw, 'diagnostics': diagnostics}]


class BackendTimeoutEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.raw = raw_result()
        self.effective = {'stage': 'formal', 'code_hash': self.raw['code_hash']}

    def tearDown(self):
        self.tmp.cleanup()

    def portfolio(self):
        directory = self.root/'formal_backend/property-0001/cbmc'
        report = directory/'reports/property_1.scanbound.portfolio.json'
        report.parent.mkdir(parents=True)
        source = report.parent/'property_1.scanbound.c'
        source.write_text('synthetic generated source; MUST NOT enter the model context')
        stage = {'stage': 'completion_12', 'status': 'unknown', 'executed': True,
                 'timed_out': True, 'allocated_seconds': 8, 'elapsed_seconds': 8.1,
                 'returncode': -9, 'pid': 123}
        receipt = report.with_suffix('')/'completion_12/result.json'
        receipt.parent.mkdir(parents=True);receipt.write_text(json.dumps(stage))
        data = {'schema_version': 2, 'status': 'unknown', 'portfolio_kind': 'input_predicate',
                'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'stages': [stage], 'artifacts_sha256': {
                    'completion_12/result.json': hashlib.sha256(receipt.read_bytes()).hexdigest()}}
        report.write_text(json.dumps(data))
        self.raw['evidence']['property_results'][0]['attempts'] = [
            {'backend': 'input_predicate_portfolio', 'status': 'unknown', 'executed': True,
             'reason': UNKNOWN, 'artifact_directory': str(directory.relative_to(self.root))}]
        return report, source, receipt, data

    def test_recorded_backend_timeout_preserves_other_unknown_reason(self):
        raw = raw_result('Missing, invalid or late input_predicate portfolio execution receipt.')
        raw['evidence']['property_results'][0]['attempts'] = [
            {'backend': 'nusmv', 'status': 'unknown', 'executed': True, 'reason': TIMEOUT,
             'timeout_seconds': 30, 'elapsed_seconds': 30.01}]
        old = copy.deepcopy(raw)
        result = project_feedback(raw, self.effective, self.root)
        self.assertEqual(result[0]['reason'], old['diagnostics'][0]['reason'])
        self.assertEqual(result[0]['status'], 'unknown')
        self.assertEqual(result[0]['backend_timeout_evidence']['sources'], [])
        self.assertTrue(refactorable_formal_timeout(checks(raw, result)))
        self.assertEqual(raw, old)
        raw['evidence']['property_results'][0]['attempts'][0]['executed'] = False
        self.assertEqual(backend_timeout_evidence(raw, self.root), {})

    def test_nested_completion_timeout_requires_manifest_and_receipt_bindings(self):
        report, source, receipt, data = self.portfolio()
        projected = project_feedback(self.raw, self.effective, self.root)
        evidence = projected[0]['backend_timeout_evidence']
        self.assertEqual(len(evidence['sources']), 3)
        self.assertTrue(refactorable_formal_timeout(checks(self.raw, projected)))
        self.assertNotIn('MUST NOT', json.dumps(projected))
        self.assertEqual(projected[0]['reason'], UNKNOWN)
        for field, value in [('source_sha256', 'wrong'), ('status', 'pass'), ('portfolio_kind', 'other')]:
            changed = copy.deepcopy(data);changed[field] = value;report.write_text(json.dumps(changed))
            self.assertEqual(backend_timeout_evidence(self.raw, self.root), {})
        report.write_text(json.dumps(data));receipt.write_text('{}')
        self.assertEqual(backend_timeout_evidence(self.raw, self.root), {})

    def test_manifest_cannot_label_a_different_record_as_the_same_stage(self):
        report, source, receipt, data = self.portfolio()
        changed = json.loads(receipt.read_text());changed['timed_out'] = False
        receipt.write_text(json.dumps(changed))
        data['artifacts_sha256']['completion_12/result.json'] = hashlib.sha256(receipt.read_bytes()).hexdigest()
        report.write_text(json.dumps(data))
        self.assertEqual(backend_timeout_evidence(self.raw, self.root), {})

    def test_path_escape_and_symlink_cannot_supply_timeout_evidence(self):
        report, source, receipt, data = self.portfolio()
        outside = self.root.parent/(self.root.name+'-outside.c');outside.write_text(source.read_text())
        try:
            data['source'] = str(outside);report.write_text(json.dumps(data))
            self.assertEqual(backend_timeout_evidence(self.raw, self.root), {})
            link = source.with_name('linked.c');link.symlink_to(outside)
            data['source'] = str(link);report.write_text(json.dumps(data))
            self.assertEqual(backend_timeout_evidence(self.raw, self.root), {})
        finally:
            outside.unlink()

    def test_seed_binding_covers_all_nested_evidence_files(self):
        workspace = self.root/'workspace';workspace.mkdir()
        # Build the same synthetic portfolio within a real receipt workspace.
        self.root = workspace
        report, source, receipt, data = self.portfolio()
        (workspace/'authorized_check_result.json').write_text(json.dumps(self.raw))
        (workspace/'authorized_check_request.json').write_text(json.dumps(self.effective))
        bound = workspace.parent/'receipt.json';bound.write_text(json.dumps(self.raw))
        projected, bindings = project_receipt(bound, self.raw['code_hash'])
        self.assertEqual(projected['status'], 'unknown')
        for path in (report, source, receipt):
            self.assertEqual(bindings[str(path)], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(json.loads(bound.read_text()), self.raw)

    def test_no_witness_never_becomes_timeout_and_context_stays_bounded(self):
        self.assertEqual(project_feedback(self.raw, self.effective, self.root), self.raw['diagnostics'])
        self.portfolio()
        projected = project_feedback(self.raw, self.effective, self.root, maximum=250)
        self.assertEqual(projected, self.raw['diagnostics'])
        self.assertLessEqual(len(json.dumps(projected)), 250)
        self.assertFalse(refactorable_formal_timeout(checks(self.raw, projected)))
        for value in (0, -1, True, float('nan'), float('inf')):
            raw = copy.deepcopy(self.raw)
            raw['evidence']['property_results'][0]['attempts'] = [
                {'backend': 'nusmv', 'status': 'unknown', 'executed': True, 'reason': TIMEOUT,
                 'timeout_seconds': value, 'elapsed_seconds': 1}]
            self.assertEqual(backend_timeout_evidence(raw, self.root), {})

    def test_one_timeout_does_not_mask_another_unknown_property_or_different_code(self):
        self.portfolio();projected = project_feedback(self.raw, self.effective, self.root)
        projected.append({'property_index': 2, 'status': 'unknown', 'reason': 'unsupported input'})
        self.assertFalse(refactorable_formal_timeout(checks(self.raw, projected)))
        projected.pop();projected[0]['backend_timeout_evidence']['code_hash'] = content_hash('OTHER')
        self.assertFalse(refactorable_formal_timeout(checks(self.raw, projected)))
        with self.assertRaises(ValueError):
            project_feedback(self.raw, {**self.effective, 'code_hash': content_hash('OTHER')}, self.root)

    def test_nested_evidence_guides_bounded_refactoring_with_original_unknown_receipts(self):
        self.portfolio();projected = project_feedback(self.raw, self.effective, self.root)
        harness = timeout_fixture.FormalTimeoutRefactor();harness.setUp()
        try:
            ctx, config = harness.context(['TIMEOUT', 'CORRECTED'], diagnostics=projected)
            result = run(public_task(), ctx, config)
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['budget']['model_calls'], 2)
            self.assertEqual(result['budget']['tool_calls'], 7)
            request = json.loads((ctx.output/'model/0002/request.json').read_text())
            self.assertEqual(request['payload']['confirmed_errors'], [])
            item = request['payload']['verification_uncertainty'][0]['diagnostics'][0]
            self.assertEqual(item['status'], 'unknown')
            self.assertEqual(item['reason'], UNKNOWN)
            self.assertEqual(item['backend_timeout_evidence']['sources'], projected[0]['backend_timeout_evidence']['sources'])
        finally:
            harness.tearDown()


if __name__ == '__main__':unittest.main()
