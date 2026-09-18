import copy
import unittest

from baseline_common.errors import ProtocolError
from baseline_common.utils import content_hash
from our_method.typed_fragments import Parser, tokens, render, extract, learn, instantiate, declarations, Unsupported
from our_method.mechanism_retrieval import retrieve


def source(tid='TR_one', group='g1', body='IF Reset THEN Latch := FALSE; ELSIF Start THEN Latch := TRUE; END_IF; Out := Latch;'):
    code='FUNCTION_BLOCK '+tid+'\nVAR_INPUT\nReset : BOOL; Start : BOOL; END_VAR\nVAR_OUTPUT\nOut : BOOL; END_VAR\nVAR\nLatch : BOOL; END_VAR\n'+body+'\nEND_FUNCTION_BLOCK'
    return {'id':tid,'task_id':tid,'target':'DVP48ES300R','code':code,'candidate_sha256':content_hash(code),'origin':'model_generated',
        'verification_scope':'unit-test fixture only', 'metadata':{'split':'train','contamination_group_id':group,
        'category_id':'C01','interface':{'inputs':[{'name':'Reset','type':'BOOL','description':'Reset latch'},
        {'name':'Start','type':'BOOL','description':'Start latch'}],'outputs':[{'name':'Out','type':'BOOL','description':'Latched output'}]},
        'requirements':[{'text':'Reset clears Out. Start sets latch.'}]}}


