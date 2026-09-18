import copy
import unittest

from experiments.historical_assets_study.diagnose_repair_alignment import assignment_changes
from our_method.typed_fragments import Parser, tokens


def tree(code):
    return Parser(tokens(code)).statements()


class RepairAlignmentTests(unittest.TestCase):
    def test_nested_assignment_differences_are_counted_without_mutation(self):
        old = tree('IF A THEN Q := TRUE; ELSIF B THEN Q := FALSE; ELSE T := A; END_IF;')
        new = tree('IF A THEN Q := FALSE; ELSIF B THEN Q := FALSE; ELSE T := B; END_IF;')
        copies = copy.deepcopy([old, new])
        self.assertEqual(len(assignment_changes(old, new)), 2)
        self.assertEqual([old, new], copies)
        self.assertEqual(assignment_changes(old, old), [])

    def test_guard_change_or_insertion_is_not_assignment_only(self):
        old = tree('IF A THEN Q := TRUE; ELSE Q := FALSE; END_IF;')
        for code in ['IF B THEN Q := TRUE; ELSE Q := FALSE; END_IF;',
                     'IF A THEN Q := TRUE; ELSIF B THEN Q := FALSE; ELSE Q := FALSE; END_IF;',
                     'IF A THEN Q := TRUE; T := A; ELSE Q := FALSE; END_IF;']:
            self.assertIsNone(assignment_changes(old, tree(code)))

    def test_reordering_or_changed_target_is_not_aligned_heuristically(self):
        old = tree('Q := A; T := B;')
        self.assertIsNone(assignment_changes(old, tree('T := B; Q := A;')))
        self.assertIsNone(assignment_changes(old, tree('R := A; T := B;')))


if __name__ == '__main__': unittest.main()
