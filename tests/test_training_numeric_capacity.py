import unittest

from experiments.historical_assets_study.training_numeric_capacity import numeric_assignments
from our_method.repair_assets import parsed_program


class NumericInventoryTests(unittest.TestCase):
    def test_numeric_rhs_retains_prior_branch_guard_dependence(self):
        fields, nodes = parsed_program('''FUNCTION_BLOCK Fixture
VAR_INPUT Start:BOOL; X:INT; END_VAR
VAR_OUTPUT Y:INT; END_VAR
IF Start THEN Y:=1; ELSIF X>0 THEN Y:=X+1; ELSE Y:=X-1; END_IF;
END_FUNCTION_BLOCK''')
        rows = numeric_assignments(fields, nodes)
        self.assertEqual(len(rows), 3)
        for row in rows[1:]:
            self.assertEqual(row['enclosing_guard_BOOL_variables'], ['start'])
            self.assertEqual(row['direct_rhs_BOOL_variables'], [])
        self.assertEqual(rows[1]['arithmetic_operators'], ['+'])
        self.assertEqual(rows[2]['arithmetic_operators'], ['-'])

    def test_later_top_level_assignment_does_not_inherit_finished_if_guard(self):
        fields, nodes = parsed_program('''FUNCTION_BLOCK Fixture
VAR_INPUT Start:BOOL; X:INT; END_VAR
VAR_OUTPUT Y:INT; END_VAR
IF Start THEN Y:=1; END_IF; Y:=X*2;
END_FUNCTION_BLOCK''')
        rows = numeric_assignments(fields, nodes)
        self.assertEqual(rows[-1]['enclosing_guard_variables'], [])
        self.assertEqual(rows[-1]['arithmetic_operators'], ['*'])


if __name__ == '__main__': unittest.main()
