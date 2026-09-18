import itertools
import operator
import unittest

from our_method.case_projection import parsed_case_program
from our_method.repair_assets import parsed_program
from our_method.typed_fragments import Unsupported, Parser, tokens, render


def program(body, state_type='INT'):
    return ('FUNCTION_BLOCK Demo\nVAR_INPUT flag:BOOL; END_VAR\n'
            'VAR_OUTPUT out:INT; END_VAR\nVAR state:' + state_type + '; END_VAR\n'
            + body + '\nEND_FUNCTION_BLOCK')


def run(nodes, state):
    state = dict(state)
    binary = {'=': operator.eq, '>=': operator.ge, '<=': operator.le,
              'AND': lambda a, b: a and b, 'OR': lambda a, b: a or b, '+': operator.add}

    def expr(node):
        if node[0] == 'var':
            return state[node[1]]
        if node[0] == 'literal':
            if node[1] in ('TRUE', 'FALSE'):
                return node[1] == 'TRUE'
            return int(node[1])
        if node[0] == 'unary':
            return {'-': operator.neg, '+': operator.pos, 'NOT': operator.not_}[node[1]](expr(node[2]))
        return binary[node[1]](expr(node[2]), expr(node[3]))

    def sequence(items):
        for node in items:
            if node[0] == 'assign':
                state[node[1]] = expr(node[2])
            else:
                for condition, body in node[1]:
                    if expr(condition):
                        sequence(body)
                        break
                else:
                    sequence(node[2])
    sequence(nodes)
    return state


class CaseProjectionTests(unittest.TestCase):
    def test_disjoint_labels_ranges_and_negative_boundaries(self):
        _, tree, receipt = parsed_case_program(program(
            'CASE state OF -3..-1, 2: out:=1; 4,6..8:out:=2; ELSE out:=3; END_CASE;'))
        for selector in range(-5, 11):
            expected = 1 if selector in (-3, -2, -1, 2) else 2 if selector in (4, 6, 7, 8) else 3
            self.assertEqual(run(tree, {'state': selector, 'out': 9})['out'], expected)
        self.assertEqual(receipt['case_sites'][0]['branch_intervals'][0], [[-3, -1], [2, 2]])
        self.assertEqual(Parser(tokens(render(tree))).statements(), tree)

    def test_selector_write_does_not_cause_fallthrough(self):
        _, tree, _ = parsed_case_program(program(
            'CASE state OF 0:state:=1;out:=10; 1:state:=2;out:=20; ELSE out:=30; END_CASE;'))
        result = run(tree, {'state': 0, 'out': -1})
        self.assertEqual((result['state'], result['out']), (1, 10))

    def test_no_else_and_empty_matching_branch_retain_output(self):
        _, tree, _ = parsed_case_program(program('CASE state OF 0:; 1:out:=3; END_CASE;'))
        for selector, initial in itertools.product((-1, 0, 1, 2), (0, 9)):
            self.assertEqual(run(tree, {'state': selector, 'out': initial})['out'], 3 if selector == 1 else initial)

    def test_nested_case_and_if_keep_scope_and_branch_order(self):
        _, tree, receipt = parsed_case_program(program(
            'IF flag THEN CASE state OF 0:state:=2;CASE state OF 2:out:=7; ELSE out:=8;END_CASE;'
            ' 1:IF flag THEN out:=9;ELSE out:=10;END_IF; ELSE out:=11;END_CASE;'
            ' ELSE out:=12;END_IF; out:=out+1;'))
        for flag, selector in itertools.product((False, True), (0, 1, 3)):
            expected = 13 if not flag else {0: 8, 1: 10, 3: 12}[selector]
            self.assertEqual(run(tree, {'state': selector, 'flag': flag, 'out': -1})['out'], expected)
        self.assertEqual(len(receipt['case_sites']), 2)

    def test_all_sint_values_follow_direct_case_selection(self):
        _, tree, _ = parsed_case_program(program(
            'CASE state OF -128..-1:out:=1; 0:out:=2; 1..127:out:=3; END_CASE;', 'SINT'))
        for value in range(-128, 128):
            expected = 1 if value < 0 else 2 if value == 0 else 3
            self.assertEqual(run(tree, {'state': value, 'out': 0})['out'], expected)

    def test_rejects_overlap_reversed_and_out_of_type_labels(self):
        for body in ('0..3:out:=1;3:out:=2;', '1,1:out:=1;', '3..1:out:=1;', '-129:out:=1;', '128:out:=1;'):
            with self.subTest(body=body), self.assertRaises(Unsupported):
                parsed_case_program(program('CASE state OF '+body+'END_CASE;', 'SINT'))

    def test_rejects_calls_expressions_named_labels_and_noninteger_selector(self):
        for code in (program('CASE state+1 OF 0:out:=1;END_CASE;'),
                     program('CASE ABS(state) OF 0:out:=1;END_CASE;'),
                     program('CASE state OF READY:out:=1;END_CASE;'),
                     program('CASE state OF 0:out:=1;END_CASE;', 'REAL'),
                     program('CASE missing OF 0:out:=1;END_CASE;'),
                     program('CASE state OF 0:out:=ABS(state);END_CASE;')):
            with self.subTest(code=code), self.assertRaises(Unsupported):
                parsed_case_program(code)

    def test_rejects_unclosed_case_extra_label_after_else_and_input_write(self):
        for body in ('CASE state OF 0:out:=1;', 'CASE state OF ELSE out:=1;END_CASE;',
                     'CASE state OF 0:out:=1;ELSE out:=2;1:out:=3;END_CASE;',
                     'CASE state OF 0:flag:=FALSE;END_CASE;'):
            with self.subTest(body=body), self.assertRaises(Unsupported):
                parsed_case_program(program(body))

    def test_existing_if_only_tree_and_declarations_are_unchanged(self):
        code = program('IF flag THEN state:=1;ELSIF out=0 THEN state:=2;ELSE state:=3;END_IF;out:=state;')
        old_fields, old_tree = parsed_program(code)
        fields, tree, receipt = parsed_case_program(code)
        self.assertEqual((fields, tree), (old_fields, old_tree))
        self.assertEqual(receipt['case_sites'], [])


if __name__ == '__main__':
    unittest.main()
