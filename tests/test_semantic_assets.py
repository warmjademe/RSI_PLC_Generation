import copy
import itertools
import unittest

from our_method.semantic_assets import transition,features,select,learn
from tests.test_typed_fragments import source
from our_method.typed_fragments import extract


def fixture(body):
    p=source(body=body)
    return max(extract(p),key=lambda a:a['statement_count'])


def evaluate(expr,values):
    tag=expr[0]
    if tag=='entry':return values[expr[1]]
    if tag=='literal':return {'TRUE':True,'FALSE':False}.get(expr[1],expr[1])
    if tag=='store':return evaluate(expr[2],values)
    if tag=='ite':return evaluate(expr[2] if evaluate(expr[1],values) else expr[3],values)
    if tag=='unary' and expr[1]=='NOT':return not evaluate(expr[2],values)
    if tag=='binary':
        a,b=evaluate(expr[2],values),evaluate(expr[3],values)
        return {'AND':lambda:a and b,'OR':lambda:a or b,'XOR':lambda:a!=b}[expr[1]]()
    raise AssertionError(expr)


class SemanticAssetsTests(unittest.TestCase):
    def test_priority_and_old_latch_preserved_for_all_boolean_valuations(self):
        a=fixture('IF Reset THEN Latch := FALSE; ELSIF Start THEN Latch := TRUE; END_IF; Out := Latch;')
        binding={name:slot for slot,name in a['source_binding'].items()}
        t=transition(a)
        for reset,start,latch,out in itertools.product([False,True],repeat=4):
            incoming={binding[k]:v for k,v in dict(reset=reset,start=start,latch=latch,out=out).items()}
            expected=False if reset else True if start else latch
            self.assertEqual(evaluate(t['exit_values'][binding['out']],incoming),expected)
            self.assertEqual(evaluate(t['exit_values'][binding['latch']],incoming),expected)

    def test_update_order_reads_new_value_without_erasing_type_store(self):
        a=fixture('Latch := Start; Out := Latch AND NOT Reset;')
        binding={name:slot for slot,name in a['source_binding'].items()};t=transition(a)
        expression=t['exit_values'][binding['out']]
        self.assertIn("['store', 'BOOL'",str(expression))
        dependencies={(e['from'],e['to']) for e in t['relations']}
        self.assertIn((binding['start'],binding['out']),dependencies)
        self.assertNotIn((binding['latch'],binding['out']),dependencies)

    def test_branch_assignment_does_not_leak_into_elsif_guard(self):
        a=fixture('IF Reset THEN Latch := TRUE; ELSIF Latch THEN Out := Start; ELSE Out := FALSE; END_IF;')
        binding={name:slot for slot,name in a['source_binding'].items()};t=transition(a)
        for reset,start,latch,out in itertools.product([False,True],repeat=4):
            incoming={binding[k]:v for k,v in dict(reset=reset,start=start,latch=latch,out=out).items()}
            expected=out if reset else start if latch else False
            self.assertEqual(evaluate(t['exit_values'][binding['out']],incoming),expected)

    def test_copy_only_and_single_primitive_are_excluded(self):
        copies={'tree':[['assign','q',['var','a']]],'roles':[]}
        primitive={'tree':[['assign','q',['binary','AND',['var','a'],['unary','NOT',['var','b']]]]],'roles':[]}
        self.assertTrue(features(copies)[1]['copy_only'])
        # AND + NOT are interacting operators, not one primitive connective.
        self.assertEqual(features(primitive)[1]['operations'],2)
        simple={'tree':[['assign','q',['binary','AND',['var','a'],['var','b']]]],'roles':[]}
        selected,summary=select([copies,simple])
        self.assertEqual(selected,[])
        self.assertEqual(summary['excluded'],{'copy_only':1,'single_primitive_operation':1})

    def test_learning_is_deterministic_and_graph_edges_resolve(self):
        programs=[source(),source('TR_two','g2')]
        first,summary,graph=learn(programs,[])
        second,again,_=learn(copy.deepcopy(programs),[])
        self.assertEqual(first,second);self.assertEqual(summary,again)
        self.assertTrue(first);self.assertEqual(summary['learning_model_calls'],0)
        nodes={n['id'] for n in graph['nodes']}
        self.assertTrue(all(e['from'] in nodes and e['to'] in nodes for e in graph['edges']))
        self.assertFalse(summary['test_split_accessed'])

    def test_nontraining_input_still_rejected(self):
        from baseline_common.errors import ProtocolError
        with self.assertRaises(ProtocolError):learn([source('TE_test')],[])

    def test_compact_context_is_bounded_and_feedback_independent(self):
        import json
        from our_method.semantic_retrieval import retrieve
        from our_method.mechanism_retrieval import retrieve as old_retrieve
        assets,_,_=learn([source(),source('TR_two','g2')],[])
        task={'id':'TE_unseen','target':'IEC_PORTABLE_ST','requirement':'Reset clears latch. Start sets latch.',
              'interface':{'inputs':{'Reset':'BOOL','Start':'BOOL'},'outputs':{'Out':'BOOL'}}}
        config={'memory_characters':6000,'mechanism_top_k':2}
        context=retrieve(assets,task,[],config)
        self.assertTrue(context['items'])
        self.assertLessEqual(len(json.dumps(context,ensure_ascii=False)),6000)
        self.assertEqual(context,retrieve(assets,task,[{'diagnostics':['TEST_SECRET']}],config))
        self.assertEqual(retrieve(assets,{**task,'id':'TR_one'},[],config)['items'],[])
        self.assertEqual(retrieve(assets,{**task,'metadata':{'contamination_group_id':'g2'}},[],config)['items'],[])
        off={**config,'use_code_memory':False}
        self.assertEqual(retrieve(assets,task,[],off),old_retrieve(assets,task,[],off))


if __name__=='__main__':unittest.main()
