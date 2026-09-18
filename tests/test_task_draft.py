import copy
import json
from pathlib import Path
import tempfile
import unittest

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash
from experiments.hard45_study.task_draft import collect_draft
from our_method.task_draft import make_draft, draft_packet
from our_method.guarded_workflow import run
from tests.test_comparison import public_task
from tests import test_guarded_repair as fixture


class TaskDraftTests(unittest.TestCase):
    def setUp(self):
        self.task = public_task()
        self.draft = make_draft(self.task, 'OLD', 'Unverified local proposal.', {'response_sha256': 'fixture'})

    def test_draft_rejects_foreign_contract_or_changed_content(self):
        wrong = copy.deepcopy(self.draft); wrong['excerpt'] += 'changed'
        with self.assertRaises(ProtocolError): draft_packet(wrong, self.task, 'OLD')
        task = {**self.task, 'requirement': 'different requirement'}
        with self.assertRaises(ProtocolError): draft_packet(self.draft, task, 'OLD')
        self.assertIsNone(draft_packet(self.draft, self.task, 'NEW'))

    def test_long_draft_is_bounded_and_visibly_unverified(self):
        draft = make_draft(self.task, 'OLD', 'HEAD'+('x'*12000)+'TAIL', {})
        self.assertLessEqual(len(draft['excerpt']), 4800)
        self.assertTrue(draft['excerpt'].startswith('HEAD') and draft['excerpt'].endswith('TAIL'))
        self.assertTrue(draft['middle_omitted']); self.assertIs(draft['verified'], False)
        self.assertEqual(draft['draft_sha256'], content_hash('HEAD'+('x'*12000)+'TAIL'))

    def test_collector_uses_only_identical_task_and_code_and_preserves_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'study.json').write_text('{}')
            wire = root/'tests/OurMethod'/self.task['id']/'wire/00001'; wire.mkdir(parents=True)
            payload = {'task': {k: self.task[k] for k in ['id', 'requirement', 'target', 'interface']},
                       'previous_code': 'OLD', 'base_code_sha256': content_hash('OLD')}
            request = {'thinking': {'type': 'enabled'}, 'messages': [{'role': 'user', 'content': json.dumps(payload)}]}
            response = {'model': 'deepseek-flash', 'choices': [{'finish_reason': 'length',
                        'message': {'content': '', 'reasoning_content': 'unfinished fixture'}}]}
            (wire/'request_body.json').write_text(json.dumps(request)); (wire/'response.json').write_text(json.dumps(response))
            draft, bindings = collect_draft(root, self.task, 'OLD')
            self.assertEqual(draft['excerpt'], 'unfinished fixture')
            self.assertIn(str(wire/'response.json'), bindings)
            self.assertEqual(collect_draft(root, self.task, 'CHANGED'), (None, {}))
            payload['task']['requirement'] = 'different'; request['messages'][0]['content'] = json.dumps(payload)
            (wire/'request_body.json').write_text(json.dumps(request))
            self.assertEqual(collect_draft(root, self.task, 'OLD'), (None, {}))

    def test_workflow_removes_draft_after_program_change(self):
        f = fixture.GuardedRepairTests(); f.setUp()
        try:
            ctx, config = f.context([{'code': 'NEW'+str(i)} for i in range(5)], mode='fail')
            ctx.resume_previous_code = 'OLD'; ctx.resume_unverified_task_draft = self.draft
            config['use_task_draft'] = True
            result = run(self.task, ctx, config)
            first = json.loads((ctx.output/'model/0001/request.json').read_text())['payload']
            second = json.loads((ctx.output/'model/0002/request.json').read_text())['payload']
            self.assertEqual(first['unverified_task_draft'], self.draft)
            self.assertNotIn('unverified_task_draft', second)
            self.assertEqual(result['budget']['model_calls'], 5)
        finally:
            f.tearDown()


if __name__ == '__main__': unittest.main()
