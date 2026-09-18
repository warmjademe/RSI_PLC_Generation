import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash, object_hash
from experiments.deepseek_study.run import save
from our_method.rsi_protocol import make_protocol, assert_training_sources, groups, source_closure
from our_method.rsi_assets import gate_decision
from our_method.rsi_assets import VersionStore
from our_method.training import records_from_corpus
from baseline_common.binding import verify_adapter
from tests.test_comparison import sample_corpus
from our_method.rsi_evidence import read_outcome, verify_outcome, verified_practice


class RSIProtocolTests(unittest.TestCase):
    def protocol(self):
        examples=[{'id':f'TR_{i:03}','task_id':f'TR_{i:03}', 'metadata':{'split':'train',
            'contamination_group_id':str(i//2),'semantic_signature':str(i//3)}} for i in range(60)]
        return make_protocol(({},examples,[],[]),rounds=2,practice_per_round=6,development_tasks=6),examples

    def outcome(self, root, protocol, tid, version, *, tokens=100, status='pass', role='development'):
        path=root/version/tid
        code='FUNCTION_BLOCK P\nEND_FUNCTION_BLOCK'
        code_hash=content_hash(code)
        save(path/'binding.json',{'task_id':tid,'version':version,'role':role,
            'protocol_sha256':protocol['protocol_sha256'],'evaluation_binding_sha256':'fixed'})
        save(path/'generation/result.json',{'task_id':tid,'code':code,'code_hash':code_hash,
            'budget':{'input_tokens':tokens-10,'output_tokens':10,'candidates':1,'model_calls':1}})
        checks=[{'stage':s,'status':status if s=='runtime' else 'pass','code_hash':code_hash,
                 'evidence':{'executed':True}} for s in ['compile','runtime','formal']]
        save(path/'judge/judge.json',{'code_hash':code_hash,'checks':checks,'status':status,'success':status=='pass'})
        return read_outcome(path,role=role,version=version,protocol=protocol)

    def test_connected_groups_do_not_cross_partitions(self):
        p,examples=self.protocol()
        parts=[set(p['development_ids'])]+[set(c) for c in p['practice_cohorts']]
        for i,part in enumerate(parts):
            self.assertEqual(source_closure(examples,part),part)
            for other in parts[i+1:]:self.assertFalse(part&other)
        self.assertFalse(set(p['development_ids'])&set(p['asset_source_ids']))
        self.assertEqual(p,self.protocol()[0])

    def test_test_and_development_cannot_be_learned(self):
        p,_=self.protocol()
        for ids in [['TE_C01_C02_01'],p['development_ids'],[]]:
            with self.assertRaises(ProtocolError):assert_training_sources(ids,p)

    def test_promotion_uses_complete_paired_receipts_and_rejects_regression(self):
        p,_=self.protocol()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            old=[self.outcome(root,p,t,'v0') for t in p['development_ids']]
            new=[self.outcome(root,p,t,'v1',tokens=90) for t in p['development_ids']]
            self.assertTrue(gate_decision(old,new,p)['accepted'])
            with self.assertRaises(ProtocolError):gate_decision(old,new[:-1],p)
            bad=[self.outcome(root,p,t,'v2',tokens=50,status='fail' if i==0 else 'pass') for i,t in enumerate(p['development_ids'])]
            self.assertFalse(gate_decision(old,bad,p)['accepted'])
            forged=[dict(r) for r in new];forged[0]['tokens']=0
            with self.assertRaises(ProtocolError):gate_decision(old,forged,p)
            with self.assertRaises(ProtocolError):verified_practice(new[0],p)

    def test_changed_code_or_check_cannot_enter_assets(self):
        p,_=self.protocol()
        with tempfile.TemporaryDirectory() as tmp:
            row=self.outcome(Path(tmp),p,p['practice_cohorts'][0][0],'v0',role='practice')
            verified_practice(row,p)
            path=Path(row['directory'])/'judge/judge.json'
            doc=json.loads(path.read_text());doc['checks'][0]['evidence']['executed']=False;save(path,doc)
            with self.assertRaises(ProtocolError):verify_outcome(row,p)

    def test_version_promotion_rollback_and_anti_leak_views(self):
        corpus=sample_corpus();p=make_protocol(corpus,rounds=1,practice_per_round=1,development_tasks=1)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=VersionStore(root/'registry',p,corpus)
            records=[r for r in records_from_corpus(corpus) if r['task_id'] in p['asset_source_ids']]
            initial=store.create(records,{})
            store.publish(initial,parent=None)
            query={'id':p['practice_cohorts'][0][0],'metadata':{}}
            with self.assertRaises(ProtocolError):verify_adapter(store.root/'versions'/initial,'OurMethod',query)
            view=store.view(initial,p['practice_cohorts'][0],root/'view')
            verify_adapter(view,'OurMethod',query)
            candidate=store.create(records,{'program_representation':'code_with_contract_delta'},parent=initial,round_number=1)
            with self.assertRaises(ProtocolError):store.publish(candidate,parent=initial,gate={'accepted':True})
            old=[self.outcome(root,p,t,initial) for t in p['development_ids']]
            new=[self.outcome(root,p,t,candidate,tokens=80) for t in p['development_ids']]
            store.publish(candidate,parent=initial,gate=gate_decision(old,new,p))
            self.assertEqual(json.loads((store.root/'current.json').read_text())['version'],candidate)
            store.rollback(initial,reason='fixture rollback check')
            self.assertEqual(json.loads((store.root/'current.json').read_text())['version'],initial)
            changed=[dict(r) for r in records];changed[0]['code']='unverified code'
            with self.assertRaises((ProtocolError,KeyError)):store.create(changed,{},parent=initial,round_number=2)

    def test_unmetered_gate_cannot_claim_token_savings(self):
        p,_=self.protocol()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            old=[self.outcome(root,p,t,'v0') for t in p['development_ids']]
            new=[self.outcome(root,p,t,'v1',tokens=80) for t in p['development_ids']]
            path=Path(new[0]['directory'])/'generation/result.json'
            doc=json.loads(path.read_text());doc['budget']['estimated_charge_calls']=1;save(path,doc)
            new[0]=read_outcome(Path(new[0]['directory']),role='development',version='v1',protocol=p)
            self.assertFalse(gate_decision(old,new,p)['accepted'])

    def test_two_round_coordinator_consumes_promoted_parent_without_test_reads(self):
        from experiments.rsi_study.run import learn
        corpus=sample_corpus();p=make_protocol(corpus,rounds=2,practice_per_round=1,development_tasks=1)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            save(root/'protocol.json',p);save(root/'generation_config.json',{'method':{}})
            save(root/'study.json',{'workers':1,'candidate_policy':{'program_representation':'code_with_contract_delta'}})
            calls=[]
            def batch(r,protocol,*,role,version,ids,memory,settings,batch,workers):
                self.assertNotEqual(role,'test')
                calls.append((role,version))
                tokens=80 if settings.get('program_representation') else 100
                rows=[self.outcome(root/'runs'/role,protocol,t,version,tokens=tokens,role=role) for t in ids]
                for row in rows:
                    save(Path(row['directory'])/'generation/run_config.json',{'method_config':{'memory_root':str(memory)}})
                return rows
            with patch('experiments.rsi_study.run.load_prepared',return_value=corpus), \
                 patch('experiments.rsi_study.run.calibrate',return_value={'status':'pass'}), \
                 patch('experiments.rsi_study.run.run_batch',side_effect=batch), \
                 patch('experiments.rsi_study.run.curate',return_value=[]):
                result=learn(root)
            self.assertTrue(result['rounds'][0]['accepted'])
            self.assertEqual(result['rounds'][1]['parent'],result['rounds'][0]['candidate'])
            practice=[version for role,version in calls if role=='practice']
            self.assertEqual(practice[1],result['rounds'][0]['candidate'])
            self.assertFalse((root/'runs/test').exists())

    def test_transitive_context_is_included_in_record_sources(self):
        from our_method.rsi_protocol import record_sources
        record={'task_id':'TR_A','kind':'verified_program','learning_context_task_ids':['TR_B','TR_C']}
        self.assertEqual(record_sources(record),{'TR_A','TR_B','TR_C'})

    def test_skill_cannot_hide_inherited_context_sources(self):
        from our_method.skills import skill_record,verify_skill
        p,examples=self.protocol()
        ids=p['asset_source_ids'][:4]
        sources=[{'id':tid,'task_id':tid,'kind':'verified_program','target':'DVP48ES300R'} for tid in ids[:2]]
        inherited={'id':'skill:parent','kind':'verified_skill','target':'DVP48ES300R','evidence_task_ids':ids[2:]}
        sources.append(inherited)
        content={k:'bounded procedure' for k in ['trigger','preconditions','procedure','verification','boundary']}
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            save(directory/'proposal.json',content)
            save(directory/'input.json',{'source_records':{r['id']:object_hash(r) for r in sources},'protocol_sha256':p['protocol_sha256']})
            save(directory/'context/model/0001/response.json',{'fixture':'not real model evidence'})
            record=skill_record(content,sources,audit_directory=directory,protocol=p,parent='v0')
            self.assertEqual(record['evidence_task_ids'],sorted(ids))
            verify_skill(record,{r['id']:r for r in sources},p)
            record['evidence_task_ids']=ids[:2]
            with self.assertRaises(ProtocolError):verify_skill(record,{r['id']:r for r in sources},p)


if __name__=='__main__':unittest.main()
