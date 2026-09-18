import copy
import unittest

from our_method.repair_edit_decomposition import decompose, reconstruct, events, typed_core
from our_method.typed_fragments import Parser, tokens


def tree(code):
    return Parser(tokens(code)).statements()


class RepairEditDecompositionTest(unittest.TestCase):
    def check_pair(self, before, after):
        before, after = tree(before), tree(after)
        original = copy.deepcopy((before, after))
        plan = decompose(before, after)
        self.assertEqual(reconstruct(plan, 'before'), before)
        self.assertEqual(reconstruct(plan, 'after'), after)
        self.assertEqual((before, after), original)
        return plan

    def test_order_duplicates_insert_and_delete_roundtrip(self):
        for a, b in [('a:=b; c:=d;', 'c:=d; a:=b;'),
                     ('a:=b; a:=b; c:=d;', 'a:=b; c:=d; a:=b;'),
                     ('', 'a:=b;'), ('a:=b;', ''), ('a:=b;', 'a:=b;')]:
            self.check_pair(a, b)

    def test_nested_guard_and_else_priority_retained(self):
        p = self.check_pair('IF a THEN x:=a; ELSIF b THEN IF c THEN x:=d; ELSE x:=e; END_IF; ELSE x:=f; END_IF;',
                            'IF a THEN x:=a; ELSIF b THEN IF c THEN x:=g; ELSE x:=e; END_IF; ELSE x:=f; END_IF;')
        es = events(p); self.assertEqual(len(es), 1)
        self.assertEqual(es[0]['before_controls'], [
            {'earlier_guards_false': [['var', 'a']], 'this_guard_true': ['var', 'b']},
            {'earlier_guards_false': [], 'this_guard_true': ['var', 'c']}])
        self.assertTrue(es[0]['whole_source_transition_required'])
        self.assertFalse(es[0]['causal_attribution_established'])

    def test_branch_change_and_different_branch_counts(self):
        p = self.check_pair('IF a THEN x:=TRUE; ELSE x:=FALSE; END_IF;',
                            'IF b THEN x:=FALSE; ELSE x:=TRUE; END_IF;')
        self.assertEqual([e['kind'] for e in events(p)], ['branch_guard', 'assignment_rhs', 'assignment_rhs'])
        p = self.check_pair('IF a THEN x:=TRUE; END_IF;',
                            'IF a THEN x:=TRUE; ELSIF b THEN x:=FALSE; END_IF;')
        self.assertEqual([e['kind'] for e in events(p)], ['statement_window'])

    def test_typed_core_preserves_alias_literal_type_and_initial(self):
        def core(before, after, names, typ='BOOL', initial='FALSE'):
            e = events(self.check_pair(before, after))[0]
            return typed_core(e, [{'slot': n, 'type': typ, 'direction': 'VAR', 'initial': initial} for n in names])
        a = core('x:=a AND b;', 'x:=a;', ['x', 'a', 'b'])
        self.assertEqual(a, core('q:=c AND d;', 'q:=c;', ['q', 'c', 'd']))
        self.assertNotEqual(a, core('x:=a AND a;', 'x:=a;', ['x', 'a']))
        self.assertNotEqual(a, core('x:=a AND b;', 'x:=a;', ['x', 'a', 'b'], initial='TRUE'))
        self.assertNotEqual(core('x:=1;', 'x:=2;', ['x'], 'INT', '0'),
                            core('x:=1;', 'x:=3;', ['x'], 'INT', '0'))
        self.assertNotEqual(core('x:=1;', 'x:=2;', ['x'], 'INT', '0'),
                            core('x:=1;', 'x:=2;', ['x'], 'DINT', '0'))

    def test_event_copies_and_rejects_unknown_phase(self):
        p = self.check_pair('x:=a;', 'x:=b;')
        es = events(p); es[0]['before'][0][2][1] = 'changed'
        self.assertEqual(reconstruct(p, 'before'), tree('x:=a;'))
        with self.assertRaises(ValueError): reconstruct(p, 'current')


if __name__ == '__main__': unittest.main()