class TypedFragmentsTests(unittest.TestCase):
    def test_expression_precedence_keeps_relational_inside_equality(self):
        expression=Parser(tokens('ok = value < limit')).expr()
        self.assertEqual(expression,['binary','=', ['var','ok'],['binary','<',['var','value'],['var','limit']]])
        self.assertEqual(Parser(tokens('NOT x AND y')).expr(),
                         ['binary','AND',['unary','NOT',['var','x']],['var','y']])

    def test_order_and_reset_guard_survive_roundtrip(self):
        p=source(); fields, body=declarations(p['code']); tree=Parser(tokens(body)).statements()
        self.assertEqual(tree, Parser(tokens(render(tree))).statements())
        full=next(x for x in extract(p) if x['statement_count']==2)
        code=instantiate(full,full['source_binding'],fields)
        self.assertLess(code.index('reset'),code.index('start'))
        self.assertTrue(code.endswith('out := latch;'))

    def test_initial_values_and_scope_are_binding_guards(self):
        p=source(); f=extract(p)[0]; fields,_=declarations(p['code'])
        for key,value in [('initial','TRUE'),('direction','VAR_OUTPUT'),('type','INT')]:
            wrong=copy.deepcopy(fields); wrong['latch'][key]=value
            with self.assertRaises(ProtocolError): instantiate(f,f['source_binding'],wrong)

    def test_alias_and_injection_rejected(self):
        p=source(); f=extract(p)[0]; fields,_=declarations(p['code'])
        for bad in ({k:'latch' for k in f['source_binding']}, {**f['source_binding'],'p0':'x;END_IF;'}):
            with self.assertRaises(ProtocolError): instantiate(f,bad,fields)

    def test_no_false_group_independence(self):
        a=source(); b=source('TR_two','g1')
        self.assertEqual(learn([a,b],[])[0],[])
        b['metadata']['contamination_group_id']='g2'
        self.assertTrue(learn([a,b],[])[0])

    def test_deterministic_compositions_do_not_create_model_source_support(self):
        a=source();b=source('TR_two','g2');b['origin']='verified_cross_task_subsystems_then_deterministic_st_composition'
        self.assertEqual(learn([a,b],[])[0],[])
        c=source('TR_three','g3')
        assets,summary,_=learn([a,b,c],[])
        self.assertTrue(assets)
        self.assertEqual(summary['uncredited_source_programs'],1)
        for asset in assets:
            self.assertEqual(asset['source_group_count'],2)
            derived=next(e for e in asset['evidence'] if e['task_id']=='TR_two')
            self.assertFalse(derived['credited_model_generated_group'])
            original=next(e for e in asset['evidence'] if e['task_id']=='TR_one')
            self.assertIsNone(original['independent_of_parent_library'])

    def test_missing_origin_and_known_parent_context_are_not_credited(self):
        a=source();b=source('TR_two','g2');b.pop('origin')
        self.assertEqual(learn([a,b],[])[0],[])
        b['origin']='model_generated';b['learning_context_task_ids']=['TR_one']
        self.assertEqual(learn([a,b],[])[0],[])

    def test_graph_edges_have_nodes_and_keep_read_write_roles(self):
        assets,_,graph=learn([source(),source('TR_two','g2')],[])
        nodes={n['id'] for n in graph['nodes']}
        self.assertTrue(all(e['from'] in nodes and e['to'] in nodes for e in graph['edges']))
        self.assertTrue(any(e['relation']=='reads' for e in graph['edges']))
        self.assertTrue(any(e['relation']=='writes' for e in graph['edges']))

    def test_direct_all_history_build_never_runs_practice(self):
        from unittest.mock import patch
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import json
        from experiments.historical_assets_study.direct_all import build
        examples=[{'task_id':'TR_'+str(i)} for i in range(1000)]
        corpus=({'task_count':1000},examples,[],[])
        with TemporaryDirectory() as tmp, \
             patch('experiments.historical_assets_study.direct_all.load_prepared',return_value=corpus), \
             patch('experiments.historical_assets_study.direct_all.records_from_corpus',return_value=examples), \
             patch('experiments.historical_assets_study.direct_all.learn',return_value=([],{},{})) as learner, \
             patch('experiments.historical_assets_study.direct_all.freeze') as freezer, \
             patch('experiments.historical_assets_study.direct_all.seal_adapter'), \
             patch('experiments.historical_assets_study.learning.practice_one',side_effect=AssertionError('practice forbidden')):
            for e in examples:e['kind']='verified_program'
            done=build(Path(tmp),Path(tmp)/'training',Path(tmp)/'cancelled_pilot')
            self.assertEqual(len(learner.call_args.args[0]),1000)
            self.assertEqual(len(freezer.call_args.kwargs['records']),1000)
            self.assertEqual(done['model_calls'],0)
            self.assertFalse(done['additional_training_validation'])
            self.assertEqual(done['training_partition'],'all_1000_no_holdout')

    def test_nontraining_or_changed_source_rejected(self):
        for p in [source('TE_one'),{**source(),'candidate_sha256':'bad'}]:
            with self.assertRaises(ProtocolError): learn([p],[])

    def test_unsupported_calls_and_arrays_fail_closed(self):
        for body in ['Out := Timer();','Out[1] := TRUE;','FOR i := 0 TO 2 DO Out := TRUE; END_FOR;']:
            with self.assertRaises(Unsupported): extract(source(body=body))

    def test_comments_cannot_supply_an_operation(self):
        p=source(body='(* IF Reset THEN Out := TRUE; END_IF; *) Out := Start;')
        fragments=extract(p)
        self.assertTrue(all('reset' not in x['source_binding'].values() for x in fragments))

    def test_feedback_off_and_assets_off_boundaries(self):
        assets,_,_=learn([source(),source('TR_two','g2')],[])
        task={'id':'TE_case','target':'IEC_PORTABLE_ST','requirement':'Reset the latch; Start sets the latched output.',
              'interface':{'inputs':{'Reset':'BOOL','Start':'BOOL'},'outputs':{'Out':'BOOL'}}}
        config={'use_code_memory':True,'memory_characters':12000}
        visible=retrieve(assets,task,[],config)
        self.assertTrue(visible['items'])
        self.assertEqual(visible,retrieve(assets,task,[{'status':'fail','diagnostics':['SECRET_TEST_FEEDBACK']}],config))
        self.assertEqual(retrieve(assets,task,[],{**config,'use_code_memory':False})['items'],[])
        self.assertEqual(retrieve(assets,{**task,'id':'TR_one'},[],config)['items'],[])
        self.assertEqual(retrieve(assets,{**task,'metadata':{'contamination_group_id':'g2'}},[],config)['items'],[])


if __name__=='__main__': unittest.main()
