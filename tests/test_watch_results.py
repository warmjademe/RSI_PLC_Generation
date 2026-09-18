import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from experiments.historical_assets_study.watch_results import decode_files, collect
from tests.test_component_evidence import fixture


def payload():
    return {n: {'base64': base64.b64encode(b'{}').decode(), 'sha256': hashlib.sha256(b'{}').hexdigest()}
            for n in ('report.json', 'readiness.json', 'completion_audit.json')}


class WatchResultsTests(unittest.TestCase):
    def test_transferred_bytes_and_hashes_are_required(self):
        data = payload();self.assertEqual(decode_files(data)['report.json'], b'{}')
        data['report.json']['base64'] = base64.b64encode(b'changed').decode()
        with self.assertRaises(ValueError): decode_files(data)

    def test_unlisted_paths_and_missing_evidence_are_rejected(self):
        data = payload();data['../report.json'] = data['report.json']
        with self.assertRaises(ValueError): decode_files(data)
        data = payload();del data['completion_audit.json']
        with self.assertRaises(ValueError): decode_files(data)

    def test_existing_archive_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);(root/'v5').mkdir();(root/'v5'/'keep').write_text('original')
            with self.assertRaises(ValueError): collect({'label':'v5','files':payload()},root,root/'previous')
            self.assertEqual((root/'v5'/'keep').read_text(),'original')

    def test_complete_report_is_validated_and_original_bytes_are_preserved(self):
        documents = fixture()
        files = {}
        for name, document in zip(('report.json','readiness.json','completion_audit.json'), documents):
            content = json.dumps(document).encode()
            files[name] = {'base64':base64.b64encode(content).decode(),
                           'sha256':hashlib.sha256(content).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = collect({'label':'v5','root':'/remote/v5','files':files,
                              'raw_audit_bindings_rechecked':135},root,root/'absent')
            self.assertEqual(result['strata'][0]['n'],25)
            receipt = json.loads((root/'v5/collection_receipt.json').read_text())
            self.assertTrue(receipt['collection_complete'])
            for name, record in files.items():
                self.assertEqual((root/'v5'/name).read_bytes(),base64.b64decode(record['base64']))


if __name__ == '__main__': unittest.main()
